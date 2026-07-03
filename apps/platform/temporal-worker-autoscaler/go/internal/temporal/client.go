// Package temporal wraps the one Temporal Cloud call this controller makes:
// DescribeWorkerDeploymentVersion with task-queue stats — the same call the KEDA
// Temporal scaler used (proven correct for this repo's Worker-Deployment
// versioning), giving fresh per-version backlog. The SDK's high-level
// DescribeVersion returns task-queue NAMES only (no stats), so we use the raw
// WorkflowService gRPC via the SDK client's connection (auth/TLS handled by Dial).
package temporal

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	deploymentpb "go.temporal.io/api/deployment/v1"
	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/api/workflowservice/v1"
	temporalclient "go.temporal.io/sdk/client"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// ErrRateLimited signals the Cloud Worker-Deployment-Read API throttled us; the
// caller should keep the current replica count and try again next cycle (not a
// hard failure). This is the constraint that bounds backlog freshness.
var ErrRateLimited = errors.New("temporal worker-deployment read rate limited")

// ErrTransient signals a benign, self-healing transport hiccup — Temporal Cloud's
// edge recycles long-lived gRPC connections by max-connection-age (~5min), so a
// call in flight at the recycle moment surfaces EOF/Unavailable before gRPC
// transparently reconnects. It is NOT load- or demand-related; the next cycle
// succeeds. Like ErrRateLimited, the caller holds the current replica count and
// logs quietly instead of treating it as a hard error. (The high-level SDK hides
// this for workflows/activities, but we call the raw WorkflowService gRPC for
// backlog stats, so we own the classification — see the SDK's own WARN-and-retry
// on the worker's poll loop.)
var ErrTransient = errors.New("temporal connection recycled (transient)")

// isTransient reports whether err is a recoverable transport teardown (connection
// recycled/reset) rather than a real failure. gRPC auto-reconnects; we just skip
// this tick. Matches the typed SDK error, the retryable gRPC codes, and the raw
// io.EOF that a mid-flight read of a closing connection produces.
func isTransient(err error) bool {
	if err == nil {
		return false
	}
	var unavail *serviceerror.Unavailable
	if errors.As(err, &unavail) {
		return true
	}
	if errors.Is(err, io.EOF) {
		return true
	}
	switch status.Code(err) {
	case codes.Unavailable, codes.Canceled, codes.DeadlineExceeded:
		return true
	}
	// Fallback: the transport-teardown phrasing surfaces inconsistently across
	// grpc-go versions (sometimes codes.Unknown wrapping the raw message), so
	// match the observed strings directly. "error reading from server: EOF" is
	// the exact form seen against Temporal Cloud's edge on connection recycle.
	msg := err.Error()
	for _, s := range []string{"EOF", "connection reset", "connection closed", "broken pipe", "transport is closing"} {
		if strings.Contains(msg, s) {
			return true
		}
	}
	return false
}

// Client is the Temporal Cloud accessor for backlog reads.
type Client struct {
	sdk       temporalclient.Client
	namespace string
}

// Dial builds a lazy Temporal client (boots even if Cloud is briefly
// unreachable; connects on first call). API-key auth requires TLS.
func Dial(hostPort, namespace, apiKey string, useTLS bool) (*Client, error) {
	opts := temporalclient.Options{HostPort: hostPort, Namespace: namespace}
	if apiKey != "" {
		opts.Credentials = temporalclient.NewAPIKeyStaticCredentials(apiKey)
	}
	// Explicit gRPC keepalive: ping every 30s (SDK default) with pings permitted
	// even when no RPC is in flight, so an idle connection is detected and cycled
	// proactively rather than discovered dead mid-call. This trims stale-connection
	// EOFs; it does NOT eliminate them, because Cloud's edge also recycles by
	// max-connection-age (~5min) regardless of keepalive — those residual EOFs are
	// handled as ErrTransient by the caller.
	conn := temporalclient.ConnectionOptions{KeepAliveTime: 30 * time.Second, KeepAliveTimeout: 15 * time.Second}
	if useTLS {
		conn.TLS = &tls.Config{}
	}
	opts.ConnectionOptions = conn
	c, err := temporalclient.NewLazyClient(opts)
	if err != nil {
		return nil, err
	}
	return &Client{sdk: c, namespace: namespace}, nil
}

// Close releases the underlying client.
func (c *Client) Close() {
	if c.sdk != nil {
		c.sdk.Close()
	}
}

// VersionBacklog returns the approximate backlog for one task queue of one
// Worker Deployment version. deploymentName is the Temporal-side name (e.g.
// "orders/orders-workflow"); buildID is the version's Build ID.
func (c *Client) VersionBacklog(ctx context.Context, deploymentName, buildID, taskQueue, queueType string) (int64, error) {
	resp, err := c.sdk.WorkflowService().DescribeWorkerDeploymentVersion(ctx,
		&workflowservice.DescribeWorkerDeploymentVersionRequest{
			Namespace: c.namespace,
			DeploymentVersion: &deploymentpb.WorkerDeploymentVersion{
				DeploymentName: deploymentName,
				BuildId:        buildID,
			},
			ReportTaskQueueStats: true,
		})
	if err != nil {
		// A draining/just-registering version Temporal doesn't know yet → treat as
		// no backlog (it will settle to min; a version with pinned work reports
		// backlog and is found). Rate limits are surfaced as a soft, typed error.
		var nf *serviceerror.NotFound
		if errors.As(err, &nf) {
			return 0, nil
		}
		var rl *serviceerror.ResourceExhausted
		if errors.As(err, &rl) {
			return 0, ErrRateLimited
		}
		if isTransient(err) {
			return 0, ErrTransient
		}
		return 0, err
	}
	want := toEnumTQType(queueType)
	for _, tq := range resp.GetVersionTaskQueues() {
		if tq.GetName() == taskQueue && tq.GetType() == want {
			if s := tq.GetStats(); s != nil {
				return s.GetApproximateBacklogCount(), nil
			}
			return 0, nil // queue present, no stats yet
		}
	}
	// Version polls this deployment but not (yet) this queue/type -> no backlog.
	return 0, nil
}

func toEnumTQType(s string) enumspb.TaskQueueType {
	switch s {
	case "activity":
		return enumspb.TASK_QUEUE_TYPE_ACTIVITY
	case "nexus":
		return enumspb.TASK_QUEUE_TYPE_NEXUS
	default:
		return enumspb.TASK_QUEUE_TYPE_WORKFLOW
	}
}

// DefaultWorkerDeploymentName derives the Temporal deployment name from the k8s
// namespace + deployment name when the CR doesn't set it explicitly.
func DefaultWorkerDeploymentName(k8sNamespace, deploymentName string) string {
	return fmt.Sprintf("%s/%s", k8sNamespace, deploymentName)
}
