# Checkpoint 0031 — Slot utilization as a second scaling signal (asymmetric OR-up / AND-down)

**Date:** 2026-07-08
**Status:** **Landed + verified live** (kind + Cloud). Merged to `main` via PR #41
(commit `f45065f`). Chart `temporal-worker-autoscaler` 0.1.4 → 0.1.5, `orders-workers`
0.1.19 → 0.1.20.
**Relates to:** ADR-0023 (worker autoscaling), ADR-0021 (metrics), checkpoint 0028
(custom autoscaler this extends).
**Roles:** Architect planned/reviewed/validated + drove PR; Cursor implemented the code.

## Why

The custom `temporal-worker-autoscaler` (checkpoint 0028) scaled purely on server-side
**backlog** (`DescribeWorkerDeploymentVersion` + `ReportTaskQueueStats`), per version.
Backlog is authoritative but lagging and rate-limited (shared Worker-Deployment-Read
budget). We wanted a **leading, worker-local** signal that costs **zero** Cloud API load:
**slot utilization** from in-cluster Prometheus (`slots_used / (used + available)`).

The expressiveness win of owning the controller: combine the two signals **asymmetrically**
in a way a stock HPA cannot.

- **Scale up = OR.** Backlog says add workers OR slots saturated at current replicas → up.
  Catches sustained no-headroom before backlog reflects it.
- **Scale down = AND.** Shrink only when backlog low AND slots idle. Slot util is a **veto**
  on scale-down — a multi-metric HPA can only take max-desired (OR-only), never AND-down.

Backlog stays primary; slot util is a bounded modifier. Posture unchanged: fast-up / slow-down.

## What landed

**Prometheus signal (`deploy/argocd/applications/prometheus.yaml`)**
- New recording rule `temporal_slot_utilization:by_build` grouped by
  `(namespace, worker_type, task_queue, temporal_io_build_id)` — per-version, keeps `build_id`
  (the existing dashboard rule `temporal_slot_utilization` drops it; left untouched).
- Global `scrape_interval` + `evaluation_interval` pinned to **30s**, rule group `interval: 30s`
  — the derived series is only as fresh as the rule recompute; unpinned it inherited ~1m.

**Decision math (`internal/scaling/scaling.go`)**
- `Input` gained `SlotUpHint`, `SlotIdleHint`, `SlotUpOn`, `SlotDownGateOn`, `TargetSlotUtil`,
  `IdleSlotUtil`. **NaN = no data** (0.0 is a legitimate idle reading), guarded by `math.IsNaN`.
- OR-up: `slotDrivenDesired` = `ceil(current * upHint / target)`, raised into `raw` **before** the
  tolerance-deadband early-return so quiet-backlog + saturated-slots can still scale (the deadband
  trap). Inherits the existing panic / up-stab / step-clamp path unchanged.
- AND-down veto: after down-stabilization, if `slotsBusy` (down-gate on, idle hint ≥ threshold)
  hold at current. Written as a composable `slotsBusy` predicate so a later change can AND
  `pollersAlive` for the deferred safe-to-zero guard.
- Fail-open: NaN up hint → backlog decides up; NaN idle hint → do not veto (backlog is primary
  safety). Both-flags-off is byte-identical to pre-feature behavior.

**Prometheus client (`internal/promsource/client.go`, new)**
- Instant-query client; `SlotHints(...)` returns `(upHint, idleHint)` — `max_over_time[upWindow]`
  for up, `avg_over_time[downWindow]` for down; empty series → NaN.
- `WorkerTypeForQueueType`: activity → `ActivityWorker`, nexus → `NexusWorker`, default →
  `WorkflowWorker` (label values verified live via Prometheus MCP at Gate 1).

**Controller (`internal/controller/workerautoscaler_controller.go`)**
- New `TemporalNamespace` reconciler field, sourced from config, passed to `SlotHints` — the
  metric `namespace` label is the **Temporal** namespace (`ziggymart.<acct>`), NOT the k8s
  namespace. This was the critical bug (below).
- Split metrics: `slot_query_failures` (transport errors) + `slot_series_missing` (empty series).

**CRD (`api/v1alpha1/workerautoscaler_types.go`)**
- `SlotScaleUpEnabled` / `SlotScaleDownGateEnabled` (default false — two independent flags so
  up-use and down-use are separable, the seam for future scale-to-zero),
  `TargetSlotUtilizationPercent` (75), `ScaleDownSlotUtilizationPercent` (25),
  `SlotUpWindowSeconds` (60), `SlotDownWindowSeconds` (120).

## Key design decisions (all confirmed)

- **Per-version granularity** — a fleet-blended ratio is wrong during a canary/rollout when old
  and new versions have different pressure. Cost: ~1–3 live versions, negligible.
- **Smooth in Prometheus, not the controller** — `max_over_time`/`avg_over_time` windows; NO
  controller-side ring buffer. The existing 120s down-stabilization already smooths the
  *recommendation*; a slot history would double-damp. Main "don't over-engineer" call.
- **30s freshness is right, not 15s** — slot-up detects a sustained no-headroom *condition*, not a
  spike (backlog owns spikes via the live 15s gRPC poll + 200% panic bypass). A scraped-then-
  evaluated metric structurally lags a live read, so matching 15s buys nothing and creates a false
  "slot leads backlog" expectation.
- **Scale-to-zero designed-for, not enabled** — no hardcoded `min >= 1` in the new logic;
  `minReplicas` default stays 1 on orders CRs. The AND-down gate IS the core of ADR-0023's
  deferred safe-to-zero composite (backlog zero AND slots idle AND pollers alive).
- **Valid because workers are fixed-size suppliers** — `slots_available` is only meaningful with a
  fixed pool. Guard note: revisit if workers move to resource-based suppliers.

## Bugs found + fixed during review/validation

- **Namespace selector (Cursor found, confirmed via MCP):** `promsource` queried
  `namespace=<k8s ns>` (orders) but the label is the Temporal namespace → 0 series → NaN → 14
  `slot_query_failures`. Fixed via the `TemporalNamespace` reconciler field.
- **BUG 1 — OR-up floor (independent `/code-review` caught):** `if desiredSlots > raw` ignored
  `slotWantsUp`, so a below-target slot util acted as a scale-down floor. Trace: current=3,
  backlog=0, util=0.5, target=0.75 → held at 2 instead of min 1. Fixed to
  `if slotWantsUp && desiredSlots > raw`; regression test `TestLowSlotUtilDoesNotFloorReplicas`.
- **BUG 2 — idle coercion:** `if idlePct == 0 { idlePct = 25 }` coerced an explicit legal 0
  (strictest gate) to 25. Removed; rely on CRD default.

## Live validation (kind + Cloud)

Ran the four gated phases (signal real → plumbing → logic + tests → live). Verified via
Prometheus MCP that `:by_build` carries the correct Temporal namespace + matching build_id and
both hints are non-NaN post-fix. BUG 1 was deterministic/unit-covered so no live re-run needed.
**Cloud footprint: 2 workflow executions total** (well under the repo ceiling).

## Open / deferred follow-ups (tracked in PR #41 body)

1. Refactor `promsource` onto the official `client_golang/prometheus/v1` client.
2. `domain-workers` scaffold: slot-field parity + chart version bump.
3. Gauge-staleness: set NaN on a series that goes missing between scrapes.
4. `WorkerTypeForQueueType` hardening for unknown queue types.
5. ADR-0023 note that slot utilization is now a live second signal; scale-to-zero still deferred.

## Spun-out reliability workstream (separate branch)

The Gate 4 live test exposed local-deploy friction (a stopped kind cluster with a stale registry
EndpointSlice after host restart, hand-rewired instead of reconciled). Being handled on branch
`kind-live-test-reliability`: restart-recovery runbook + AGENTS.md get-to-known-good guidance
(landed), and self-healing `just cluster-start` + a `kind-ready` entry point (Cursor implementing).
Not part of this feature; tracked in its own checkpoint/PR.
