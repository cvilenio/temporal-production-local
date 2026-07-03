// Package controller holds the WorkerAutoscaler reconciler: for each declared
// worker it discovers the per-version Deployments (by the Worker Controller's
// labels), reads each version's fresh backlog from Temporal Cloud, computes
// desired replicas, patches the Deployment's replica count directly, and records
// every decision (Events on the Deployment + Prometheus + CRD status).
//
// The poll is folded into the reconciler on purpose: versions are discovered
// from k8s, and controller-runtime serializes reconciles (MaxConcurrentReconciles
// = 1) so there is exactly ONE Temporal Cloud caller, rate-limited + jittered.
package controller

import (
	"context"
	"errors"
	"fmt"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/tools/record"
	"golang.org/x/time/rate"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	crcontroller "sigs.k8s.io/controller-runtime/pkg/controller"
	"sigs.k8s.io/controller-runtime/pkg/log"

	autoscalingv1alpha1 "github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/api/v1alpha1"
	"github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/metrics"
	"github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/scaling"
	temporalpkg "github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/temporal"
)

const (
	labelDeploymentName = "temporal.io/deployment-name"
	labelBuildID        = "temporal.io/build-id"

	// The Worker Controller's WorkerDeployment CR — the source of truth for which
	// build ID is currently routed (and any ramping target). We read it to learn
	// which versions are ACTIVE so we never fight the controller over a draining one.
	wdGroup   = "temporal.io"
	wdVersion = "v1alpha1"
	wdKind    = "WorkerDeployment"

	annManagedBy       = "autoscaler.ziggymart.io/managed-by"
	annScaler          = "autoscaler.ziggymart.io/scaler"
	annLastScaleReason = "autoscaler.ziggymart.io/last-scale-reason"
	annLastScaleTime   = "autoscaler.ziggymart.io/last-scale-time"

	managedByValue = "temporal-worker-autoscaler"
)

// BacklogReader is the Temporal Cloud accessor (interface for testability).
type BacklogReader interface {
	VersionBacklog(ctx context.Context, deploymentName, buildID, taskQueue, queueType string) (int64, error)
}

// WorkerAutoscalerReconciler reconciles a WorkerAutoscaler.
type WorkerAutoscalerReconciler struct {
	client.Client
	Recorder record.EventRecorder
	Temporal BacklogReader
	Algo     scaling.Algorithm
	Limiter  *rate.Limiter // bounds Temporal Cloud describe QPS across all CRs

	// RequeueInterval is the fast actuation cadence (seconds) — direct patch, so
	// it does not wait on the HPA sync loop.
	RequeueInterval time.Duration
}

// +kubebuilder:rbac:groups=autoscaling.ziggymart.io,resources=workerautoscalers,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=autoscaling.ziggymart.io,resources=workerautoscalers/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=autoscaling.ziggymart.io,resources=workerautoscalers/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=apps,resources=deployments/scale,verbs=get;update;patch
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
// +kubebuilder:rbac:groups=temporal.io,resources=workerdeployments,verbs=get;list;watch

// Reconcile scales one worker's versions from fresh backlog.
func (r *WorkerAutoscalerReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	l := log.FromContext(ctx)

	var wa autoscalingv1alpha1.WorkerAutoscaler
	if err := r.Get(ctx, req.NamespacedName, &wa); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	beh := behaviorFromSpec(&wa.Spec)
	queueType := string(wa.Spec.QueueType)
	if queueType == "" {
		queueType = string(autoscalingv1alpha1.QueueTypeWorkflow)
	}
	depName := wa.Spec.WorkerDeploymentName
	if depName == "" {
		depName = temporalpkg.DefaultWorkerDeploymentName(wa.Namespace, wa.Spec.DeploymentName)
	}

	// Discover the per-version Deployments created by the Worker Controller.
	var deps appsv1.DeploymentList
	if err := r.List(ctx, &deps,
		client.InNamespace(wa.Namespace),
		client.MatchingLabels{labelDeploymentName: wa.Spec.DeploymentName},
	); err != nil {
		metrics.ReconcileErrors.WithLabelValues(wa.Spec.DeploymentName).Inc()
		return ctrl.Result{}, err
	}

	// Learn which versions are fully DRAINED from the Worker Controller's
	// WorkerDeployment CR. We manage every other version — Current, Ramping, AND
	// still-Draining (a draining version has no NEW workflows routed to it, but its
	// already-started PINNED workflows keep generating tasks, so it has real backlog
	// and must keep scaling — critical for canary / side-by-side rollouts). We skip
	// only fully-Drained (no open workflows) versions: the Worker Controller drives
	// those to zero and deletes them, so flooring them at minReplicas would fight it
	// (the Deployment ping-pongs 0<->min and the version can never be GC'd). If the
	// CR is absent (no Worker Controller), the set is empty → manage every version.
	drained := r.drainedBuildIDs(ctx, wa.Namespace, wa.Spec.DeploymentName)

	var (
		versions          []autoscalingv1alpha1.VersionStatus
		sumCurrent, sumDes int32
		anyErr            bool
		lastReason        string
		scaled            bool
		skipped           []string
	)

	for i := range deps.Items {
		dep := &deps.Items[i]
		buildID := dep.Labels[labelBuildID]
		current := currentReplicas(dep)

		if _, isDrained := drained[buildID]; isDrained {
			// Fully drained — owned by the Worker Controller's teardown (scale to
			// zero + delete). Draining (still-serving) versions are NOT in this set.
			skipped = append(skipped, buildID)
			continue
		}

		if err := r.Limiter.Wait(ctx); err != nil {
			return ctrl.Result{}, err
		}
		backlog, err := r.Temporal.VersionBacklog(ctx, depName, buildID, wa.Spec.TaskQueue, queueType)
		if err != nil {
			// Two soft cases: both keep the current count, retry next cycle, and log
			// quietly (no stacktrace, not flagged as a reconcile error) because they
			// self-heal and are not caused by workflow demand:
			//   - ErrRateLimited: the Cloud describe API bounds our freshness.
			//   - ErrTransient: Cloud recycled a long-lived gRPC connection (EOF /
			//     Unavailable); gRPC reconnects and the next tick succeeds.
			switch {
			case errors.Is(err, temporalpkg.ErrRateLimited):
				metrics.CloudCalls.WithLabelValues("rate_limited").Inc()
				l.V(1).Info("backlog read rate-limited; holding replicas",
					"deployment", depName, "buildID", buildID)
			case errors.Is(err, temporalpkg.ErrTransient):
				metrics.CloudCalls.WithLabelValues("transient").Inc()
				l.V(1).Info("backlog read hit a recycled connection; holding replicas",
					"deployment", depName, "buildID", buildID)
			default:
				metrics.CloudCalls.WithLabelValues("error").Inc()
				metrics.ReconcileErrors.WithLabelValues(wa.Spec.DeploymentName).Inc()
				l.Error(err, "reading backlog", "deployment", depName, "buildID", buildID)
				anyErr = true
			}
			// Keep current replicas for this version; don't guess on a read error.
			versions = append(versions, autoscalingv1alpha1.VersionStatus{
				BuildID: buildID, Backlog: -1, CurrentReplicas: current, DesiredReplicas: current,
			})
			sumCurrent += current
			sumDes += current
			continue
		}
		metrics.CloudCalls.WithLabelValues("ok").Inc()
		metrics.Backlog.WithLabelValues(wa.Spec.DeploymentName, buildID, wa.Spec.TaskQueue).Set(float64(backlog))

		dec := r.Algo.Decide(cacheKey(&wa, buildID), scaling.Input{
			Current:          current,
			Backlog:          backlog,
			TargetPerReplica: wa.Spec.TargetBacklogPerReplica,
			Min:              wa.Spec.MinReplicas,
			Max:              wa.Spec.MaxReplicas,
			Now:              time.Now(),
			Behavior:         beh,
		})

		metrics.CurrentReplicas.WithLabelValues(wa.Spec.DeploymentName, buildID).Set(float64(current))
		metrics.DesiredReplicas.WithLabelValues(wa.Spec.DeploymentName, buildID).Set(float64(dec.Desired))

		if dec.Changed {
			if err := r.applyScale(ctx, dep, &wa, dec); err != nil {
				metrics.ReconcileErrors.WithLabelValues(wa.Spec.DeploymentName).Inc()
				l.Error(err, "patching replicas", "deployment", dep.Name)
				anyErr = true
			} else {
				scaled = true
				lastReason = dec.Reason
				dir := "up"
				if dec.Desired < current {
					dir = "down"
				}
				metrics.ScaleEvents.WithLabelValues(wa.Spec.DeploymentName, dir).Inc()
				if dec.Panic {
					metrics.PanicEvents.WithLabelValues(wa.Spec.DeploymentName).Inc()
				}
				r.Recorder.Eventf(dep, corev1.EventTypeNormal, "Scaled",
					"temporal-worker-autoscaler scaled %s from %d to %d (%s)",
					dep.Name, current, dec.Desired, dec.Reason)
			}
		} else {
			// Keep the managed-by breadcrumb fresh even without a scale change.
			if err := r.ensureAnnotations(ctx, dep, &wa, ""); err != nil {
				l.V(1).Info("annotate (no-op scale)", "err", err.Error())
			}
		}

		versions = append(versions, autoscalingv1alpha1.VersionStatus{
			BuildID: buildID, Backlog: backlog, CurrentReplicas: current, DesiredReplicas: dec.Desired,
		})
		sumCurrent += current
		sumDes += dec.Desired
	}

	if len(skipped) > 0 {
		l.V(1).Info("skipping fully-drained versions (owned by Worker Controller teardown)",
			"deployment", wa.Spec.DeploymentName, "buildIDs", skipped)
	}

	r.updateStatus(ctx, &wa, versions, sumCurrent, sumDes, scaled, anyErr, lastReason)
	return ctrl.Result{RequeueAfter: r.requeue()}, nil
}

// drainedBuildIDs returns the set of build IDs the autoscaler must NOT manage —
// versions the Worker Controller has fully drained (no open workflows) and is
// tearing down (scaling to zero + deleting). Read from the Worker Controller's
// WorkerDeployment CR (named after the k8s deployment); an in-cluster read, no
// extra Temporal Cloud call. A version is "drained" when its deprecated-version
// entry reports status=Drained or eligibleForDeletion=true.
//
// Everything NOT in this set is managed normally — Current, Ramping, and
// still-Draining versions alike. A Draining version keeps serving its already
// pinned workflows, so it has genuine backlog and must stay autoscaled (this is
// what preserves proportional scaling across versions running side by side during
// a canary or a gradual migration). An empty set (CR absent, or no drained
// versions) means "manage every discovered version" — the safe default.
func (r *WorkerAutoscalerReconciler) drainedBuildIDs(ctx context.Context, namespace, name string) map[string]struct{} {
	drained := map[string]struct{}{}
	var wd unstructured.Unstructured
	wd.SetGroupVersionKind(schema.GroupVersionKind{Group: wdGroup, Version: wdVersion, Kind: wdKind})
	if err := r.Get(ctx, client.ObjectKey{Namespace: namespace, Name: name}, &wd); err != nil {
		return drained // CR absent (no Worker Controller) → manage every version.
	}
	deprecated, ok, _ := unstructured.NestedSlice(wd.Object, "status", "deprecatedVersions")
	if !ok {
		return drained
	}
	for _, entry := range deprecated {
		m, ok := entry.(map[string]interface{})
		if !ok {
			continue
		}
		buildID, _, _ := unstructured.NestedString(m, "buildID")
		if buildID == "" {
			continue
		}
		status, _, _ := unstructured.NestedString(m, "status")
		eligible, _, _ := unstructured.NestedBool(m, "eligibleForDeletion")
		if status == "Drained" || eligible {
			drained[buildID] = struct{}{}
		}
	}
	return drained
}

func (r *WorkerAutoscalerReconciler) applyScale(ctx context.Context, dep *appsv1.Deployment, wa *autoscalingv1alpha1.WorkerAutoscaler, dec scaling.Decision) error {
	patch := client.MergeFrom(dep.DeepCopy())
	desired := dec.Desired
	dep.Spec.Replicas = &desired
	setAnnotations(dep, wa, dec.Reason)
	return r.Patch(ctx, dep, patch)
}

func (r *WorkerAutoscalerReconciler) ensureAnnotations(ctx context.Context, dep *appsv1.Deployment, wa *autoscalingv1alpha1.WorkerAutoscaler, _ string) error {
	if dep.Annotations[annManagedBy] == managedByValue &&
		dep.Annotations[annScaler] == scalerRef(wa) {
		return nil
	}
	patch := client.MergeFrom(dep.DeepCopy())
	setAnnotations(dep, wa, "")
	return r.Patch(ctx, dep, patch)
}

func setAnnotations(dep *appsv1.Deployment, wa *autoscalingv1alpha1.WorkerAutoscaler, reason string) {
	if dep.Annotations == nil {
		dep.Annotations = map[string]string{}
	}
	dep.Annotations[annManagedBy] = managedByValue
	dep.Annotations[annScaler] = scalerRef(wa)
	if reason != "" {
		dep.Annotations[annLastScaleReason] = reason
		dep.Annotations[annLastScaleTime] = time.Now().UTC().Format(time.RFC3339)
	}
}

func (r *WorkerAutoscalerReconciler) updateStatus(ctx context.Context, wa *autoscalingv1alpha1.WorkerAutoscaler, versions []autoscalingv1alpha1.VersionStatus, cur, des int32, scaled, anyErr bool, reason string) {
	l := log.FromContext(ctx)
	wa.Status.Versions = versions
	wa.Status.CurrentReplicas = cur
	wa.Status.DesiredReplicas = des
	wa.Status.ObservedGeneration = wa.Generation
	if reason != "" {
		wa.Status.LastReason = reason
	}
	if scaled {
		now := metav1.Now()
		wa.Status.LastScaleTime = &now
	}
	readyStatus, readyReason, readyMsg := metav1.ConditionTrue, "SignalReady", "Backlog signal reachable"
	if anyErr {
		readyStatus, readyReason, readyMsg = metav1.ConditionFalse, "SignalError", "One or more backlog reads failed"
	}
	meta.SetStatusCondition(&wa.Status.Conditions, metav1.Condition{
		Type: "Ready", Status: readyStatus, Reason: readyReason, Message: readyMsg,
		ObservedGeneration: wa.Generation,
	})
	meta.SetStatusCondition(&wa.Status.Conditions, metav1.Condition{
		Type: "Active", Status: boolCond(des > wa.Spec.MinReplicas), Reason: "Evaluated",
		Message: fmt.Sprintf("desired=%d min=%d", des, wa.Spec.MinReplicas),
		ObservedGeneration: wa.Generation,
	})
	if err := r.Status().Update(ctx, wa); err != nil {
		l.V(1).Info("status update failed", "err", err.Error())
	}
}

func (r *WorkerAutoscalerReconciler) requeue() time.Duration {
	if r.RequeueInterval <= 0 {
		return 3 * time.Second
	}
	return r.RequeueInterval
}

// SetupWithManager registers the reconciler with MaxConcurrentReconciles=1 so
// there is exactly one Temporal Cloud caller. We do NOT Own() Deployments (the
// Worker Controller / GitOps own them); the relationship is expressed via
// annotations + Events, never an ownerReference.
func (r *WorkerAutoscalerReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&autoscalingv1alpha1.WorkerAutoscaler{}).
		Named("workerautoscaler").
		WithOptions(crcontroller.Options{MaxConcurrentReconciles: 1}).
		Complete(r)
}

// --- helpers ---------------------------------------------------------------

func currentReplicas(dep *appsv1.Deployment) int32 {
	if dep.Spec.Replicas != nil {
		return *dep.Spec.Replicas
	}
	return dep.Status.Replicas
}

func cacheKey(wa *autoscalingv1alpha1.WorkerAutoscaler, buildID string) string {
	return fmt.Sprintf("%s/%s/%s", wa.Namespace, wa.Name, buildID)
}

func scalerRef(wa *autoscalingv1alpha1.WorkerAutoscaler) string {
	return fmt.Sprintf("%s/%s", wa.Namespace, wa.Name)
}

func boolCond(b bool) metav1.ConditionStatus {
	if b {
		return metav1.ConditionTrue
	}
	return metav1.ConditionFalse
}

func behaviorFromSpec(spec *autoscalingv1alpha1.WorkerAutoscalerSpec) scaling.Behavior {
	b := scaling.Behavior{
		TolerancePercent:      10,
		MaxScaleDownStep:      1,
		PanicThresholdPercent: 200,
		ScaleDownStabilization: 120 * time.Second,
	}
	if sb := spec.Behavior; sb != nil {
		b.ScaleUpStabilization = time.Duration(sb.ScaleUpStabilizationSeconds) * time.Second
		b.ScaleDownStabilization = time.Duration(sb.ScaleDownStabilizationSeconds) * time.Second
		b.TolerancePercent = sb.TolerancePercent
		b.MaxScaleUpStep = sb.MaxScaleUpStep
		b.MaxScaleDownStep = sb.MaxScaleDownStep
		b.PanicThresholdPercent = sb.PanicThresholdPercent
	}
	return b
}
