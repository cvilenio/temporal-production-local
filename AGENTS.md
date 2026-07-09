# Agent instructions

## Commit Convention (MUST FOLLOW)
Authoritative rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Any agent writing a
commit or PR title in this repo MUST obey these. Summary:

- **Imperative, sentence case.** "Add X", not "Added X" / "adds X" / "add x".
- **No trailing period.** Subject under ~72 chars.
- **NO Conventional Commits types.** Do not prefix with `feat:` / `fix:` / `chore:` /
  `refactor:` etc. This repo mirrors `temporalio/sdk-python`, which does not use them.
- **Optional lowercase scope prefix** only when it clarifies: `ci:`, `docs:`, or a
  component like `orders:` / `console:`. Loose, not required.
- Body optional; wrap ~72 cols; explain *why*, not what.

Examples — good: `Add deterministic order ID derived from idempotency key`,
`orders: Narrow retry policy on payment activity`, `ci: Drop macos-intel from CI`.
Bad: `feat: add order id`, `Added order id.`, `update stuff`.

# Commit & push workflow — default to a PR (MUST)

When the user asks to **commit and push**, the default flow is a pull request — the PR is
the history-recording artifact for this repo (solo-maintained; PRs are the changelog of
*why*, not just *what*). Do NOT push straight to `main`.

Default flow (no extra confirmation needed — this is the standing instruction):

1. Branch off `main` with a short descriptive name (e.g. `clickhouse-log-store-hardening`).
2. Commit using the subject rules above (CONTRIBUTING.md). Keep the branch to one focused,
   well-formed commit — on rebase-merge it replays onto `main` as-is, so the commit *is*
   the permanent history (squash into one before merging if you made several).
3. Push the branch and open a PR with `gh`, with an **informative body**: summary of the
   change, *why*, the key changes, and how it was verified. This body is the artifact —
   make it worth reading later.
4. **Rebase-merge** to `main` (`gh pr merge <n> --rebase --delete-branch`) for a linear
   history with no merge commits. Then sync local `main` (`git checkout main &&
   git pull --ff-only`).

Notes:
- **Rebase-merge keeps history linear** and does NOT append the PR number `(#n)` to the
  subject (unlike squash) — so the commit subject must already be the final, clean one.
- **Self-approval is impossible on GitHub** (you cannot approve your own PR). On this
  solo repo no approval is required; **merging is the operative step** — do not block on
  a review.
- **Skip the PR only when the user explicitly says so** for that request (e.g. "commit
  and push directly, no PR"). Then commit + push as they specify.
- All commit/PR titles still obey the Commit Convention above.

# Verify before merge — static review gates the PR (MUST)

Live testing proves a change *runs*; it does not prove the paths you didn't run are correct.
This matters here specifically: the Cloud footprint rule below defaults to one happy-path execution, so failure branches, retry behavior, and replay/non-determinism are structurally under-exercised by live runs.
A static review on the diff is the cheap complement that covers exactly what the live budget skips.

Run the review as an **independent** pass, never as author self-review.
`/code-review` fans the work out to fresh subagents that read only the PR diff (plus git history and this file) — they never inherit the session that wrote the code, so the review is genuinely independent.
For that reason there is no need to open a separate Claude Code session; invoking the command from the implementing session is equivalent.

When to run it (scale the gate to the diff — this is a cost decision, not a blanket step):

- **Skip the review call** for trivial diffs: docs, comments, `values.yaml`/config, chart-version bumps, generated files.
  Live smoke (where relevant) is enough.
- **Run `/code-review <pr>`** for any diff with real logic — after opening the PR, before the rebase-merge.
  Fold the findings into the branch, then merge.
  The command posts its findings as a PR comment, so the review lives next to the *why* in the PR artifact.
- **Prefer the Temporal-aware review** (temporal-architect workflow review) over generic `/code-review` when the diff touches workflow or activity code, retry/timeout policy, versioning/patch gates, or anything determinism-sensitive — generic review does not deeply know those traps.

State in the PR body which review ran (or that the diff was trivial and the review was skipped), so "how it was verified" reflects both the live check and the static pass.

# Python app layout — settings / dependencies / main (+ routes/) (MUST)

Applies to **Python** deployable apps under `/apps` (ADR-0022). Other languages are **not
yet hardened** — do not assume this shape for a future Go/TS/Java app; revisit when one lands.

Every mature Python app (i.e. not an unused scaffold/placeholder) uses the same module layout
so apps read the same way:

- **`settings.py`** — env → a typed `Settings` (pydantic-settings). Kernel apps compose the
  `appkit` field-group mixins (`TemporalConnectionSettings` / `WorkerTuningSettings` /
  `TelemetrySettings`) + their own deltas; standalone apps (e.g. mock-api) keep a small
  self-contained `Settings`.
- **`dependencies.py`** — the composition root: the `dependency-injector` `Container` (provider
  *lifetimes* are this app's policy) plus, for web apps, the FastAPI dependency accessors. Named
  `dependencies.py` — **not** `container.py` (DI jargon) or `composition.py` (vague). A larger
  package app (the console) may instead spread this across purpose modules (`db.py`,
  `order_client.py`) — that's fine; the rule is the *three roles*, not exactly three files.
- **`main.py`** — entrypoint, lifecycle/lifespan, and (web apps) request middleware. Keep route
  handlers OUT of `main.py`.

**Route discipline (web apps).** Routes live in a `routes/` package, one module per **base path
prefix**, and each module's `APIRouter(prefix="…")` declares that base once so endpoints are
relative — never repeat or mismatch the prefix in individual paths. Group by URL prefix (e.g.
orders-api: `/orders` public, `/internal/orders` workflow callbacks, `/admin`), not by loose
"surface". `main.py` includes each router; `/health` may stay top-level.

Contracts that cross apps (paths a caller hardcodes, the data converter, queue/namespace/SA keys)
are consumed, not re-decided — change a path only by updating the API and its in-repo callers
together (see ADR-0022 / ADR-0021).

# Live kind testing — bring the platform-console up FIRST (MUST)

The `platform-console` (http://localhost:8086) is the operator's single live window onto a
run. Before doing **any** live testing on the kind cluster — `just cluster-up`, running or
resetting orders, mutating cluster state — the console MUST already be up so a human can
follow along in real time.

- **Start it first:** `just host-up` (detached host plane; tail logs with `just host-logs`),
  then `just headlamp-reload`. The console is boot-resilient (ADR-0015 / `console/.../db.py`):
  it boots Healthy with the entire kind side absent and self-heals as the cluster appears, so it
  is always safe to start before `just cluster-up`. One-shot alternative: `just platform-up`
  brings host up first, then the kind side.
- **Enforced, not just documented:** `just preflight` (→ `poe preflight-console`) probes
  `:8086/healthz` and fails with how-to-fix if the console is down. `just cluster-up`,
  `just workloads-up`, and `just orders-db-reset` run it as a gate, so a blind live test
  aborts before it starts.
- **Off-path agents:** if you mutate the cluster outside those recipes (e.g. raw `kubectl`,
  `terraform apply` on the cluster layer), run `just preflight` yourself first.

**Local is low-latency - never blind-wait; verify progress out-of-band and fail fast (MUST).**
Everything on the kind + Docker + Terraform side runs on this machine, so real work shows
observable movement within *seconds*, not minutes: `docker compose up` transitions containers
to `Created`/`Up` within ~10-15s, kind pods start almost immediately, and a Terraform step
either advances or errors. The failure mode this rule exists to kill: firing a bring-up, then
sitting on a static sleep or a blind `curl :8086/healthz` loop "waiting longer" for something
that is already dead. If nothing is moving, the step is stuck or the process died - diagnose it,
do not extend the wait.

The distinction that matters: a recipe's **stdout** is a bad readiness signal (it goes quiet
for long stretches while work genuinely happens, and "still running" looks identical to
"stuck"), but the **system's observable state** is an excellent, near-instant progress signal.
So:

- **Confirm liveness first, within the first ~10-15s.** Right after kicking off a bring-up,
  check that it is actually doing something out-of-band - `docker ps` / `docker ps -a` for
  containers appearing and transitioning state, `pgrep -fl 'just|docker compose'` for the
  process still being alive, `kubectl get pods -A` for pods being created. Seeing containers
  come online is the proof the command works at all; an empty `docker ps` seconds in means the
  command never got to `docker compose up` (a dead background process), not "still building."
- **Poll observable STATE on a tight cadence - that is not the same as tailing a log.** Re-run
  the cheap state probes (`docker ps`, `kubectl get pods`, health endpoints) every few seconds
  until they satisfy or a short, aggressive timeout trips. Do NOT `tail` the recipe's stdout /
  a backgrounded task's `.output` file, and do NOT guess readiness from elapsed time - those are
  the banned blind signals. If the tool call auto-backgrounds the command, still probe state
  yourself for early liveness rather than only awaiting its completion notification.
- **On zero progress, stop and diagnose - do not wait longer.** No container movement in the
  first window => inspect immediately: `docker ps -a` for `Exited`/`Created`-stuck, `docker logs
  <svc> --tail 50`, the process exit code. Default suspicion is a dead/orphaned background
  process or a genuine error, not patience owed.
- **Validate final readiness against the signals a human would trust,** not the command's exit
  alone: `curl :8086/healthz` (console), `curl :3000/api/health` (Grafana), `docker ps` for
  `Up ... (healthy)` on the containers you need, `kubectl get pods -A` for zero
  non-Running/Completed pods. Prefer `just preflight` where it exists (console gate) - it is the
  same probe `cluster-up` already trusts.
- **The one exception is the network to Temporal Cloud.** Anything crossing the internet -
  starting Cloud workflows, `tcld`, scraping the Cloud metrics endpoint - has real, non-local
  latency, so a patient wait there is legitimate. This whole low-latency assumption applies to
  the local kind / Docker / Terraform substrate only; do not carry the aggressive timeouts onto
  Cloud calls.

This generalizes beyond `host-up` / `cluster-up`: any local `docker`, `kubectl`,
or `terraform` step follows the same discipline - verify it is progressing via observable state,
fast, and fail fast when it is not.

**Known cold-build trap (pre-build to sidestep it).** On a cold `docker compose up --build`,
Docker Desktop's parallel bake can hang exporting the `platform-console` image, and a
backgrounded `just host-up` can die at that point *before* `docker compose up` ever
runs - so no containers are created and healthz never comes up (the exact "waiting on a corpse"
trap above). If a bring-up shows no containers within the first window on a cold build,
pre-build the images as their own step first - `docker compose build platform-console mock-api
codec-server` - then re-run the recipe; containers then come up in ~30s. Relatedly, a detached
compose that dies before its `depends_on: <svc> service_healthy` chain clears can leave a
late-ordered service (e.g. `otel-collector`, gated on `clickhouse` healthy) stale/`Exited`
while the rest are `Up` - verify the *full* expected container set with `docker ps`, not just
the console. Tail host logs with `just host-logs` when debugging.

**Get the environment to known-good BEFORE improvising around it.**
Live testing assumes a running, healthy kind cluster.
After a host or Docker restart the cluster is often stopped or half-broken, and the failure looks like something else (e.g. ArgoCD 502 pulling charts because the registry EndpointSlice went stale; see [`docs/runbooks/kind-restart-registry-recovery.md`](docs/runbooks/kind-restart-registry-recovery.md)).
Do not reverse-engineer a fix by hand-patching cluster resources.
This is a local sandbox with no production load: reconciling to a known-good state is cheap and always safe, so prefer the deterministic recovery recipe over clever surgery.

- If the cluster is stopped, resume it with `just cluster-start` (it restarts the registry + nodes and self-heals the registry EndpointSlice); if it does not exist, `just cluster-up` (or `just kind-up` for an empty substrate only).
- For any other stale or unknown cluster state (including a running cluster with a stale registry EndpointSlice), run `just kind-ready` first — it repairs in place without restarting when nodes are already up.
- Only after the cluster is Healthy (ArgoCD Applications Synced/Healthy, zero non-Running pods) proceed with the test.

**Preserve the current backend and image digests on a surgical redeploy.**
When redeploying one component (per the chart discipline below), do not fight the environment's current state:

- Read and preserve the backend with `terraform -chdir=deploy/terraform/layers/cluster output -raw temporal_backend`, and do NOT export `TF_VAR_temporal_backend` / `TF_VAR_oss_server_enabled` unless you are *intentionally* switching. Forcing a backend flips OSS to Cloud (or back) and churns every worker version as a side effect.
- Capture the CURRENT worker/api image digests from the live deployments and pin them through the apply, so only the one component you changed moves. Do not hand-assemble digests from a fresh build unless you rebuilt that image.

See `docs/RUNMODES.md` for the full run-mode matrix.

# Chart + redeploy discipline — bump the version, publish before apply (MUST)

ArgoCD pulls every chart from the local OCI registry (ADR-0011) and caches by
`name:version`. This makes the version the delivery contract, and it bites in three ways
an agent editing a chart will hit. All three were live-verified during the worker-autoscaler
work.

- **Bump the chart version on ANY template change.** Edit anything under a chart's
  `templates/` (or `values.yaml`) → bump `Chart.yaml` `version`/`appVersion` **and** the
  matching `*_chart_version` default in `deploy/terraform/layers/cluster/variables.tf`. If you
  don't, ArgoCD serves the cached old chart: the Application shows **Synced/Healthy** while your
  new resources silently never render — the most confusing failure mode in this repo. See
  [`docs/adr/0011-local-oci-delivery.md`](docs/adr/0011-local-oci-delivery.md).
- **Publish the chart as its own step BEFORE `terraform apply` — never chain them.** Running
  `just chart-publish && terraform apply` in one shell means a denied/failed apply (e.g. the
  auto-mode classifier gating a protected IaC change) blocks the *whole* command, so the publish
  never runs either → ArgoCD then fails "OCI chart … `<version>` not found." Publish first,
  confirm it landed, then apply.
- **Redeploy surgically to avoid worker-version churn.** Don't run full `just cluster-up` to
  ship one component — it rebuilds the workers, producing a new image digest = a new Worker
  Deployment version the Worker Controller must poll (extra shared Worker-Deployment-Read Cloud
  load). Rebuild only the changed image and pass the *current* worker digests through to
  `terraform apply` (`TF_VAR_worker_image_digests`), per `docs/RUNMODES.md`.
- **Verify the RENDERED manifest, not just the code default.** A chart-injected env var wins over
  an app's in-code default at runtime (a code default of 15s lost to a chart `POLL_INTERVAL=3s`).
  After a deploy, check the live pod's env/args (`kubectl … -o yaml`), not the source default.

# MCP servers for agents — ClickHouse, Prometheus, Kubernetes

A project-scoped `.mcp.json` gives agents read-mostly MCP access to the three systems they
inspect most — the ClickHouse warehouse (`otel_logs` / `otel_metrics_*`), the durable Prometheus
store, and the kind cluster. All run via `uvx` (no Node/Go), point at the local **kind + Cloud**
endpoints, and need the stack up (`just host-up` → `just cluster-up`, or `just platform-up`) to return data.
Docker/Terraform/Grafana/ArgoCD were deliberately left out as net baggage for this repo. See
`docs/MCP.md` for the rationale, smoke tests, and how to add more later.

# Live Temporal Cloud testing — keep the footprint minimal (MUST)

Temporal Cloud executions are real, billable, and visible to the account. When an agent runs
a live test that starts workflows or standalone activities against Temporal Cloud, it MUST use
the **minimum number of unique executions needed to prove the change** — not a convenient round
number, not "a few extras to be safe". Validating a change is a *correctness* exercise, not a
load test.

- **Default to one.** If a single workflow execution (or one signal/update/query against it)
  demonstrates the behavior, run exactly one. Reuse that execution for follow-on assertions
  rather than starting fresh ones. Reach for a second or third only when the change genuinely
  spans distinct paths (e.g. success vs. a specific failure branch) and each path needs its own
  run to be proven.
- **Hard ceiling without asking: 5 workflow executions CUMULATIVE per task, and no batch/bulk
  operations.** This is a running total across the entire task/session, **not** a per-burst or
  per-command budget. It does not reset — pausing, starting a new tool call, splitting the work
  into rounds, or "5 now, 5 more after checking" all count against the same total and are
  explicitly disallowed as ways around it. Deliberately fragmenting runs to stay under the
  ceiling IS a violation of this rule. Below the cumulative ceiling, proceed and state the
  running count in your summary. Once the task's total reaches 5 — or for *any* loop, batch
  trigger, scheduled run, load/soak test, retry storm, or anything that fans out — STOP and ask
  permission first, even if each individual step looks small. The proposal MUST include: what you
  intend to run, the total executions/activities and why that count is the minimum, the task
  queue / namespace targeted, the rough action footprint, and how you'll clean up
  (terminate/cancel) afterward. After approval, the approved budget applies only to the work
  described — returning for more still requires a fresh ask.
- **Prefer the cheaper substrate.** If the change can be proven on the local kind + OSS path
  (or a unit/replay test) instead of Cloud, do that first; only escalate to Cloud when the
  behavior is Cloud-specific. Prefer terminating a test execution when done over leaving it
  running.
- **Determinism caveat:** never validate by spamming retries or starting many executions to
  "see if one works" — a flaky result is a signal to investigate, not to add volume.