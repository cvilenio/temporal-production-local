// Package promsource reads worker slot-utilization hints from in-cluster Prometheus.
// Slot metrics are advisory: missing series return NaN so the scaler falls back to
// backlog-only (fail open).
package promsource

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const metric = "temporal_slot_utilization:by_build"

// Hints holds smoothed slot-utilization readings for one worker version.
type Hints struct {
	UpHint   float64 // max_over_time over the up window
	IdleHint float64 // avg_over_time over the down window
}

// Client queries Prometheus instant vectors.
type Client struct {
	baseURL string
	http    *http.Client
}

// New returns a Client for the Prometheus HTTP API (no trailing slash).
func New(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 5 * time.Second},
	}
}

// SlotHints returns slot utilization hints for one version. Missing or empty series
// yield NaN for that hint; transport/query errors return an error.
func (c *Client) SlotHints(
	ctx context.Context,
	namespace, taskQueue, buildID, workerType string,
	upWindow, downWindow time.Duration,
) (Hints, error) {
	if c == nil || c.baseURL == "" {
		return Hints{math.NaN(), math.NaN()}, nil
	}
	selector := LabelSelector(namespace, taskQueue, buildID, workerType)
	upQ := fmt.Sprintf(`max_over_time(%s{%s}[%s])`, metric, selector, promDuration(upWindow))
	downQ := fmt.Sprintf(`avg_over_time(%s{%s}[%s])`, metric, selector, promDuration(downWindow))

	up, err := c.instantScalar(ctx, upQ)
	if err != nil {
		return Hints{}, err
	}
	idle, err := c.instantScalar(ctx, downQ)
	if err != nil {
		return Hints{}, err
	}
	return Hints{UpHint: up, IdleHint: idle}, nil
}

// LabelSelector builds the PromQL label matcher for slot-utilization queries.
// namespace is the Temporal namespace label on SDK metrics (e.g. ziggymart.evvjb),
// NOT the k8s namespace of the WorkerAutoscaler CR.
func LabelSelector(namespace, taskQueue, buildID, workerType string) string {
	parts := []string{
		fmt.Sprintf(`namespace=%q`, namespace),
		fmt.Sprintf(`task_queue=%q`, taskQueue),
		fmt.Sprintf(`temporal_io_build_id=%q`, buildID),
		fmt.Sprintf(`worker_type=%q`, workerType),
	}
	return strings.Join(parts, ",")
}

func promDuration(d time.Duration) string {
	if d <= 0 {
		d = time.Minute
	}
	s := int(d.Seconds())
	if s < 1 {
		s = 1
	}
	return fmt.Sprintf("%ds", s)
}

type queryResponse struct {
	Status string `json:"status"`
	Data   struct {
		ResultType string `json:"resultType"`
		Result     []struct {
			Value []any `json:"value"`
		} `json:"result"`
	} `json:"data"`
	Error string `json:"error"`
}

func (c *Client) instantScalar(ctx context.Context, query string) (float64, error) {
	u, err := url.Parse(c.baseURL + "/api/v1/query")
	if err != nil {
		return math.NaN(), err
	}
	q := u.Query()
	q.Set("query", query)
	u.RawQuery = q.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return math.NaN(), err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return math.NaN(), err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return math.NaN(), err
	}
	if resp.StatusCode != http.StatusOK {
		return math.NaN(), fmt.Errorf("prometheus query %s: HTTP %d", query, resp.StatusCode)
	}

	var out queryResponse
	if err := json.Unmarshal(body, &out); err != nil {
		return math.NaN(), err
	}
	if out.Status != "success" {
		if out.Error != "" {
			return math.NaN(), fmt.Errorf("prometheus: %s", out.Error)
		}
		return math.NaN(), fmt.Errorf("prometheus query failed: %s", out.Status)
	}
	if len(out.Data.Result) == 0 {
		return math.NaN(), nil
	}
	if len(out.Data.Result) > 1 {
		// Multiple series for one build_id should not happen; take the max so
		// saturation is not under-read.
		var max float64 = math.NaN()
		for _, r := range out.Data.Result {
			v, err := parseSample(r.Value)
			if err != nil {
				continue
			}
			if math.IsNaN(max) || v > max {
				max = v
			}
		}
		return max, nil
	}
	return parseSample(out.Data.Result[0].Value)
}

func parseSample(value []any) (float64, error) {
	if len(value) < 2 {
		return math.NaN(), fmt.Errorf("prometheus sample missing value")
	}
	s, ok := value[1].(string)
	if !ok {
		return math.NaN(), fmt.Errorf("prometheus sample not a string")
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return math.NaN(), err
	}
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return math.NaN(), nil
	}
	return v, nil
}

// WorkerTypeForQueueType maps the CRD queueType to the SDK worker_type label on
// slot metrics. Values match Temporal Core SDK Prometheus labels (WorkflowWorker,
// ActivityWorker, NexusWorker).
func WorkerTypeForQueueType(queueType string) string {
	switch queueType {
	case "activity":
		return "ActivityWorker"
	case "nexus":
		return "NexusWorker"
	default:
		return "WorkflowWorker"
	}
}
