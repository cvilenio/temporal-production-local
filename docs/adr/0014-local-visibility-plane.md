# ADR-0014: Local visibility plane — host-side observer UIs, cluster-native components stay in-cluster

- **Status:** Proposed (2026-06-24). Implementation tracked by checkpoint
  [`0007`](../../ai_checkpoints/0007-local-visibility-plane.md).
- **Date:** 2026-06-24

## Context

The kind cluster layer (ADR-0009, ADR-0011) runs workloads — ArgoCD, the Worker Controller,
cert-manager, the order workers — inside kind nodes. kind nodes are Docker containers that run
their own containerd; the pods live one layer below what Docker Desktop renders. So the only
thing visible in Docker Desktop is four node containers (`control-plane`, two workers, the
registry) with no workload logs. There is currently **no K8s-aware lens** on the local cluster,
and the ArgoCD UI — which already runs in-cluster — is reachable only through an ad-hoc
`kubectl port-forward` line buried in `just platform-up`.

We need two local UIs: a cluster explorer (pods, logs, events, exec) and the ArgoCD UI. The
non-obvious decision is **where each runs**, given a chicken-and-egg risk: a visibility tool that
runs *inside* the very cluster it observes disappears exactly when the cluster is down or
degraded — i.e., when you most need to look.

This repo already has two de-facto planes, even though they were never named:

- **Host plane (Compose):** the apps, the lgtm/Grafana stack, the `retail-demo-console`
  aggregator (:8086), and the `nginx` ui-proxy (:8081). Always-on; survives the cluster being
  stopped or deleted.
- **Cluster plane (kind):** ArgoCD, workers, cert-manager. CLI-owned lifecycle (ADR-0009),
  ephemeral, held to a production-grade bar.

## Decision

1. **Visibility tools are placed by what they observe, not by convenience.** A tool that *is* a
   component of the cluster lives in the cluster. A tool that *observes* the cluster from outside
   lives on the host plane.

2. **The cluster explorer is [Headlamp](https://headlamp.dev/) (CNCF), run host-side in Compose.**
   It mounts the kind kubeconfig (`.secrets/kube/temporal-platform.kubeconfig`) read-only and is
   published on a host port. Headlamp is a web UI (not a TUI), gives the GKE-console-like
   experience — cluster tree, live pod logs, exec, events, RBAC viewer, multi-cluster — and unlike
   Lens carries no licensing friction. Running it host-side is what removes the chicken-and-egg:
   - it survives `cluster-stop`/`cluster-start` and full teardown (shows "cluster unreachable"
     rather than vanishing);
   - it can inspect a *half-broken* cluster whose control plane is flapping;
   - it needs no in-cluster bootstrap, so you never need ArgoCD healthy to see why ArgoCD is not.
   `k9s` remains the terminal companion for power use — not surfaced in the console.

3. **The ArgoCD UI stays in-cluster** — it is a cluster component, not a lens, and cannot
   meaningfully exist without kind. It is surfaced through a **stable host-plane seam** rather than
   `kubectl port-forward`: a kind `extraPortMappings` (hostPort → NodePort) exposes
   `argocd-server`, fronted by the existing `nginx` ui-proxy. The proxy strips `X-Frame-Options`
   and sets a lax `frame-ancestors` CSP (the exact trick already proven for the Temporal UI), and
   `argocd-server` runs `--insecure` locally to avoid TLS-in-iframe pain. The console iframes the
   *nginx* port, so the URL is stable even when the ArgoCD backend is down — the tab simply goes
   dark when the cluster is stopped. **That darkness is correct**, not a defect: ArgoCD is a
   cluster-only concern by nature.

4. **`retail-demo-console` remains the always-on aggregator on the host plane** and gains a
   Headlamp tab and an ArgoCD tab, mirroring the existing Temporal-UI / Grafana / pgweb embed
   pattern (`_embed_page` route + `config.py` setting + `base.html` nav + an nginx server block
   where framing headers must be stripped).

5. **The host plane is explicitly NOT held to the cluster's production-grade bar.** The console
   and the observer UIs are a business-side / local-operator stand-in; none of them is something a
   customer must run to use Temporal. They optimize for fast, frictionless local visibility
   (anonymous/insecure local auth, no HA, no air-gap guarantee). Production-grade discipline —
   GitOps, digest-pinning, the air-gap boundary (ADR-0011/0013) — applies to the **cluster plane**,
   which is the artifact that mirrors a customer environment.

## Consequences

- A new Compose service (Headlamp) and two new `nginx` server blocks (ArgoCD, Headlamp) are added
  to the host plane. The `kind-config.yaml` gains an `extraPortMappings` entry and `argocd-server`
  gets a NodePort + `--insecure`. The `port-forward` hint in `just platform-up` / RUNMODES is
  replaced by the stable URL.
- The host plane now depends on the kind kubeconfig path being present for Headlamp to connect —
  but only at *connect* time, not at *start* time, so Compose-only runs (no kind) still come up
  cleanly with Headlamp showing "unreachable."
- **The console's hardcoded targets are now a known liability** (see ADR-0015). Adding backend- and
  substrate-dependent tabs surfaces that the console bakes in (a) a Docker-only live-status page,
  (b) the single retail/orders topology, and (c) one fixed Temporal-UI location. Headlamp is
  run-mode-invariant (kind is standardized), but the Temporal-UI embed and the architecture page
  are not. ADR-0015 records the evolution toward a backend/substrate-aware console; this ADR only
  adds the two tabs against today's structure.
- The host-plane/cluster-plane split is now explicit and can be cited by future tooling decisions:
  "is this an observer or a participant?" answers where it runs.

## Alternatives considered

- **Headlamp in-cluster (Helm app, ArgoCD-managed).** Rejected as the primary lens: it dies with
  the cluster, defeating the main use case (diagnosing a degraded/stopped cluster). Could be added
  later as a *secondary* in-cluster instance for a production-faithful demo, but the host-side one
  is the default.
- **`kubectl port-forward` for ArgoCD (status quo).** Rejected: fragile, dies on pod restart,
  requires a live terminal, gives an unstable URL the console can't iframe reliably.
- **A real ingress controller on kind for both UIs.** Rejected as overkill for a single-node-ish
  local cluster; `extraPortMappings` + NodePort + the existing host nginx is enough and keeps the
  framing-header strip in one place.

## Open tradeoff

ArgoCD and Headlamp both want auth + websockets through the iframe. Locally this is settled as
`argocd-server --insecure` + anonymous-read (zero-friction local). That is the deliberate non-prod
posture of the host plane (decision 5); it is *not* how these UIs should be exposed in a customer
environment, and the ADR records that gap rather than hiding it.

## Host-plane images and the zot boundary

Host-plane images (the console, Grafana/lgtm, Headlamp) live **outside** zot. zot is the
cluster-plane / containerd OCI registry and air-gap boundary (ADR-0011/0013); only kind's nodes
pull through it. Host-plane Compose images are pulled by the Docker daemon and cached in Docker's
local image store, so offline parity for the host plane comes from Docker's own cache (pull once,
reuse) — not from zot. Mirroring Headlamp into zot is therefore unnecessary and is explicitly not
done.
