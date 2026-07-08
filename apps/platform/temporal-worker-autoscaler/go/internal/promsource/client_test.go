package promsource

import (
	"context"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestSlotHintsNaNOnEmpty(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"status":"success","data":{"resultType":"vector","result":[]}}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	h, err := c.SlotHints(context.Background(), "ziggymart.evvjb", "orders-workflow-task-queue", "build-1", "WorkflowWorker", time.Minute, 2*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if !math.IsNaN(h.UpHint) || !math.IsNaN(h.IdleHint) {
		t.Fatalf("want NaN hints on empty vector; got %+v", h)
	}
}

func TestSlotHintsReadsScalar(t *testing.T) {
	var gotUp, gotDown string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		switch {
		case strings.Contains(q, "max_over_time"):
			gotUp = q
			_, _ = w.Write([]byte(`{"status":"success","data":{"resultType":"vector","result":[{"value":[1,"0.9"]}]}}`))
		case strings.Contains(q, "avg_over_time"):
			gotDown = q
			_, _ = w.Write([]byte(`{"status":"success","data":{"resultType":"vector","result":[{"value":[1,"0.15"]}]}}`))
		default:
			t.Fatalf("unexpected query: %s", q)
		}
	}))
	defer srv.Close()

	c := New(srv.URL)
	h, err := c.SlotHints(context.Background(), "ziggymart.evvjb", "q", "b1", "WorkflowWorker", 60*time.Second, 120*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if h.UpHint != 0.9 || h.IdleHint != 0.15 {
		t.Fatalf("want 0.9/0.15; got %+v", h)
	}
	if gotUp == "" || gotDown == "" {
		t.Fatalf("expected both queries; up=%q down=%q", gotUp, gotDown)
	}
	if !strings.Contains(gotUp, `namespace="ziggymart.evvjb"`) {
		t.Fatalf("up query must use temporal namespace: %s", gotUp)
	}
	if strings.Contains(gotUp, `namespace="orders"`) {
		t.Fatalf("up query must not use k8s namespace: %s", gotUp)
	}
	if !strings.Contains(gotUp, `temporal_io_build_id="b1"`) || !strings.Contains(gotUp, `worker_type="WorkflowWorker"`) {
		t.Fatalf("up query missing labels: %s", gotUp)
	}
}

func TestLabelSelectorUsesTemporalNamespace(t *testing.T) {
	const (
		temporalNS = "ziggymart.evvjb"
		k8sNS      = "orders"
	)
	sel := LabelSelector(temporalNS, "orders-workflow-task-queue", "build-abc", "WorkflowWorker")
	if !strings.Contains(sel, `namespace="ziggymart.evvjb"`) {
		t.Fatalf("want temporal namespace in selector; got %s", sel)
	}
	if strings.Contains(sel, `namespace="orders"`) {
		t.Fatalf("k8s namespace must not appear in selector; got %s", sel)
	}
	_ = k8sNS // documents the anti-pattern this test guards against
}

func TestWorkerTypeForQueueType(t *testing.T) {
	if WorkerTypeForQueueType("workflow") != "WorkflowWorker" {
		t.Fatal("workflow")
	}
	if WorkerTypeForQueueType("activity") != "ActivityWorker" {
		t.Fatal("activity")
	}
	if WorkerTypeForQueueType("nexus") != "NexusWorker" {
		t.Fatal("nexus")
	}
}

