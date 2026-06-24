# 0007 — local visibility plane (Headlamp + ArgoCD UI), console as run-mode-aware aggregator

- **Status:** **PLANNED (2026-06-24) — not started.** This checkpoint is a forward plan, not a
  record of landed work. It captures scope before implementation so the design (ADR-0014,
  ADR-0015) and the build are reviewable separately.
- **Date:** 2026-06-24
- **ADRs:** [ADR-0014](../docs/adr/0014-local-visibility-plane.md) (visibility plane placement),
  [ADR-0015](../docs/adr/0015-console-backend-substrate-aware.md) (console evolution).

## Why

Checkpoint 0006 landed kind + ArgoCD + local-OCI delivery, but local visibility into the cluster
is missing: kind nodes are Docker containers running their own containerd, so Docker Desktop shows
only the four node containers and no pod logs. ArgoCD's UI runs in-cluster but is reachable only
via an ad-hoc `kubectl port-forward`. We want two browser UIs — a K8s explorer and the ArgoCD UI —
surfaced through the existing `retail-demo-console` aggregator, without coupling them to the
cluster's lifecycle any more than each tool's nature requires.

## Guiding decisions (from the ADRs)

- **Placement by role:** observers run on the host plane (Compose); cluster-native components stay
  in-cluster. Headlamp = observer → host-side. ArgoCD UI = cluster component → in-cluster, surfaced
  via a stable host seam.
- **Host plane is non-prod-grade on purpose.** The console + observer UIs are a business-side /
  local-operator stand-in, not something a customer must run. Production-grade discipline stays on
  the **cluster plane**.

## Scope — what 0007 will build

### A. Headlamp (host-side cluster explorer)
- New Compose service (image from `config/dependencies.yaml`, mirrored per the existing pattern if
  air-gap parity is wanted; otherwise host-plane is allowed to pull from upstream — non-prod).
- Mount `.secrets/kube/temporal-platform.kubeconfig` read-only; publish a host port.
- Connects at view time, not start time → Compose-only runs (no kind) still start; Headlamp shows
  "cluster unreachable."

### B. ArgoCD UI (in-cluster, surfaced via host nginx)
- `kind-config.yaml`: add an `extraPortMappings` (hostPort → NodePort).
- Expose `argocd-server` as a NodePort; run it `--insecure` locally.
- Extend `compose/deployment/nginx/nginx.conf` with an ArgoCD `server` block that strips
  `X-Frame-Options` / sets `frame-ancestors` (same trick as the Temporal-UI block).
- Replace the `port-forward` hint in `just platform-up` and RUNMODES with the stable URL.

### C. Console tabs + minimal run-mode awareness
- Add `headlamp_embed_url` + `argocd_embed_url` to `app/config.py`.
- Add two `_embed_page` routes in `app/routes/pages.py` and two nav entries in `base.html`
  (mirrors Grafana/pgweb/Temporal-UI).
- Minimal ADR-0015 slice only: make the Temporal-UI embed target injected/run-mode-aware and add
  the **link-out fallback** for Cloud (Cloud UI can't be iframed). Do **not** rewrite the status
  page or topology in this checkpoint.

## Explicitly out of scope (deferred to later checkpoints / ADR-0015)
- `kube_status` provider so the architecture/status page is live on kind (today it's Docker-only).
- Topology-as-data to de-bake the retail-only graph and support multiple domains.
- In-cluster Headlamp for a production-faithful demo (host-side is the default).
- Mirroring Headlamp/ArgoCD into the air-gap boundary (host plane is non-prod; ADR-0013 boundary
  stays the cluster plane).

## Verification plan (to run when built)
- Compose-only (no kind): stack up, Headlamp tab shows "unreachable" cleanly, no startup failure.
- `just platform-up`: Headlamp lists pods across namespaces, streams a worker pod's logs, exec works.
- ArgoCD tab renders Applications Synced/Healthy inside the console iframe (framing headers stripped).
- `just cluster-stop` → ArgoCD tab goes dark gracefully; Headlamp shows "unreachable"; console and
  Grafana stay up. `just cluster-start` → both recover with no console restart.
- Cloud run-mode: Temporal-UI tab degrades to a link-out card (no broken iframe).
- `nginx -t`, `terraform fmt/validate`, `ruff`/`poe lint` clean.

## Resolved decisions (2026-06-24)
- **Console scope:** Headlamp + ArgoCD tabs land **now** (this checkpoint) so they're usable as
  kind development continues. The fuller console evolution (status-page rewrite, topology-as-data)
  stays deferred per ADR-0015 — confirmed.
- **ArgoCD local auth:** `--insecure` + anonymous-read. Zero-friction local; prod-exposure gap is
  recorded in ADR-0014 and not papered over.
- **Headlamp image lives OUTSIDE zot.** zot is the cluster-plane / containerd air-gap boundary
  (ADR-0011/0013); host-plane Compose images are pulled by the Docker daemon and cached in Docker's
  local image store. Offline parity for the host plane comes from Docker's cache (pull once, reuse),
  not from zot — consistent with ADR-0014 decision 5 (host plane is non-prod, upstream pull allowed).

## Open before building
- Headlamp host port assignment (avoid collision with 3000/8081/8083/8086).

## Next (after 0007)
- ADR-0015 phase 2: `kube_status` provider → live architecture page on kind.
- ADR-0015 phase 3: topology-as-data for multi-domain.
