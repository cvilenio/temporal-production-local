# MCP servers for AI agents

This repo ships a project-scoped [`.mcp.json`](../.mcp.json) that gives AI agents (Claude Code,
etc.) first-class access to the three runtime systems they interact with most. The servers are
**read-mostly** and point at the local **kind + Cloud** run path's host-plane endpoints.

## What's enabled

| Server | Runner | Points at | Replaces |
|--------|--------|-----------|----------|
| `clickhouse` | `uvx mcp-clickhouse` | `localhost:8123` (HTTP), user `default` | `docker exec clickhouse clickhouse-client …` |
| `prometheus` | `uvx --from git+…/prometheus-mcp-server` | `http://localhost:9009` (host `prometheus-store`, 15d tier) | `curl '…/api/v1/query?query=…'` + JSON parsing |
| `kubernetes` | `uvx kubernetes-mcp-server@latest --read-only` | kubeconfig `.secrets/kube/kind.kubeconfig` (context `kind-kind`) | long `kubectl --kubeconfig … --context …` lines |

All three run as **host processes via `uvx`** — `uv` is already a repo prerequisite, so there is
no new Node/Go toolchain dependency. The ClickHouse password is the committed workbench constant
from `docker-compose.yml` (not a secret); the kubeconfig path is repo-relative via `${PWD}`. None
of the three need provisioned tokens, so nothing secret lives in `.mcp.json`.

## Prerequisites

- `uv` (already used across the repo) → provides `uvx`. Nothing else.

## Requires the stack to be up

These are **live-connection** servers. They connect to the local observability + kind stack, so
bring it up first (console-first, per the repo rule):

```
just host-up           # host visibility + console + mock-api (detached)
just cluster-up        # kind + workloads
# or one-shot: just platform-up
```

When the stack is down, the servers still load but their tool calls return
`connection refused` — that is expected, not a misconfiguration.

## Smoke test

After the stack is up, restart your MCP client and run its server-list command (e.g.
`claude mcp list`) — all three should be **connected**. Then:

- **clickhouse** — list tables → expect `otel_logs`, `otel_metrics_sum`, …; `SELECT count() FROM otel_logs`.
- **prometheus** — instant query `temporal_slot_utilization` → returns series.
- **kubernetes** — list pods in `orders` / `observability` / `argocd`.

## Why only these three (and not Docker / Terraform / Grafana / ArgoCD)

Every connected MCP server injects its tool schemas into **every agent turn** in this repo,
whether used or not — a standing context-token tax. So a server earns its place only when its
leverage clearly beats that tax. These three do: structured queries + schema/metric discovery
cut both tokens and trial-and-error against the gnarly `otel_*` schema, PromQL, and kube
introspection.

The four left out were assessed as net baggage **for this repo and goal**, not as bad tools:

- **Docker** — `docker ps/logs/compose` is already trivial, well-known, and allowlisted; the MCP
  adds bloaty JSON and an unnecessary start/stop/rm surface. The repo drives Docker via `just`.
- **Terraform** — the HashiCorp server is Registry/HCP-docs oriented. It **cannot** read this
  repo's local state or `deploy/terraform/layers/` (state is local in `.secrets/terraform/`, no
  HCP backend), so it does not "point at this repo's resources." Useful for authoring new IaC,
  not introspecting current infra.
- **Grafana** — its query tools are redundant with the dedicated `clickhouse` + `prometheus`
  servers (Grafana just proxies to them). Only dashboard/alert/annotation introspection is
  unique, and it costs a service-account token.
- **ArgoCD** — app health/sync is largely reachable via `kubectl get applications.argoproj.io`
  through the `kubernetes` server, and its API-token mint (admin `apiKey` capability) is the most
  fragile setup of the set.

If dashboard/alert or Argo-app introspection later becomes a recurring workflow, add `grafana`
and/or `argocd` then — each as its own small change, with the token stored in a gitignored
`.secrets/mcp.env` sourced via `.envrc`.

## Caveats

- **Prometheus** has no clean PyPI release, so it runs from git (`uvx --from git+…`). If that
  ever flaps, fall back to the published image with
  `docker run -i --rm -e PROMETHEUS_URL=http://host.docker.internal:9009 ghcr.io/pab1it0/prometheus-mcp-server:latest`.
- The `kubernetes` server runs `--read-only`; drop that flag only deliberately if an agent needs
  to mutate the cluster (prefer the repo's `just` recipes for that).
