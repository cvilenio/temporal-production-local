# temporal-worker-autoscaler

Seconds-level, direct-patch autoscaler for Temporal worker Deployments. It exists
because neither HPA nor KEDA can deliver seconds-level actuation — both bottom out
on the cluster-wide ~15s HPA sync loop — while the load signal (task-queue backlog)
lives in Temporal, not in a request data path. See **ADR-0023** for the full
rationale, the reuse-vs-build analysis, and the defensibility narrative.

## What it does

A leader-elected singleton controller that:

1. **polls** Temporal Cloud centrally for fresh per-`(taskQueue, buildId)` backlog
   (`DescribeTaskQueueEnhanced`), rate-limited + jittered — one caller, so it is
   rate-safe regardless of fleet/version count (the KEDA per-version fan-out that
   tripped `ResourceExhausted` is gone);
2. **decides** desired replicas per version with a mirrored k8s HPA algorithm
   (ratio + tolerance deadband + max-over-window downscale stabilization + step
   clamp) plus Knative's stable/panic burst model;
3. **actuates** by patching each versioned Deployment's `.spec.replicas` directly
   (incl. scale-to-zero — the task queue is the durable buffer during cold start).

It is CRD-driven (`WorkerAutoscaler`) and signal-source-agnostic in shape (Temporal
today; Kafka/SQS later). It never sets an ownerReference on the managed Deployments
(GitOps/the Worker Controller own them); the relationship is expressed via
annotations, labels, Events, and the CRD status — so scaling is never "magic".

## Layout (mirrors the Python settings/wiring/main split, ADR-0022)

    api/v1alpha1/        WorkerAutoscaler CRD types (+ generated deepcopy)
    cmd/main.go          composition root + manager lifecycle
    internal/config/     env -> typed Config (settings.py role)
    internal/backlog/    shared freshest-backlog cache (poller writes, reconciler reads)
    internal/poller/     central Temporal Cloud poll loop            (step 3)
    internal/scaling/    decision algorithm (HPA ratio + stable/panic) (step 4)
    internal/controller/ the reconciler (reader + actuator)          (step 4/5)
    config/crd,rbac      generated manifests

## Develop

    make all        # generate + manifests + fmt + vet + build + test

Regenerate after editing the API types (`make generate manifests`). The container
image is built via the repo's `just` recipes (see `images/go.Dockerfile`).
