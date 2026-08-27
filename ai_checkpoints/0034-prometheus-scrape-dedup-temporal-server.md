# 0034 — Prometheus scrape de-duplication for the OSS Temporal server

## Status

**ACCEPTED — merging via PR** (branch `prometheus-scrape-dedup`). Chart at 0.1.7.
All verification green; no outstanding work.

## The problem

Three Prometheus jobs were each scraping the same five Temporal server pods, so every
absolute server metric was stored three times and unfiltered `sum()` returned 3x the
true value. Found while building a namespace-inventory script for a customer migration:
the CLI reported 134 workflow executions for a window where PromQL reported 801 starts.

Decomposed to exactly 6x — 3x from triple scraping, 2x from summing `service_name`
frontend + history. Neither was visible from inside a single data source.

Jobs involved:

| Job | Origin | Why it matched |
|---|---|---|
| `temporal-oss` | ours, `local.scrape_oss` in `deploy/terraform/layers/cluster/applications.tf` | intentional; keyed on `prometheus.io/scrape` |
| `kubernetes-pods` | prometheus community chart **default** | same annotation, all namespaces |
| `kubernetes-service-endpoints` | prometheus community chart **default** | the headless Service's annotation |

Only the Temporal server pods were affected. `orders-api`, `orders-workers` and
`temporal-worker-autoscaler` annotate the Pod only with no annotated Service, so they
were always scraped once.

## Why it mattered beyond tidiness

76 of 77 Grafana dashboard expressions carry no job filter, and the dashboards are
dual-mode (they reference both `temporal_cloud_v1_*` and OSS server metrics). So
OSS-mode panels read 3x their Cloud-mode equivalents, which defeats the OSS/Cloud
parity this environment exists to provide. That, not dev-cluster hygiene, was the
reason to fix it at the scrape layer.

## Decisions

**Fix at the scrape layer, not in the dashboards.** Adding `job="temporal-oss"` to 76
expressions was rejected: it would break in Cloud mode where the job is
`temporal-cloud`, and every future panel would have to remember the filter. Fixing the
scrape means one series per target, so unfiltered `sum()` is simply correct. Candidate
for promotion to `docs/adr/`.

**`temporal-oss` selects on chart labels, not `prometheus.io/*` annotations.**
`app.kubernetes.io/part-of=temporal` plus `component` in
`frontend|history|matching|worker|internal-frontend`. Port `:9090` is pinned in the
relabel rule because the port annotation is gone; the listener itself stays configured
at `temporal.server.config.metrics.prometheus.listenAddress`. Excluded roles
(`admintools`, `web`, `database`) do not expose a metrics listener.

**Rejected: overriding the community chart's default `scrape_configs`** to add drop
rules. It means owning the chart defaults forever.

**Per-role `metrics.annotations.enabled` is the authoritative switch.** The upstream
template resolves it as
`dig "metrics" "annotations" "enabled" $.Values.server.metrics.annotations.enabled $serviceValues`
and upstream defaults `enabled: true` at **both** the global and every per-role level.
Because the per-role value is always present, `dig` never reaches the global fallback.
Setting only the global would not have worked.

## Gotchas worth remembering

**Ratios were never wrong.** Numerator and denominator carried the same duplication
factor, so `service_errors / service_requests` was correct throughout. Only absolute
values were affected. This is why the sheet's internal consistency checks did not catch
it — both sides came from the same inflated source. The catch required comparing a
PromQL number against a CLI number.

**Store retention is 15d, not ~2h.** Verified via
`/api/v1/status/flags → storage.tsdb.retention.time`. This was reported as ~2h twice
during the build and it is wrong. Consequence: `increase(...[30d])` still spans the
pre-fix period for fifteen days, so historical windows remain 3x for the older portion.
Instant and 5m-rate queries were clean within minutes.

**The migration-intake script keeps its `job="temporal-oss"` filter.** For historical
windows the filter selects exactly one of the three pre-fix recordings, so it stays
correct across the boundary. Do not remove it on the grounds that the infra is fixed.

## Cleanup (0.1.7)

The dead global `server.metrics.annotations.enabled: false` was removed and the NOTE
extended to record that `dig` makes the per-role block authoritative. Kept the five
per-role blocks.

Removing it caused **no pod rollout** — the rendered Deployment was byte-identical,
because the key was never read. That is a second, independent proof that it was
unreachable: a live key would have changed the output and rolled the pods.

## Review follow-ups (folded in before merge)

Static review on PR #55 surfaced four items, all applied:

- **The OSS scrape job was keyed on the wrong condition.** `prometheus_scrape_configs` was
  `is_oss ? scrape_oss : scrape_cloud`, but the OSS server's lifecycle is gated on
  `oss_server_enabled`, which is deliberately decoupled from `temporal_backend`
  (`oss_server_enabled=true` with `backend=cloud` is a supported combination, asserted by
  the layer's own validation comment). Before this change the community-default jobs
  happened to cover that case through the annotations; after it, nothing would, and the
  self-hosted-internals dashboards would go silently dark. Now `scrape_oss` is emitted
  whenever `oss_server_enabled` is true and `scrape_cloud` whenever the backend is Cloud,
  so each job follows the target that actually exists. Rendered and YAML-parsed all four
  combinations via `terraform console`: cloud+server 2 jobs, cloud-only 1, oss 1, and
  oss without a server 0 (already blocked by the existing precondition).
- **`temporal_service` was a constant.** It was sourced from `app.kubernetes.io/name`,
  which upstream sets to `include "temporal.name"` = the chart name, so it read `temporal`
  on every target rather than the role. Now sourced from `app.kubernetes.io/component`.
  Nothing consumed the label (no dashboard references it), so no downstream change.
- **The pinned `:9090` has no back-reference to the listener.** Recorded in the comment
  that it must stay in lockstep with
  `temporal.server.config.metrics.prometheus.listenAddress`; upstream hardcodes the same
  9090 in its own annotation, so the coupling is documented rather than mechanized.
- **`OBSERVABILITY.md` still described the job as "annotation-discovered"** - the exact
  mechanism removed here. Rewritten to chart-label discovery, and to the
  `oss_server_enabled` keying above.

## Verification

- `count(service_requests)` == `count(service_requests{job="temporal-oss"})`, re-checked
  after the 0.1.7 roll (50 == 50; the absolute count grows as more operations are
  exercised, the identity is the test).
  Structural proof of de-duplication; needs no traffic, unlike a before/after on an
  absolute metric (the two attempts at that ran against an idle window and proved nothing).
- All five live targets report exactly one job.
- `kubernetes-service-endpoints` 7 → 2 and `kubernetes-pods` 13 → 8, each dropping
  exactly the five Temporal pods. Non-Temporal targets unchanged.
- Pod and Service `prometheus.io/scrape` annotations absent on all five roles. Disabling
  the per-role setting strips both objects — no separate Service fix was needed.
- Visual pass over five dashboards: ~3x step down on absolute server panels at the
  rollout, ratio panels continuous, SDK panels flat. `flow-start-signal` could not be
  observed (near-zero rates) and is formally unvalidated; `durable-execution-value` is
  Cloud-metric-only and empty on OSS.
- No dashboard JSON modified. Cloud path untouched.

## Files changed

- `deploy/terraform/layers/cluster/applications.tf` — `local.scrape_oss` relabels + comment
- `deploy/charts/temporal-server/values.yaml` — per-role annotations off + comment
- `deploy/charts/temporal-server/Chart.yaml` — 0.1.5 → 0.1.7
- `deploy/terraform/layers/cluster/variables.tf` — chart version default → 0.1.7

## Open questions

- Promote the scrape-layer decision to `docs/adr/0029-*`? The reasoning is permanent and
  the alternative (dashboard job filters) will look tempting again to the next person.
- `durable-execution-value` has no OSS-mode representation at all — every panel is
  Cloud-metric-only. Is that intended, or a mode-parity gap worth its own work item?

## Next

1. Ask the migration customer at Phase 0B kickoff: *how many scrape jobs cover your Temporal pods?*
   A Helm-deployed Temporal with annotation-based discovery is a common shape, and this
   was found on the first cluster anyone pointed the script at.
