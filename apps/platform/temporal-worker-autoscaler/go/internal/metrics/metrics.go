// Package metrics exposes the controller's scaling decisions as Prometheus
// series on the controller-runtime metrics endpoint (/metrics on the manager's
// metrics address). These are the "why did replicas move" signals that keep the
// direct-patch scaling from being a black box (paired with Events + CRD status).
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	crmetrics "sigs.k8s.io/controller-runtime/pkg/metrics"
)

const subsystem = "temporal_worker_autoscaler"

var (
	// DesiredReplicas is the last computed target per version.
	DesiredReplicas = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Subsystem: subsystem, Name: "desired_replicas",
		Help: "Desired replicas last computed, per worker deployment version.",
	}, []string{"deployment", "build_id"})

	// CurrentReplicas is the observed replica count per version.
	CurrentReplicas = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Subsystem: subsystem, Name: "current_replicas",
		Help: "Current replicas observed, per worker deployment version.",
	}, []string{"deployment", "build_id"})

	// Backlog is the freshest backlog read from Temporal Cloud per version.
	Backlog = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Subsystem: subsystem, Name: "backlog",
		Help: "Approximate task-queue backlog last read from Temporal Cloud, per version.",
	}, []string{"deployment", "build_id", "task_queue"})

	// ScaleEvents counts replica changes by direction.
	ScaleEvents = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "scale_events_total",
		Help: "Replica changes applied, by direction (up|down).",
	}, []string{"deployment", "direction"})

	// PanicEvents counts panic-mode (burst) scale-ups.
	PanicEvents = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "panic_events_total",
		Help: "Panic-mode (burst) scale-ups triggered.",
	}, []string{"deployment"})

	// CloudCalls counts Temporal Cloud describe calls (verify rate-safety).
	CloudCalls = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "cloud_describe_total",
		Help: "DescribeWorkerDeploymentVersion calls to Temporal Cloud, by outcome.",
	}, []string{"outcome"})

	// ReconcileErrors counts reconcile failures per deployment.
	ReconcileErrors = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "reconcile_errors_total",
		Help: "Reconcile errors, per worker deployment.",
	}, []string{"deployment"})

	// SlotUpHint is the last max_over_time slot-util reading per version.
	SlotUpHint = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Subsystem: subsystem, Name: "slot_up_hint",
		Help: "Last slot utilization up hint (max_over_time), per version. NaN omitted.",
	}, []string{"deployment", "build_id"})

	// SlotIdleHint is the last avg_over_time slot-util reading per version.
	SlotIdleHint = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Subsystem: subsystem, Name: "slot_idle_hint",
		Help: "Last slot utilization idle hint (avg_over_time), per version. NaN omitted.",
	}, []string{"deployment", "build_id"})

	// SlotQueryFailures counts Prometheus slot hint transport/query errors.
	SlotQueryFailures = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "slot_query_failures_total",
		Help: "Prometheus slot hint query transport/API failures, per deployment.",
	}, []string{"deployment"})

	// SlotSeriesMissing counts reconciles where the slot hint query succeeded but
	// returned no series for the version (distinct from query failures).
	SlotSeriesMissing = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "slot_series_missing_total",
		Help: "Slot hint queries that returned no matching series, per deployment.",
	}, []string{"deployment"})

	// SlotDrivenUpEvents counts scale-up decisions where the slot OR term fired.
	SlotDrivenUpEvents = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "slot_driven_up_events_total",
		Help: "Scale-up decisions where slot saturation drove the OR-up term.",
	}, []string{"deployment"})

	// SlotDownVetoEvents counts scale-down vetoes from busy slots.
	SlotDownVetoEvents = prometheus.NewCounterVec(prometheus.CounterOpts{
		Subsystem: subsystem, Name: "slot_down_veto_events_total",
		Help: "Scale-down vetoes because slots were still busy.",
	}, []string{"deployment"})
)

func init() {
	crmetrics.Registry.MustRegister(
		DesiredReplicas, CurrentReplicas, Backlog,
		ScaleEvents, PanicEvents, CloudCalls, ReconcileErrors,
		SlotUpHint, SlotIdleHint, SlotQueryFailures, SlotSeriesMissing,
		SlotDrivenUpEvents, SlotDownVetoEvents,
	)
}
