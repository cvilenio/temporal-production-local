# ADR-0015: demo-console evolves into a backend- and substrate-aware aggregator

- **Status:** Proposed (2026-06-24), direction accepted, implementation phased. First slice lands
  with checkpoint [`0007`](../../ai_checkpoints/0007-local-visibility-plane.md); the full
  generalization is deferred.
- **Date:** 2026-06-24

## Context

`platform-console` (originally `retail-demo-console`) was built for a single shape of the demo and bakes in three assumptions that
no longer hold now that the repo spans the full run-mode matrix (RUNMODES.md: Compose×{OSS,Cloud},
kind×{OSS,Cloud}):

1. **Substrate = Docker.** The live architecture/status page is fed by `app/services/docker_status`
   over the Docker socket, and `architecture.html` literally frames the model as "these containers
   simulate what Temporal Cloud provides." On kind, the workloads are pods, not host containers —
   the status page goes blind.
2. **One topology = retail/orders.** `architecture.html` hardcodes the orders-service →
   workers → mock-api → orders-db graph and a single `__temporal_cloud__` node. A second business
   domain (RUNMODES already anticipates `payments-*`) has nowhere to render.
3. **One Temporal-UI location.** `config.py` pins `temporal_ui_embed_url=http://localhost:8081`
   (the nginx-proxied OSS Web UI). In Cloud mode the UI is `https://cloud.temporal.io/...` — which
   **cannot be iframed at all** (Temporal Cloud sends frame-busting headers we don't control), so
   the embed pattern must degrade to an external link, and the URL is namespace/account-specific.

ADR-0014 adds Headlamp and ArgoCD tabs against this structure. Headlamp is run-mode-invariant
(kind is standardized), so it slots in cleanly. The other targets are not invariant, which forces
the question this ADR answers: how should the console know *which* backend and *which* substrate
it is pointed at?

## Decision

1. **The console becomes run-mode-aware via injected config, never inference.** The same contract
   the apps already follow (RUNMODES.md: "which backend is injected config, not a code change")
   extends to the console. A single injected descriptor — backend (`oss` | `cloud`) and substrate
   (`compose` | `kind`) — drives which embeds, which status source, and which topology render. The
   `poe`/`just` task that brings up a given run-mode sets it, the same way each task already sources
   its connection profile.

2. **Embed targets are resolved per run-mode, with a graceful non-iframable path.**
   - Temporal UI: OSS → the nginx-proxied local Web UI (framable, today's path); Cloud → an
     **external link-out** card (Cloud UI can't be framed), built from the namespace/account.
   - Headlamp / ArgoCD: per ADR-0014 (Headlamp always; ArgoCD only when a kind cluster exists).
   - Grafana / pgweb: unchanged.
   The embed page learns one new state — "open in new tab" — for targets that refuse framing.

3. **The live-status source is abstracted behind the substrate.** `docker_status` becomes one
   implementation of a status provider; a `kube_status` provider (reading the kubeconfig, the same
   one Headlamp uses) backs the kind substrate. The architecture page consumes a substrate-neutral
   snapshot, so the node/edge graph stops asserting "Docker container."

4. **Topology is data, not markup (deferred slice).** The hardcoded orders graph in
   `architecture.html` should eventually be driven by a per-domain descriptor so additional
   business cases (payments, etc.) render without editing the template. This is the largest piece
   and is explicitly **deferred** — it is not required to land the visibility plane.

## Phasing

- **Now (checkpoint 0007, minimal slice):** make the embed targets injected/run-mode-aware enough
  to add the Headlamp + ArgoCD tabs and to stop hardcoding the single Temporal-UI location;
  link-out fallback for the Cloud Temporal UI. Do **not** rewrite the status page or topology yet.
- **Next — LANDED (checkpoint 0013):** the `kube_status` provider so the architecture page is live
  on kind, not just Compose. The status source is now abstracted behind a `StatusProvider`
  (`app/services/status/`): `DockerProvider` (Compose), `KubeProvider` (kind pods, via a read-only
  ServiceAccount kubeconfig), and a `CompositeProvider` that unions them on kind — Kube for the
  cluster-resident workloads (orders-api/orders-db/workers), Docker for the host-plane tooling that
  still runs in Compose (lgtm, console, viz-proxy, headlamp, mock-api). Substrate is injected via
  `CONSOLE_SUBSTRATE` (compose | kind) — this also **wires the phase-1 "injected descriptor" seam**,
  which to this point existed only as intent. Provider selection is config, never inference.
- **Later:** topology-as-data to de-bake the retail-only assumption and support multiple domains.
  Log streaming on kind (Docker logs → pod logs) is also still Docker-only — a follow-on to this
  slice, not yet done.

## Consequences

- The console stops being a retail/Docker-specific artifact and becomes the run-mode-agnostic
  front door that RUNMODES.md already implies the rest of the repo is.
- Per ADR-0014, the console lives on the **non-prod host plane** — this generalization is for demo
  fidelity and operator convenience, not a customer-shipped component. Effort is scoped accordingly
  (phased, not a big-bang rewrite).
- ~~Until the `kube_status` provider lands, the architecture/status page is accurate only in Compose
  substrate; on kind, Headlamp is the source of truth for live cluster state and the console's own
  status page is knowingly stale.~~ **Resolved (phase-2, checkpoint 0013):** the architecture page
  is now live on kind via `KubeProvider`. Headlamp remains the deep cluster explorer; the console's
  page is again a faithful at-a-glance topology in both substrates.
- The console reads the cluster with a **least-privilege, read-only ServiceAccount**
  (`console-reader`: get/list/watch on pods/nodes/namespaces only — no writes, no Secrets), minted
  by `cluster-up.sh` as a long-lived-token kubeconfig under `.secrets/kube`. It deliberately does
  **not** reuse the admin kubeconfig Headlamp uses: the console only observes, so it should only be
  able to observe — a habit worth modeling even on the non-prod host plane.
