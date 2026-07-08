package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// QueueType selects which task-queue backlog drives scaling for this worker.
// +kubebuilder:validation:Enum=workflow;activity;nexus
type QueueType string

const (
	QueueTypeWorkflow QueueType = "workflow"
	QueueTypeActivity QueueType = "activity"
	QueueTypeNexus    QueueType = "nexus"
)

// WorkerAutoscalerSpec declares how one Temporal worker (a WorkerDeployment and
// all of its build-id versions) is scaled from live task-queue backlog.
//
// The controller discovers the concrete, per-version Deployments to patch by the
// labels the Temporal Worker Controller stamps on them
// (temporal.io/deployment-name = .spec.deploymentName), and scales EACH version
// on its OWN backlog so drainers sit at min while the current version scales.
type WorkerAutoscalerSpec struct {
	// DeploymentName is the WorkerDeployment name (matches the
	// temporal.io/deployment-name label on the versioned Deployments to scale),
	// e.g. "orders-workflow".
	// +kubebuilder:validation:MinLength=1
	DeploymentName string `json:"deploymentName"`

	// WorkerDeploymentName is the Temporal-side Worker Deployment name used in the
	// DescribeWorkerDeploymentVersion API, e.g. "orders/orders-workflow". If empty,
	// it defaults to "<this CR's namespace>/<deploymentName>" (the Worker
	// Controller's convention).
	// +optional
	WorkerDeploymentName string `json:"workerDeploymentName,omitempty"`

	// TaskQueue is the Temporal task queue whose backlog drives scaling,
	// e.g. "orders-workflow-task-queue".
	// +kubebuilder:validation:MinLength=1
	TaskQueue string `json:"taskQueue"`

	// QueueType selects which backlog to read for this worker.
	// +kubebuilder:default=workflow
	QueueType QueueType `json:"queueType,omitempty"`

	// MinReplicas is the floor per version. 0 enables scale-to-zero (the Temporal
	// task queue is the durable buffer during a cold start).
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	MinReplicas int32 `json:"minReplicas,omitempty"`

	// MaxReplicas is the ceiling per version.
	// +kubebuilder:validation:Minimum=1
	MaxReplicas int32 `json:"maxReplicas"`

	// TargetBacklogPerReplica is the desired backlog tasks per replica; desired
	// replicas = ceil(backlog / target) subject to the deadband + bounds.
	// +kubebuilder:default=5
	// +kubebuilder:validation:Minimum=1
	TargetBacklogPerReplica int32 `json:"targetBacklogPerReplica,omitempty"`

	// Behavior tunes reaction speed and flap damping. Optional; defaults model an
	// "overscale-briefly beats flap" posture (fast up, damped down).
	// +optional
	Behavior *ScalingBehavior `json:"behavior,omitempty"`

	// SlotScaleUpEnabled turns on the OR-up slot term: scale up when slot
	// utilization exceeds target even if backlog is within tolerance.
	// +kubebuilder:default=false
	SlotScaleUpEnabled bool `json:"slotScaleUpEnabled,omitempty"`

	// SlotScaleDownGateEnabled turns on the AND-down veto: only shrink when slots
	// are idle (avg utilization below scaleDownSlotUtilizationPercent).
	// +kubebuilder:default=false
	SlotScaleDownGateEnabled bool `json:"slotScaleDownGateEnabled,omitempty"`

	// TargetSlotUtilizationPercent is the slot-util target for scale-up (relieve
	// pressure above this). Default 75.
	// +kubebuilder:default=75
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=100
	TargetSlotUtilizationPercent int32 `json:"targetSlotUtilizationPercent,omitempty"`

	// ScaleDownSlotUtilizationPercent is the idle gate for scale-down: veto shrink
	// while avg slot util is at or above this. Default 25.
	// +kubebuilder:default=25
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	ScaleDownSlotUtilizationPercent int32 `json:"scaleDownSlotUtilizationPercent,omitempty"`

	// SlotUpWindowSeconds is the Prometheus max_over_time window for the up hint.
	// Default 60 (1m).
	// +kubebuilder:default=60
	// +kubebuilder:validation:Minimum=1
	SlotUpWindowSeconds int32 `json:"slotUpWindowSeconds,omitempty"`

	// SlotDownWindowSeconds is the Prometheus avg_over_time window for the down
	// idle hint. Default 120 (2m).
	// +kubebuilder:default=120
	// +kubebuilder:validation:Minimum=1
	SlotDownWindowSeconds int32 `json:"slotDownWindowSeconds,omitempty"`
}

// ScalingBehavior mirrors the useful parts of HPA behavior + Knative's
// stable/panic model. All fields optional; the controller applies defaults.
type ScalingBehavior struct {
	// ScaleUpStabilizationSeconds smooths scale-up recommendations. Default 0
	// (react immediately up).
	// +kubebuilder:default=0
	// +kubebuilder:validation:Minimum=0
	ScaleUpStabilizationSeconds int32 `json:"scaleUpStabilizationSeconds,omitempty"`

	// ScaleDownStabilizationSeconds holds the MAX recommendation over this window
	// before scaling down — the primary anti-flap lever. Default 120.
	// +kubebuilder:default=120
	// +kubebuilder:validation:Minimum=0
	ScaleDownStabilizationSeconds int32 `json:"scaleDownStabilizationSeconds,omitempty"`

	// TolerancePercent is the deadband: no action while |ratio-1| <= tolerance.
	// Default 10 (matches HPA's 0.1).
	// +kubebuilder:default=10
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	TolerancePercent int32 `json:"tolerancePercent,omitempty"`

	// MaxScaleUpStep caps replicas added per decision (0 = unlimited). Default 0.
	// +kubebuilder:default=0
	// +kubebuilder:validation:Minimum=0
	MaxScaleUpStep int32 `json:"maxScaleUpStep,omitempty"`

	// MaxScaleDownStep caps replicas removed per decision (0 = unlimited).
	// Default 1 (bleed down gradually).
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	MaxScaleDownStep int32 `json:"maxScaleDownStep,omitempty"`

	// PanicThresholdPercent triggers Knative-style panic scaling: if backlog
	// demands >= this percent of current capacity, bypass up-stabilization and
	// scale up hard. Default 200 (2x). 0 disables panic mode.
	// +kubebuilder:default=200
	// +kubebuilder:validation:Minimum=0
	PanicThresholdPercent int32 `json:"panicThresholdPercent,omitempty"`
}

// VersionStatus reports the last decision for one build-id version.
type VersionStatus struct {
	BuildID         string `json:"buildId"`
	Backlog         int64  `json:"backlog"`
	CurrentReplicas int32  `json:"currentReplicas"`
	DesiredReplicas int32  `json:"desiredReplicas"`
}

// WorkerAutoscalerStatus makes every scaling decision inspectable via
// `kubectl describe` — the cure for "replicas change but there's no HPA".
type WorkerAutoscalerStatus struct {
	// ObservedGeneration is the .metadata.generation last reconciled.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// DesiredReplicas summed across live versions (for the printer column).
	// +optional
	DesiredReplicas int32 `json:"desiredReplicas,omitempty"`

	// CurrentReplicas summed across live versions.
	// +optional
	CurrentReplicas int32 `json:"currentReplicas,omitempty"`

	// LastScaleTime is when the controller last changed a replica count.
	// +optional
	LastScaleTime *metav1.Time `json:"lastScaleTime,omitempty"`

	// LastReason is a human-readable explanation of the last decision.
	// +optional
	LastReason string `json:"lastReason,omitempty"`

	// Versions is the per-build-id breakdown of the last decision.
	// +optional
	// +listType=map
	// +listMapKey=buildId
	Versions []VersionStatus `json:"versions,omitempty"`

	// Conditions: Ready (signal source reachable), Active (currently scaling),
	// Paused.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=wasc;wa,categories=autoscaling
// +kubebuilder:printcolumn:name="Deployment",type=string,JSONPath=`.spec.deploymentName`
// +kubebuilder:printcolumn:name="Queue",type=string,JSONPath=`.spec.taskQueue`
// +kubebuilder:printcolumn:name="Min",type=integer,JSONPath=`.spec.minReplicas`
// +kubebuilder:printcolumn:name="Max",type=integer,JSONPath=`.spec.maxReplicas`
// +kubebuilder:printcolumn:name="Current",type=integer,JSONPath=`.status.currentReplicas`
// +kubebuilder:printcolumn:name="Desired",type=integer,JSONPath=`.status.desiredReplicas`
// +kubebuilder:printcolumn:name="Reason",type=string,JSONPath=`.status.lastReason`,priority=1

// WorkerAutoscaler scales a Temporal worker's per-version Deployments directly
// from live task-queue backlog, for seconds-level actuation (ADR-0023).
type WorkerAutoscaler struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   WorkerAutoscalerSpec   `json:"spec,omitempty"`
	Status WorkerAutoscalerStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// WorkerAutoscalerList contains a list of WorkerAutoscaler.
type WorkerAutoscalerList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []WorkerAutoscaler `json:"items"`
}

func init() {
	SchemeBuilder.Register(&WorkerAutoscaler{}, &WorkerAutoscalerList{})
}
