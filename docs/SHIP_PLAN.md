# 30-60 Day Temporal Cloud Rollout Plan

**Customer:** Ziggymart Systems (Fictional)
**Goal:** Migrate the core "Retail Order Processing" workflow to Temporal Cloud with zero downtime and high confidence, establishing a pattern for future workloads.

> **Note — repo vs. this plan.** A real customer rollout *should* use environment-separated
> namespaces (e.g. `ziggymart-staging` / `ziggymart-prod`), as below. The **repo itself**
> deliberately models a single production-shaped namespace per domain (`ziggymart`) with no
> nonprod/prod split (ADR-0017) — the env axis demonstrates no Temporal feature, and the
> workbench is disposable. This plan is customer guidance, not a description of the repo.

## Phase 1: Foundation & Observability (Days 1-14)

**Objective:** Establish a secure, observable, and multi-tenant foundation in Temporal Cloud.

* **Week 1: Infrastructure as Code & Security**
  * Provision Temporal Cloud Namespace (`ziggymart-prod`, `ziggymart-staging`).
  * Issue mTLS certificates and API keys for worker identity.
  * Deploy a **Codec Server** inside Ziggymart's VPC to ensure all payload data (PII, order details) is encrypted before transit to Temporal Cloud.
  * Integrate Temporal UI SSO with Ziggymart's existing Identity Provider (Okta/Entra).

* **Week 2: Observability & CI/CD**
  * Wire the Temporal Python SDK to export OpenTelemetry metrics (workflow duration, activity latency, error rates) to Ziggymart's existing Datadog/Prometheus stack.
  * Create Datadog dashboards mirroring current business KPIs (Orders Processed/Min, Error Rates).
  * Update CI/CD pipelines to build and deploy Temporal Worker containers alongside the existing API services.
    * **In this repo today:** the build+gate logic lives in `just ci` → `poe ci`
      (`lint → typecheck → test → build worker images → push to the registry`), with images tagged
      by git SHA so each is immutable and maps to a Worker Controller Build ID. CD is ArgoCD + the
      Worker Controller (Build-ID ramps). A GitHub Actions workflow would be a thin wrapper calling
      `poe ci` (identical steps local and remote; runnable locally via `act`) — deferred.
    * **Next workstream — the Temporal-specific gate:** a replay/NDE suite that runs recorded
      production/staging histories through the SDK `Replayer` against the new worker code and fails
      the build on non-determinism, wired into `poe ci`'s `test` step. The canonical "catch NDEs
      before deploy" control; it pairs with Worker Versioning (gate at build; ship a new Build ID
      when a change is genuinely incompatible). Not yet built.

## Phase 2: Shadow Mode & Verification (Days 15-30)

**Objective:** Prove the Temporal workflow handles production data correctly without impacting actual customers.

* **Week 3: Dual-Write / Shadow Workflows**
  * The existing legacy order service remains the authoritative system of record.
  * Introduce an event hook (e.g., via existing Kafka topics) that triggers the new Temporal `OrderWorkflow` asynchronously for every new order.
  * The Temporal workflow executes in "Shadow Mode" — activities are executed, but their external side-effects (payment charges, shipping labels) are directed to mock/sandbox endpoints.
  * Database writes from Temporal are directed to a parallel shadow table.

* **Week 4: Audit & Tuning**
  * Run automated daily diffs comparing the legacy system's final order state against the Temporal shadow state.
  * Tune Retry Policies and Activity Timeouts based on observed P99 latencies from the shadow runs.
  * Conduct a Game Day: simulate network partitions and database failovers on the Temporal workers to verify correct recovery behavior.

## Phase 3: Phased Cutover (Days 31-45)

**Objective:** Safely shift authoritative execution to Temporal.

* **Week 5: The 1% Canary**
  * Introduce a routing feature flag (e.g., LaunchDarkly).
  * Route 1% of live order traffic to the Temporal workflow as the authoritative execution path.
  * Legacy system handles the remaining 99%.
  * Monitor error budgets and support tickets closely.

* **Week 6: Scaling Up (10% -> 50% -> 100%)**
  * If the 1% canary holds SLOs for 48 hours, dial up to 10%, then 50%.
  * At 100%, the legacy orchestration logic is fully deprecated.
  * The Temporal workflow is now the sole orchestrator, writing durable state to the existing business Postgres database.

## Phase 4: Expansion & Enablement (Days 46-60)

**Objective:** Institutionalize Temporal knowledge and onboard the next use case.

* **Week 7: Runbooks & Training**
  * Finalize SRE runbooks for debugging stuck workflows using the Temporal UI event history.
  * Conduct an internal engineering enablement session: "How to build your first Temporal Workflow at Ziggymart."
  * Document the Worker Versioning strategy for safe, backward-compatible code updates.

* **Week 8: The Next Workload**
  * Identify the second highest-pain workload (e.g., Subscription Billing or Returns Processing).
  * Design task queue topology for the second workload (e.g., spinning up a separate `billing-task-queue` managed by the Billing team).
  * Begin Phase 1 for the new workload.