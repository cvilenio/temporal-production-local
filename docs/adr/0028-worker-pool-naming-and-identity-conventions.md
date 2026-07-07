# ADR-0028: Worker pool naming and identity conventions

- **Status:** Accepted
- **Date:** 2026-07-07
- **Related:** ADR-0004 (Worker Deployment versioning / deployment identity).
  ADR-0026 (domain descriptors + scaffolding — where new domains are generated).
  Supersedes the ad-hoc `orders-java-activity-task-queue` / `orders-activity-java` names
  introduced in the PR #38 checkpoint (cross-language `finalize_order` on a Java worker).

## Context

A Temporal domain in this repo runs one or more **worker pools** — sets of workers that a workflow
routes work to.
The production-split pattern already gives every domain two pools: a workflow pool and an activity
pool, on two task queues (ADR-0004).
Splitting further is sometimes necessary: to run part of a domain on a different runtime, resource
profile, or SDK language.
The first such case is `orders`, where `finalize_order` runs on a dedicated Java worker (an
Independent Activity on its own task queue and Worker Deployment).

That first cross-language split named the queue `orders-java-activity-task-queue` and the deployment
`orders-activity-java`.
Reviewing it surfaced two naming mistakes worth codifying against:

1. **The SDK language leaked into the task-queue name.**
   The task-queue name is a durable routing contract — it is embedded in event history
   (`ActivityTaskScheduled`), in start options, and in every caller.
   Renaming a queue strands in-flight executions.
   The implementation language is the *least* stable attribute of a pool (a Java activity may be
   re-implemented in Go), so binding it into the routing contract guarantees a future rename.

2. **A single activity name would have been the wrong granularity too.**
   A queue serves many activities — this is why the Python pool is `orders-activity`, not
   `orders-finalize` / `orders-create-order` / etc.
   Naming a pool after one of the activities it happens to host today is equally brittle.

Separately, worker **identity** — which is what the UI shows on `ActivityTaskStarted` /
`ActivityTaskCompleted` and in the Workers view — defaulted to the SDK's `processId@hostname`.
On Kubernetes that renders as an opaque, Worker-Controller-generated pod name
(e.g. `11@orders-act-29aedb2ca0-…`), which identifies neither the domain, the pool, nor the
language.
In a polyglot fleet that is exactly the information an operator needs at a glance.

## Decision

A worker pool's **pool token** is shared across its task queue, its Worker Deployment name, and its
worker identity.
The implementation **language is appended to the deployment name and the worker identity only — it
is never part of the task-queue name.**

| Surface | Convention | Carries language? | Why |
|---|---|---|---|
| **Task queue** | `<domain>-<pool>-task-queue` | **No** | Durable routing contract (event history, start options). Encodes only the stable *why* of the routing boundary. |
| **Deployment name** | `<domain>-<pool>-<lang>` | Yes | The Worker Deployment versioning unit (ADR-0004); a deployment version is inherently single-language. |
| **Worker identity** | `<domain>-<pool>-<lang>@<host>` | Yes | Pure metadata (no migration cost); makes the fleet + language legible directly in event history. |

Token definitions:

- `<domain>` — the business domain / kernel (e.g. `orders`).
- `<pool>` — the pool's **stable purpose**.
  Reserved: `workflow` (workflow tasks) and `activity` (the domain's default/primary activity pool).
  Additional pools use a **capability** noun — a business phase (`finalization`, `payments`,
  `fulfillment`) or a resource class (`cpu`, `gpu`, `io`).
  Never the SDK language; never a single activity's name.
  One pool may host many activities.
- `<lang>` — the SDK language: `python` | `java` (extensible: `go`, `typescript`, …).
- **Token order is `domain-pool-lang`** everywhere the language appears.

The `orders` domain adopts this uniformly:

| Fleet | Task queue | Deployment | Identity |
|---|---|---|---|
| Python workflow | `orders-workflow-task-queue` | `orders-workflow-python` | `orders-workflow-python@<host>` |
| Python activity | `orders-activity-task-queue` | `orders-activity-python` | `orders-activity-python@<host>` |
| Java finalization | `orders-finalization-task-queue` | `orders-finalization-java` | `orders-finalization-java@<host>` |

Notes:

- The Python **queues** were already compliant (`domain-pool`, no language) and are unchanged —
  adding a language token to them would *violate* this convention.
- The Java pool is named for its capability, `finalization` (the order-completion phase), not for
  its SDK.
  If `finalize_order` were ever re-implemented in another language, the queue name stays correct and
  only the deployment/identity language token changes.
- **Identity derives from the deployment name**: `<deployment-name>@<host>`, taking
  `TEMPORAL_DEPLOYMENT_NAME` (injected by the Worker Controller on the versioned path) when present,
  else the appkit's configured `<domain>-<pool>-<lang>` default on the host/no-controller path.
  This keeps identity DRY and guaranteed consistent with the deployment.

The scaffolder and language templates (ADR-0026) emit this convention by default, so a newly
scaffolded domain is compliant out of the box, including the language token in the deployment name.

### Production caveat (important)

Renaming an **existing** Worker Deployment to bring it onto this convention is a versioning
migration: in-flight pinned executions are tied to the old deployment name and would be stranded.
This convention is therefore meant to be adopted **at domain-creation time**.
The one-time rename of the `orders` Python deployments (`orders-workflow` → `orders-workflow-python`,
`orders-activity` → `orders-activity-python`) and the Java fleet performed alongside this ADR is safe
**only** because this is a local, tear-downable environment with no durable in-flight workflows.
It is not a routine operation and must not be presented as one.

## Consequences

- **Legible polyglot fleets.** Event history and the Workers view show `orders-finalization-java@…`
  vs `orders-activity-python@…` directly, without needing to cross-reference a task queue to a
  language by convention.
- **Stable routing contracts.** Task-queue names never churn when a pool is re-implemented in another
  language or when its activity set changes — the language lives in the deployment/identity, which
  are safe to evolve.
- **A distinct pool needs a distinct capability.** Splitting a fleet now forces the question "what
  capability does this pool represent?" rather than defaulting to language- or activity-based names.
  If no durable capability distinction exists, that is a signal the split may be unnecessary.
- **New domains are compliant by construction** via the scaffolder; existing ones are brought on only
  when it is safe to do so (see the production caveat).
