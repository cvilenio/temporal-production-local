# ADR-0004: Worker versioning via the Temporal Worker Controller

- **Status:** Accepted
- **Date:** 2026-06-23

## Context

The platform must demonstrate Worker Versioning realistically: multiple worker versions
live at once, safe ramp/rollback, in-flight executions unaffected. Temporal's modern model
is **Worker Deployment Versioning** (Server ≥ 1.28 / CLI ≥ 1.4) — deployment identity is a
worker option, PINNED/AUTO_UPGRADE is a per-workflow declaration. The legacy Build-ID
task-queue compatibility-set API is superseded.

## Decision

- **Kernel:** `orders.worker` reads `TEMPORAL_DEPLOYMENT_NAME` and
  `TEMPORAL_WORKER_BUILD_ID` from env and builds a `WorkerDeploymentConfig` when present;
  unset → version-agnostic (local/compose unchanged).
- **Kubernetes:** use the **Temporal Worker Controller** (`kind: WorkerDeployment` CRD +
  `kind: Connection`). The controller injects the env vars and derives the Build ID from the
  pod-template hash, so **shipping a version = a new image tag**. No edits to the worker pod
  spec.
- **Rollout:** default to the controller's **`AllAtOnce`** strategy. (Originally `Manual`,
  changed after a live kind+Cloud run: `Manual` registers every version `Inactive` and needs
  a human `set-current-version` for *each* version — including the first — so a freshly
  provisioned cluster has **no Current version** and versioned workflows sit pending forever.
  `AllAtOnce` auto-promotes the first healthy version, so `just platform-up` yields a routable
  cluster with no manual step.) `Progressive` (with `steps:` + an optional gate workflow) is
  the prod-grade upgrade for demoing safe canary rollouts; both non-`Manual` strategies skip
  the ramp on a cold start and promote v1 immediately. Reserve `Manual` for hand-gated
  promotion.
  - **Ownership caveat:** a manual `set-current-version` (or `set-ramping-version`) sets the
    server's `LastModifierIdentity` away from the controller, which then backs off and stops
    managing routing for that deployment. Hand control back with the version metadata
    `temporal.io/ignore-last-modifier: true`, or delete the Worker Deployment from the server
    and redeploy clean.
- **Workflow behavior:** plan to mark `OrderWorkflow` `PINNED` so in-flight orders never
  replay against new code; compatible in-place edits use `workflow.patched(...)` only on
  AUTO_UPGRADE workflows. Not yet enabled — tracked as follow-up.

## Consequences

- Versioning wiring is isolated to the worker entrypoint and the deploy layer; workflow and
  activity code stay clean.
- The reference implementation (`alexandreroman/temporal-versioning-demo`) is portable: wrap
  its CRD usage in a Helm chart + ArgoCD Application instead of Kustomize/kbld.
- Requires the Worker Controller installed on kind (cert-manager dependency).
