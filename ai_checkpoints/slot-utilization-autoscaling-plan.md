# Plan: Add slot-utilization as a second scaling signal to the custom worker autoscaler

Status: proposal / handoff to implementing agent (Cursor).
Owner of plan: Platform Architect (review + gate approvals).
Executor: Cursor agent.
Relates to: ADR-0023 (worker autoscaling), ADR-0021 (metrics), ADR-0004 (worker versioning / Worker Controller).

This document is the authoritative brief for the work.
Read it fully before touching code.
It corrects two factual assumptions from the prior chat, fixes the design decisions, gives the exact decision math, and defines four STOP gates where you hand output back for review before continuing.

---

## 1. Objective

Today the custom `temporal-worker-autoscaler` scales purely on server-side backlog (`DescribeWorkerDeploymentVersion` with `ReportTaskQueueStats: true`), per worker version.
We want to add worker-local **slot utilization** as a *second* signal, sourced entirely from our in-cluster Prometheus so it adds **zero** Temporal Cloud API load.

The posture stays **fast-up / slow-down**.
Slot utilization is a secondary signal, not the primary driver.
Backlog remains primary.
The two signals combine **asymmetrically**:

- **Scale up: OR.** If backlog says "add workers" OR slots are saturated at the current replica count, scale up. The slot term catches saturation that backlog has not reflected yet (a leading signal).
- **Scale down: AND.** Only scale down when backlog is low AND slots are idle. Slots act as a *veto* on scale-down: if workers are still busy (long-running activities, work in flight) we do not remove capacity even though the queue drained.

This is the specific expressiveness win of owning the controller.
A stock Kubernetes HPA with multiple metrics always takes the **max** desired across metrics, so it can express OR-up but **cannot** express AND-down (it cannot require two metrics to agree before shrinking).
Our controller can, and that is the point of the feature.

Non-goal for this work: *enabling* scale-to-zero.
ADR-0023 defers scale-to-zero behind a composite safe-to-zero guard (backlog + slot utilization + poller liveness).
This work builds one of those inputs (the slot-idle down-gate) but does NOT turn scale-to-zero on: `minReplicas` default stays 1 on the orders CRs.
However, this work MUST be configurable enough to support scale-to-zero later without a redesign (Decision 7): the config splits up-use from down-use, and nothing hardcodes a floor of 1.
Keep scope tight, but design the seams.

---

## 2. Ground truth and corrections (read before designing)

The prior chat contained two wrong assumptions.
Both are corrected here and both change the design.

### 2.1 CORRECTION: worker metrics scrape interval is NOT 30s

The 30s interval in `deploy/terraform/.../applications.tf` applies only to the **Temporal Cloud OpenMetrics** job (`job_name: temporal-cloud`, target `metrics.temporal.io`).
The worker SDK pods (`:9000`, discovered by pod annotations) are scraped by the community Prometheus chart's **default global interval**, which is ~1m, because no global `scrape_interval` is set anywhere in the repo.

Consequence: the prior "`avg_over_time[15s]` gives 0 to 1 samples" analysis was already optimistic.
At ~1m scrape, even a 2m window is only ~2 samples.
We must pin the worker scrape interval explicitly (see §4, decision 3).

### 2.2 CONFIRMED: per-version identity is already on the metrics (no SDK bump)

The existing recording rule `temporal_slot_utilization` (in `deploy/argocd/applications/prometheus.yaml`, group `temporal-worker`) deliberately aggregates by `(namespace, worker_type, task_queue)` and drops `build_id`, because it is a dashboard/alerting rule.

BUT the raw scraped series already carry per-version labels `temporal_io_build_id` and `temporal_io_deployment_name` (relabeled from the pod labels the Worker Controller sets).
So a per-version slot signal needs **no SDK change and no worker change**, only a new recording rule that keeps `build_id`.
See the comment block at `prometheus.yaml:81-94`, which states this explicitly.

### 2.3 CONFIRMED: workers use fixed-size slot suppliers (the design depends on this)

`apps/temporal/workers/python/{workflow,activity}/main.py` configure `max_concurrent_activities`, `max_concurrent_workflow_tasks`, etc.
These are **fixed-size** slot suppliers.

This matters because `temporal_worker_task_slots_available` is only meaningful with fixed-size suppliers.
With resource-based suppliers there is no fixed pool, `slots_available` is not a stable denominator, and `used / (used + available)` stops being a real utilization ratio.

Therefore: the utilization signal is valid **today**.
Add a guard note in code/docs: **if these workers ever move to resource-based suppliers, this slot-utilization signal must be revisited** (switch to a `slots_used`-vs-configured-capacity model or drop the down-veto).
Verify this assumption still holds at Gate 1 before building on it.

### 2.4 Current decision code (what you are extending)

`apps/platform/temporal-worker-autoscaler/go/internal/scaling/scaling.go`, `HPAScaler.Decide` (line 107).

Existing behavior, in order:
1. `raw = ceil(backlog / targetPerReplica)`, floored to 1 when backlog > 0, clamped to `[min, max]`.
2. **Tolerance deadband, `scaling.go:126-133`:** if `Current > 0` and the backlog/current ratio is within `TolerancePercent` of target, it **returns HOLD immediately** (early return). This is the critical integration point, see §5.2.
3. Panic detection at `PanicThresholdPercent` (default 200).
4. Downscale stabilization = max-over-window (default 120s).
5. Upscale stabilization = min-over-window (default 0s = react immediately up), bypassed on panic.
6. Step clamp (`MaxScaleDownStep` default 1; `MaxScaleUpStep` default 0 = unlimited).
7. Final clamp to `[min, max]`.

`Input` struct (`scaling.go:30-38`) has no slot field today.
Poll interval 15s, in-process rate limiter `rate.Every(750ms)` burst 1, `MaxConcurrentReconciles: 1`, leader-elected singleton.
The controller does **not** query Prometheus today; it only exposes its own `/metrics`.

---

## 3. Design overview

Backlog stays the primary driver and keeps its exact current path.
Slot utilization enters as a bounded modifier on the decision, per version, sourced from Prometheus at reconcile time.

```
per version, every 15s reconcile:
  backlog        = DescribeWorkerDeploymentVersion(...)          # unchanged, Cloud, primary
  slot_up_hint   = PromQL: max_over_time(slot_util[1m])          # leading, for OR-up
  slot_idle_hint = PromQL: avg_over_time(slot_util[2m])          # lagging, for AND-down veto
  decision       = HPAScaler.Decide(backlog, slot hints, ...)    # combined asymmetrically
```

Two Prometheus reads per version per reconcile (one instant query returning both windows, or two cheap instant queries).
Zero added Cloud calls.
Both slot reads are advisory: if Prometheus is unavailable or returns no series for that version, the decision **falls back to backlog-only** (fail open, see §5.4).

---

## 4. Design decisions (with rationale)

These are my recommended calls.
Items marked **[CONFIRM]** are worth a nod from the reviewer before Gate 2, but I have chosen a sensible default for each so work is not blocked.

**Decision 1 - per-version granularity via a new recording rule. [CONFIRM]**
Add a *new* recording rule `temporal_slot_utilization:by_build` (colon name = "this is a derived, wiring-coupled rule", distinct from the dashboard rule) grouped by `(namespace, worker_type, task_queue, temporal_io_build_id)`.
Keep the existing `temporal_slot_utilization` untouched for dashboards.
Rationale: the scaling decision is per version (per build ID); a fleet-blended ratio across versions is wrong during a canary/rollout when old and new versions have very different pressure.
Cardinality cost is ~1 to 3 live versions, negligible.

**Decision 2 - map `queueType` to the correct slot `worker_type`. [CONFIRM]**
A workflow CR must read workflow-task slot utilization; an activity CR must read activity-task slot utilization.
The recording rule carries `worker_type`.
Verify the actual label values present in our data at Gate 1 (likely `WorkflowWorker` / `ActivityWorker`, but confirm live, do not assume) and build the `queueType -> worker_type` map from verified values.

**Decision 3 - pin worker scrape interval to 30s AND pin the recording-rule group eval interval to 30s.**
Currently the scrape is implicit (~1m) and the rule group eval inherits Prometheus' global `evaluation_interval` (default 1m).
Both must be pinned, and the rule eval matters MORE than the scrape (see below).

Pin scrape to 30s: gives ~4 samples in a 2m down window and ~2 in a 1m up window, enough for a secondary gate, same convention as our Cloud endpoint.
Not 15s: see the scale-up freshness reasoning next; matching the 15s backlog poll buys nothing.

Pin the `temporal-worker` (or new) rule group `interval: 30s` in `prometheus.yaml`.
Rationale: the derived `:by_build` series is only as fresh as the rule recomputes it. At the default 1m eval, a 30s scrape is wasted because the ratio is only recalculated every 60s.
Alternative that removes rule-eval lag entirely: have the controller query the RAW `temporal_worker_task_slots_used/available` gauges and compute the ratio inside the instant query (`sum(max_over_time(used[1m])) / clamp_min(sum(used)+sum(available),1)`). Either is acceptable; pinning the rule interval is simpler, querying raw is freshest. Pick one at Gate 2 and state which.

**Why NOT match the 15s backlog poll (scale-up freshness reasoning).**
Slot-util-for-scale-up is valuable in exactly one case, and it is not a fast case:
- Transient spike: backlog already owns this (live 15s gRPC poll + 200% panic bypass). A *scraped* metric structurally lags a *live* read, so slot util can never beat backlog to a spike regardless of scrape rate. Matching 15s would not change that.
- Sustained saturation with flat backlog (workers pegged near 100% slots, queue not growing because throughput matches arrival, zero headroom): backlog says "fine," slot util says "no headroom." This is the only thing slot-up sees that backlog cannot, and it is a slow-moving *condition*, not a spike, so 30s freshness is ample.
Conclusion: slot-up detects a sustained no-headroom condition; it is not and cannot be a faster spike detector than backlog. So 30s scrape + 30s rule eval is the right freshness. Chasing 15s creates a false expectation that slot "leads" backlog when by construction a scraped-then-evaluated metric lags a live read.

**Decision 4 - smooth in Prometheus, not in the controller.**
Use `max_over_time(...[1m])` for the up hint and `avg_over_time(...[2m])` for the down hint.
Do NOT add a controller-side ring buffer.
Rationale: the existing 120s down-stabilization already smooths the *recommendation*, so a controller-side history of the slot metric would be double-damping for no benefit and more code.
Prometheus does the metric smoothing; the existing decision machinery does the recommendation smoothing.
This is the main "don't over-engineer" call.

**Decision 5 - CRD fields with HPA-like defaults, with up and down controlled INDEPENDENTLY.**
Split the enable into two flags, not one, because scale-up use and scale-down use are separable concerns and separating them is what makes future scale-to-zero configurable (Decision 7):
- `SlotScaleUpEnabled` default false - turns on the OR-up term (§5.2).
- `SlotScaleDownGateEnabled` default false - turns on the AND-down veto / safe-to-shrink gate (§5.3).
- `TargetSlotUtilizationPercent` default 75 - the up target (relieve pressure above this).
- `ScaleDownSlotUtilizationPercent` default 25 - the idle gate (only allow shrink below this).
- `SlotUpWindow` / `SlotDownWindow` as durations, default `1m` / `2m`. Prefer fields over constants for expressiveness.

Independent flags let a CR express any of: pure backlog (both off, identical to today), backlog + up-hint only, backlog + down-gate only (the safe-to-shrink / future safe-to-zero posture), or both.

**Decision 6 - fail open toward backlog.**
Slot util is an enhancement. Missing/stale slot data must never freeze scaling. See §5.4.

**Decision 7 - do not block future scale-to-zero (design now, do not enable now).**
We are not implementing scale-to-zero in this work, but the config and logic must not paint us into a corner, because the AND-down gate we are building IS the core of the deferred safe-to-zero guard (ADR-0023: safe-to-zero = backlog zero AND slots idle AND pollers alive).
Concretely:
- Do NOT hardcode `min >= 1` anywhere in the slot logic or the new math. `minReplicas` must remain free to be 0. Any `current > 0` guards must be there for divide-by-zero safety only, never to enforce a floor of 1.
- The down-veto (`desired = max(desired, current)` when slots busy, §5.3) already extends cleanly to the 1 -> 0 boundary: at `current = 1`, if slots are idle and backlog is zero and `min = 0`, it permits 0; if slots are busy, it holds at 1. Verify this boundary explicitly in tests (§ Phase 2).
- Cold start from zero stays backlog-driven: at `current = 0` there are no pods and thus no slot series, so the up path must rely on backlog's existing "backlog > 0 -> floor 1" rule (`scaling.go:118-119`). Do not let a NaN slot hint block that.
- Leave a seam for the third input (poller liveness) without building it: the down-gate should be structured as one composable predicate (`slotsIdle`) that a later change can AND with `pollersAlive`, not as inline logic that would have to be rewritten. A short code comment naming the future safe-to-zero composite is enough.
So the end state of THIS work: `minReplicas` default stays 1 on the orders CRs (we do not flip anyone to 0), but nothing in the code or CRD prevents setting it to 0 later with the down-gate as the safety.

---

## 5. The decision math (exact)

### 5.1 Inputs

Extend `scaling.Input` with:
```go
SlotUpHint       float64 // max_over_time(slot_util[upWindow]);   NaN if unavailable
SlotIdleHint     float64 // avg_over_time(slot_util[downWindow]); NaN if unavailable
SlotUpOn         bool    // per-CR: OR-up term enabled
SlotDownGateOn   bool    // per-CR: AND-down veto enabled
TargetSlotUtil   float64 // e.g. 0.75
IdleSlotUtil     float64 // e.g. 0.25
```
Use `NaN` (not 0.0) for "no data", because 0.0 is a legitimate idle reading and must not be confused with "unknown". Guard every use with `math.IsNaN`.
The two enables are independent (Decision 5): the OR-up term keys off `SlotUpOn`, the down-veto keys off `SlotDownGateOn`. When both are false, behavior is byte-identical to today.

### 5.2 Scale-up (OR) - and the deadband trap

Compute a slot-driven desired independent of backlog:
```
desired_slots = ceil(current * slot_up_hint / target_slot_util)   // only when SlotUpOn and !NaN and current>0
```
Example: current=3, up hint 0.90, target 0.75 -> ceil(3 * 1.2) = 4 (add one to relieve pressure).

The OR is: `desired_up = max(desired_backlog_raw, desired_slots)`.

**Critical:** the existing deadband early-return (`scaling.go:126-133`) fires when backlog is within tolerance and returns HOLD *before any other logic runs*.
If slot logic sits after it, the OR-up can never fire in exactly the case it exists for: quiet backlog, saturated slots.
So the slot-up term MUST be evaluated **inside/around the deadband branch**: even when backlog is within tolerance, if `desired_slots > current`, do not take the early HOLD return; fall through with `raw = max(raw, desired_slots)`.
Preserve the deadband hold for the case where slots are also not saturated.

After computing the combined `raw`, run it through the **existing** panic / up-stabilization / step-clamp / clamp path unchanged.
The slot-driven increase inherits the same dampers as a backlog-driven increase (this is deliberate; do not bypass them).

### 5.3 Scale-down (AND / veto)

The AND is a veto layered on the existing down path.
After the existing logic computes a `desired < current` (backlog is low enough to shrink), apply:
```
if SlotDownGateOn and !IsNaN(slot_idle_hint) and slot_idle_hint >= idle_slot_util:
    // slots still busy -> veto the shrink, hold current
    desired = max(desired, current)   // i.e. do not go below current this cycle
```
Place the veto so it composes with, and does not defeat, the 120s down-stabilization.
Order: compute backlog-driven desired -> down-stabilization (existing) -> **slot down-veto** -> step clamp -> final clamp.
Record the (possibly vetoed) value into the stabilization history so the window reflects reality.

Rationale: backlog draining to zero does not mean the work is done; long activities keep slots occupied after the queue empties.
Removing a pod mid-activity forces retries/replays elsewhere.
The veto prevents that.

### 5.4 Fail-open rules (both directions)

- Prometheus unreachable, query error, or no series for this `(task_queue, build_id, worker_type)`: set both hints to `NaN`.
- With `NaN` up hint: no slot-driven up term; backlog decides up. (Safe: backlog still scales up.)
- With `NaN` idle hint: **do not veto**; honor the backlog-driven down decision. (Safe: backlog is the primary safety and it already said shrink; a broken Prometheus must not pin the fleet at max forever.)
- Emit a controller metric and a debug log whenever a slot query fails or returns empty, so the fail-open path is observable and we notice a chronically broken signal.

---

## 6. Implementation work, by phase and STOP gate

Do the phases in order.
At each **GATE**, stop and hand the requested evidence back to the reviewer.
Do not proceed past a gate without approval.

### Phase 0 - Prometheus signal (recording rule + scrape)

- Add recording rule `temporal_slot_utilization:by_build` grouped by `(namespace, worker_type, task_queue, temporal_io_build_id)`, same used/(used+available) shape as the existing rule (aggregate the two gauges separately before dividing, per the existing rule's comment).
- Pin the worker pod scrape interval to 30s (explicitly, in the Prometheus scrape config / chart values, not relying on the chart default).
- Do NOT touch the existing `temporal_slot_utilization` rule.

**GATE 1 (signal is real).** Bring the stack up per repo rules (console first: `just up-cloud-kind` then `just platform-up`), let workers run, then hand back:
1. Output of `temporal_slot_utilization:by_build` from the Prometheus MCP or API, showing distinct series per `temporal_io_build_id` with sane values in `[0,1]`.
2. The actual `worker_type` label values present (to build the `queueType -> worker_type` map, Decision 2).
3. Confirmation the orders workers are still fixed-size suppliers (Decision / §2.3), by quoting the current worker `main.py` options.
4. A sanity check: run a load burst and confirm the ratio moves toward 1.0 under load and back down when idle.
This gate exists because everything downstream is wrong if the signal is garbage (wrong labels, resource-based suppliers, or a denominator that does not move).

### Phase 1 - CRD fields + Prometheus client wiring (no decision logic yet)

- Add CRD fields (Decision 5) to `api/v1alpha1/workerautoscaler_types.go` with defaults and validation; regenerate CRD (`config/crd/bases/...`).
- Add a Prometheus client to the controller: new config `PROMETHEUS_URL` (in `internal/config/config.go`), in-cluster service DNS default; a small `internal/promsource` (or similar) that runs the two instant queries and returns `(upHint, idleHint float64, err)` with `NaN` on missing series.
- Wire it into the reconcile loop to populate the new `Input` fields, but have `Decide` **ignore them for now** (pure plumbing).
- Extend the controller's own `/metrics` with: slot hint values read, slot-query failures total, slot-veto events total, slot-driven-up events total.

**GATE 2 (plumbing).** Hand back: the CRD diff + regenerated CRD manifest, the Prometheus client code + its unit test (including the NaN-on-missing path), and a live log line per reconcile showing the two hints being read per version. Also confirm the `[CONFIRM]` decisions (1, 2, 5) with the reviewer here.

### Phase 2 - decision logic + unit tests

- Implement §5.2 (OR-up including the deadband-trap fix), §5.3 (AND-down veto), §5.4 (fail-open) in `HPAScaler.Decide`.
- Keep backlog-only behavior **byte-identical** when both `SlotUpOn` and `SlotDownGateOn` are false, or both hints are `NaN` (regression safety).
- Add table-driven tests in `scaling_test.go` covering at minimum:
  - quiet backlog + saturated slots -> scales up (proves the deadband trap is fixed).
  - low backlog + busy slots -> down is vetoed (holds).
  - low backlog + idle slots -> scales down (AND satisfied).
  - both flags false -> identical to current behavior (byte-for-byte regression check).
  - `NaN` up hint -> backlog-only up; `NaN` idle hint -> down not vetoed.
  - slot-driven up still obeys step clamp and up-stabilization.
  - scale-to-zero seam: `min = 0`, backlog 0, slots idle -> permits 0; `min = 0`, backlog 0, slots busy -> holds at current (down-gate is the safe-to-shrink guard at the 1->0 boundary).
  - up flag on / down flag off (and vice versa) -> only the enabled direction is affected.

**GATE 3 (logic).** Hand back the `scaling.go` diff and the new test cases with `go test ./...` output. Reviewer checks the math and the fail-open semantics before anything is deployed.

### Phase 3 - chart, deploy, live verification

Follow the repo's chart/redeploy discipline exactly:
- Bump `deploy/charts/temporal-worker-autoscaler/Chart.yaml` `version` and `appVersion`, AND the matching `autoscaler_chart_version` default in `deploy/terraform/layers/cluster/variables.tf`.
- Publish the chart as its own step, confirm it landed, THEN `terraform apply` (never chain publish `&&` apply).
- Rebuild only the autoscaler image; pass current worker digests through via `TF_VAR_worker_image_digests` so workers do not churn versions.
- Enable `SlotScaleUpEnabled: true` and `SlotScaleDownGateEnabled: true` on the orders workflow CR first (single worker) as a canary, not all three at once.
- Verify the RENDERED live manifest and pod env, not just the source defaults (a chart env var overrides an in-code default).

**GATE 4 (live).** With the console up throughout, hand back:
1. Live pod env showing `PROMETHEUS_URL` and the rendered CR fields.
2. A real observed scale event driven by the slot signal (grafana/console screenshot or controller logs): ideally a slot-vetoed scale-down (backlog low, slots busy, held) and a slot-driven scale-up (backlog quiet, slots saturated, +1).
3. Confirmation Cloud API load did not rise (the slot path adds zero `DescribeWorkerDeploymentVersion` calls; backlog poll cadence unchanged).
Keep live Temporal Cloud executions minimal per repo rule (one load burst is enough to move the signal; do not fan out).

### After Gate 4 - review + merge

- Run the Temporal-aware review (temporal-architect workflow review), not generic `/code-review`, because this diff is determinism/scaling-sensitive. Fold findings into the branch.
- One focused commit, informative PR body (what, why, how verified, which review ran), rebase-merge to main, sync local main. Commit subject per CONTRIBUTING.md (imperative, sentence case, no Conventional Commits prefix), e.g. `Add slot-utilization gate to worker autoscaler scaling`.
- Add/adjust an ADR note under ADR-0023 recording that slot utilization is now a live second signal (asymmetric OR-up / AND-down) and that scale-to-zero remains deferred.

---

## 7. Scope guards (do NOT do these)

- DO NOT enable or implement scale-to-zero in this work. Leave `minReplicas` default 1 on the orders CRs; do not flip anyone to 0. BUT do NOT block it either (Decision 7): no hardcoded `min >= 1` in the new logic, down-gate written as a composable `slotsIdle` predicate.
- Do NOT add a controller-side ring buffer / slot history (Decision 4).
- Do NOT modify the existing `temporal_slot_utilization` dashboard rule.
- Do NOT change the backlog poll cadence, the rate limiter, or add any new Temporal Cloud call.
- Do NOT blend backlog and slot util into one number. They combine at the decision layer (max for up, veto for down), never as a summed/averaged metric.
- Do NOT drop the worker scrape below 30s "to be safe". It is a secondary signal.
- Do NOT change worker slot-supplier config as part of this work.

---

## 8. Why this is a good production recommendation (customer-facing framing)

Backlog is a **lagging, server-side** demand signal: authoritative but rate-limited (shared `FrontendGlobalWorkerDeploymentReadRPS`, ~50/namespace) and only fresh via live gRPC.
Slot utilization is a **leading, worker-local** saturation signal: free to read, no Cloud quota, and it moves before the queue does when tasks are long-running.

The mature autoscaling pattern is to drive on the authoritative signal and use the cheap local signal to sharpen the edges:
- OR on scale-up so saturation is caught before backlog reflects it (protects latency).
- AND on scale-down so capacity is only removed when the queue is drained AND the workers are actually idle (protects in-flight work).

The AND-down is specifically what a stock HPA cannot express (multi-metric HPA only takes the max desired, which is OR-only).
Recommending the custom controller is justified precisely when a customer needs this asymmetry or seconds-level actuation; when their reaction SLO is looser (minutes), ADR-0023's guidance to use HPA + prometheus-adapter on `temporal_slot_utilization` still stands.
This work is also the first building block of the deferred composite safe-to-zero guard.

---

## 9. Open confirmations for the reviewer (answer at Gate 1/2, non-blocking)

1. Per-version recording rule name `temporal_slot_utilization:by_build` acceptable? (Decision 1)
2. Confirm the `queueType -> worker_type` mapping from the live label values found at Gate 1. (Decision 2)
3. Threshold defaults 75% up / 25% down idle acceptable, or tune? (Decision 5)
4. Two independent flags (`SlotScaleUpEnabled` / `SlotScaleDownGateEnabled`) vs a single enum - confirm the two-flag shape. (Decision 5 / 7)
5. Freshness: pin the recording-rule group eval to 30s, or query the raw gauges in the controller (no rule-eval lag)? (Decision 3)
6. Windows as CRD duration fields (recommended) confirmed. (Decision 5)
