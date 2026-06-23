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
- **Rollout:** start with the controller's `Manual` strategy (UI/SDK drives ramp via
  `SetCurrentVersion` / `SetRampingVersion`); switch to `Progressive` when we want ArgoCD to
  own the ramp.
- **Workflow behavior:** plan to mark `OrderWorkflow` `PINNED` so in-flight orders never
  replay against new code; compatible in-place edits use `workflow.patched(...)` only on
  AUTO_UPGRADE workflows. Not yet enabled — tracked as follow-up.

## Consequences

- Versioning wiring is isolated to the worker entrypoint and the deploy layer; workflow and
  activity code stay clean.
- The reference implementation (`alexandreroman/temporal-versioning-demo`) is portable: wrap
  its CRD usage in a Helm chart + ArgoCD Application instead of Kustomize/kbld.
- Requires the Worker Controller installed on kind (cert-manager dependency).
