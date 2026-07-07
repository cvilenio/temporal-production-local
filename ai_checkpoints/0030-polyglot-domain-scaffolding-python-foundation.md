# Checkpoint 0030 — Polyglot domain scaffolding (Python foundation, M1–M4)

**Date:** 2026-07-07
**Status:** **Ready for PR #1** (branch `polyglot-domain-scaffolding`; not merged — awaits
Temporal-aware independent review). Static gates + offline scaffolder pytest green; live hello
proof completed on kind+OSS then stripped from the committed diff.

## Why

Make it fast and repeatable to land Temporal demo domains (Python now, Java in PR #2) without
hand-copying the orders tree. Centralize within-domain contracts (task queues, data converter,
observability) in a descriptor + scaffolder so porting external demos stops re-inventing wiring.

## Milestones delivered

### M1 — Domain descriptor + verify gate

- `config/domains/ziggymart.yaml` for the existing orders workload (`kernel: orders`)
- `compose/scripts/verify-domains.py` — cross-checks namespaces.yaml + kernel TaskQueue constants
- Wired into `just lint` via `just verify-domains`

### M2 — Pluggable data converter

- `appkit.temporal.connect(data_converter=…)` optional param (default unchanged)
- `appkit.domains` — load descriptor, `resolve_data_converter`, `data_converter_for_namespace`
- Workers + orders-api resolve converter from namespace/domain
- Added `pyyaml>=6.0` to appkit (workers crashed without it during live verify)

### M3 — Python template + scaffolder

- `templates/domain/python/` — HelloWorkflow stub, production-split workers, activity routing helper
- `templates/charts/domain-workers/`, `templates/grafana/`
- `compose/scripts/scaffold_domain.py` + `just scaffold-domain`
- **`--root` / `--template-root`** for offline pytest

### M4 — Grafana dashboard template

- Tokenized dashboard with SDK schedule-to-start panels
- **Live fix:** datasource uid `prometheus` → **`prometheus-kind`** (panels were "no data")
- PromQL uses `namespace="<domain>"` + per-queue `task_queue` labels

## Live verification (hello proof domain — not committed)

Executed on **kind + OSS** (`temporal_backend=oss`) to avoid Cloud execution cost:

1. Scaffolded `hello`, built/pushed worker images, published `hello-workers:0.1.1` chart
2. Deployed hello workers in `orders` k8s namespace (shared mTLS secret)
3. Created bare Temporal namespace `hello` on OSS (bootstrap still ziggymart-only)
4. **HelloWorkflow `hello-m3-verify` → COMPLETED** (`{"message":"Hello, Temporal!"}`)
5. Activity routed to **`hello-activity-task-queue`** (production split confirmed)
6. Grafana **`hello-overview`** panels resolve with `prometheus-kind` (schedule-to-start NaN when idle)

Live fixups back-ported into templates:

- `VersioningBehavior.PINNED` on HelloWorkflow
- `startupProbe.enabled: false` in domain-workers chart values (no orders-api dependency)
- `pyyaml` in appkit dependencies

## PR #1 scope (banked, hello stripped)

**In:** descriptor schema, verify-domains, domains.py, scaffolder, Python/Java-ready templates,
ziggymart descriptor, adapting-a-demo.md, ADR-0026, scaffolder pytest.

**Out (stripped from diff):** `libs/hello`, hello workers, hello chart, hello ArgoCD app, hello
Grafana mounts, hello pyproject group, hello TF vars.

**Deferred to PR #2:** Java appkit, Java template, Spring Boot worker layout.

**Deferred (design items, not PR #1):**

1. `domains.py` repo-tree path won't resolve in-image → non-default converter silently falls back
2. Scaffolder pyproject patches via string-replace anchors (silent no-op if anchor drifts)

## Offline guard added for PR #1

`compose/scripts/tests/test_scaffold_domain.py` — scaffolds into `tmp_path`, asserts expected
files, runs `verify-domains` with `DOMAIN_VERIFY_ROOT`.

## Next steps

1. Open PR #1 with informative body (include live hello proof in verification section)
2. Run Temporal-aware `/code-review` before merge
3. PR #2: Java appkit + `templates/domain/java/` + Java section in adapting-a-demo.md
