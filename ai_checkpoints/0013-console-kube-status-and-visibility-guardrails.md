# 0013 — Console kube-status provider, External Systems plane, console-first guardrail

- **Status:** **LANDED + LIVE-VALIDATED ON KIND (2026-06-25).** Composite snapshot confirmed live:
  orders-api/orders-db/workers `kube`-sourced and Healthy (orders-db 2/2 primary+replica),
  host-plane tooling `docker`-sourced; a pod-delete rolled the health count + pod name in real time;
  RBAC confirmed read-only via impersonation; worker/CNPG selectors confirmed against live labels.
  Not committed.
- **Date:** 2026-06-25
- **ADRs:** **ADR-0015 phase-2** moved to LANDED (the `kube_status` provider + the previously-unwired
  injected-substrate seam). Extends 0012.

## Why

After 0012 the app tier runs in kind, but the console's architecture page derived health from the
Docker socket — blind to pods, so orders-api/orders-db painted DOWN while Healthy (0012's named
"Next"). Plus two visibility asks: (1) make the console a hard prerequisite for live kind testing so
the operator can follow along, and (2) regroup the topology so the external-dependency mock reads as
*external to the business*.

## Done this session

- **Substrate-aware live status (ADR-0015 phase-2).** Replaced `services/docker_status.py` with a
  `services/status/` package behind a `StatusProvider` protocol:
  - `DockerProvider` — the original socket/probe logic, now honoring an `exclude` set.
  - `KubeProvider` — reads pods via a read-only SA kubeconfig; maps pod phase + container readiness
    to the existing `healthy/degraded/starting/down` vocabulary. Connects lazily and degrades to
    "down" until the cluster appears (console still boots first, per 0012).
  - `CompositeProvider` — on kind, **unions** Kube (cluster workloads: orders-api/orders-db/workers)
    with Docker (host-plane tooling: lgtm/console/viz-proxy/headlamp/mock-api). Not either/or.
  - Substrate selected from injected `CONSOLE_SUBSTRATE` (compose | kind) — **this wires the
    ADR-0015 phase-1 "injected descriptor" seam that until now existed only as a comment.** Base
    compose defaults `kind`; the host-apptier overlay sets `compose`.
  - Identity map lives in `SERVICE_REGISTRY` as an optional `kube` locator (ns + label selector):
    orders-service→`app.kubernetes.io/name=orders-api`, workers→`…=orders-workflow|orders-activity`,
    orders-db→`cnpg.io/cluster=orders-db`.
- **Read-only console identity.** `deploy/kind/console-reader-rbac.yaml` (Namespace + SA +
  long-lived-token Secret + ClusterRole get/list/watch on pods/nodes/namespaces + binding).
  `cluster-up.sh` step 7b applies it and mints `.secrets/kube/kind.console.kubeconfig` from the SA
  token (container-reachable + TLS-skipped, like the Headlamp one). Console mounts `.secrets/kube`
  and points `KUBECONFIG` at it. **Least-privilege on purpose** — the console only observes, so it
  cannot do more (decision: not the admin kubeconfig). Long-lived token survives cluster-stop/start.
- **External Systems plane.** `mock-api` moved to its own group: registry `group` →
  `External Systems`, display → `External System Mocks`. `architecture.html` gains a dashed
  EXTERNAL SYSTEMS boundary box below the customer env; the mock node moves into it and the
  activity-worker→mock edge is now dashed and crosses the boundary.
- **Console-first guardrail.** `poe preflight-console` probes `:8086/healthz`; `just preflight`
  wraps it; `just platform-up` and `just orders-db-reset` are gated on it. Durable rule in
  `AGENTS.md` ("Live kind testing — bring the platform-console up FIRST") + RUNMODES cross-ref.
- **Docker Desktop group rename.** `COMPOSE_PROJECT_NAME` `temporal` → `host-plane` (`.env`).
  `temporal-network` unchanged (it is not the group). Migrate with a clean `down-cloud` → `up`.

## Verification

- **Static (all green):** `poe lint` — ruff/format/pyright 0 errors; helm lint ×3; sync-wave ok ×3;
  kubeconform 5/2/3 + **11 resources/7 files** (now incl. console-reader RBAC); `poe test` (no tests
  yet, tolerated). `docker compose config` valid for base (substrate kind) and +host-apptier
  (flips to compose). `uv lock` updated (`kubernetes` added to the platform-console group).
- **Live (DONE, kind):** `just cluster-up` minted `.secrets/kube/kind.console.kubeconfig` + applied
  RBAC; cleaned the old `temporal`-project stack; `just up-cloud-kind` rebuilt the console (new deps
  via the relocked uv.lock) under the `host-plane` project. Snapshot showed orders-* as `kube`/
  Healthy and host-plane tooling as `docker`/Healthy. A `kubectl delete pod orders-api` rolled the
  health string (1/1→1/2→2/2→1/1) and pod name live. **Note:** scaling a Deployment to 0 to force a
  DOWN does NOT work here — ArgoCD selfHeal (ADR-0016) reverts the replica drift before pods drain;
  that's correct GitOps behavior, not a feature gap. The DOWN render path is the cluster-absent /
  no-pods branch (`_down_entry`).

## Next / follow-ups

- **Live-validate** the above from scratch (the real proof), then commit.
- **Log streaming on kind** (Docker logs → pod logs) is still Docker-only — the next ADR-0015 slice.
- **Topology-as-data** (ADR-0015 phase-3) remains deferred; the `group` field is still cosmetic
  until the template is data-driven (the boundary boxes in `architecture.html` are hardcoded).
- The `KubeProvider` worker selectors (`app.kubernetes.io/name=orders-workflow|orders-activity`)
  and the CNPG `cnpg.io/cluster=orders-db` label are derived from the charts — **confirm against a
  live cluster** during live-validation in case the worker-controller relabels pods.
