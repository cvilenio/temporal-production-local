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

	deploymentpb "go.temporal.io/api/deployment/v1"
	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/api/workflowservice/v1"
	temporalclient "go.temporal.io/sdk/client"
)

// ErrRateLimited signals the Cloud Worker-Deployment-Read API throttled us; the
// caller should keep the current replica count and try again next cycle (not a
// hard failure). This is the constraint that bounds backlog freshness.
var ErrRateLimited = errors.New("temporal worker-deployment read rate limited")

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
	if useTLS {
		opts.ConnectionOptions = temporalclient.ConnectionOptions{TLS: &tls.Config{}}
	}
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
