# 0023 — MCP servers for agents (ClickHouse, Prometheus, Kubernetes)

- **Status:** **Landed + verified live** against the kind+Cloud host-plane stack.
- **Date:** 2026-06-30
- **ADRs:** none — tooling config, not an architecture decision. Rationale lives in `docs/MCP.md`.

## Done this session

- **Added a project-scoped `.mcp.json`** (repo root, committed) enabling three read-mostly MCP
  servers pointed at this repo's local kind+Cloud endpoints:
  - `clickhouse` — `uvx mcp-clickhouse` → `localhost:8123`, user `default`, the committed
    workbench password (`ziggymart-local-clickhouse`). Read-only by default.
  - `prometheus` — `uvx --from git+…/pab1it0/prometheus-mcp-server` → `http://localhost:9009`
    (the durable host `prometheus-store`, the remote_write superset; in-cluster Prometheus has
    no host port and only a short hot window).
  - `kubernetes` — `uvx kubernetes-mcp-server@latest --read-only` (the Go-native
    `containers/kubernetes-mcp-server`, pulled as a binary by uvx) → kubeconfig
    `.secrets/kube/kind.kubeconfig`, context `kind-kind`.
- **All three run via `uvx`** — no new Node/Go toolchain (host had no `node`); zero provisioned
  secrets, so nothing sensitive lives in `.mcp.json`. `KUBECONFIG` uses `${PWD}` to stay
  repo-relative.
- **Docs:** new `docs/MCP.md` (what each points at, prereqs, smoke tests, the per-turn
  token-tax rationale, and why Docker/Terraform/Grafana/ArgoCD were left out); pointer section
  in `AGENTS.md`; entries in README's docs tree + documentation map.
- **Verified live:** stack up → ClickHouse exposes `otel_logs` + all `otel_metrics_*`;
  host store has 1056 metric names incl. 74 `temporal_*` (SDK `temporal_activity_*`/`workflow_*`
  + Cloud `temporal_cloud_v1_*`); `kind-kind` context live. All three servers fetch + launch
  stdio clean.

## Decisions (settled — see `docs/MCP.md`)

- **Scope by net value, not completeness.** Each connected MCP server taxes *every* agent turn
  with its tool schemas, so only servers whose leverage beats that tax ship. Kept the three
  high-leverage, zero-secret ones; dropped Docker (shell already trivial/allowlisted), Terraform
  (Registry/HCP-docs only — can't read local state/`deploy/terraform/layers/`), Grafana (query
  tools redundant with CH/Prom), ArgoCD (covered by `kubectl get applications.argoproj.io`;
  fragile token mint).
- **Point Prometheus at the host store `:9009`,** not the in-cluster instance — it's the only
  host-reachable one and the persistent superset.

## Open questions

- None blocking. Grafana/ArgoCD MCPs remain a clean follow-up if dashboard/alert or Argo-app
  introspection becomes a recurring workflow (each needs a token in a gitignored `.secrets/mcp.env`).

## Next

1. (Carried from 0021/0022) Worker-health dashboards/alerts over the pull pipeline:
   schedule-to-start p99, sync-match, slots-available, backlog.
2. Autoscaling phase (ADR-0023): install KEDA, wire the Prometheus + Temporal scalers.
3. Add attributes to business metrics + build high-cardinality business views in ClickHouse.
