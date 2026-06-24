# ADR-0011: Local-only ArgoCD delivery via a local OCI registry

- **Status:** Accepted (refines the delivery half of ADR-0002; supersedes the git-backed
  delivery described in ADR-0009)
- **Date:** 2026-06-24

## Context

ADR-0009 stood up `layers/cluster` with ArgoCD pulling the orders-workers chart from GitHub and
the add-on charts (cert-manager, Worker Controller) from public Helm registries. That works on a
connected laptop but has two problems: (1) it needs public-internet egress (GitHub + quay.io +
docker.io) so it isn't air-gap-capable and isn't a faithful local rehearsal of a private-registry
estate; (2) iterating on a local chart required a `git push` before ArgoCD would see it.

Goal: decouple ArgoCD from the public internet for **delivery**, keep the developer's
`git push origin main` to GitHub 100% unchanged (that pushes *source*, not deploy state), and put
no binaries (chart `.tgz`, images) into git.

## Decision

The local registry is **zot** (CNCF OCI-native) — it both **hosts our pushes** and
**pull-through-caches** upstreams on demand. ArgoCD pulls **every** chart from it (`localhost:5001`
host / `kind-registry:5000` node / `registry-tls.kube-public.svc:5000` in-cluster via the proxy).
No GitHub, no public Helm/registry pulls during steady-state delivery.

- **Third-party container images** (cert-manager, Worker Controller, kube-rbac-proxy, the proxy's
  nginx, …) are **pull-through-cached on demand** by zot (`deploy/kind/zot-config.json`, `sync`
  extension, `onDemand: true`, prefix-routed: `jetstack/**`→quay.io, `kubebuilder/**`→
  registry.k8s.io, `library/**`+`temporalio/**`→docker.io). Charts keep their **original** image
  refs; each node's containerd resolves `quay.io`/`registry.k8s.io`/`docker.io` to zot via
  `certs.d` host redirects. On a cache miss zot fetches from upstream **and caches** it; on a
  prefix with no rule, containerd falls back to the upstream directly (covers e.g. ArgoCD's own
  images, which aren't enumerated). For strict air-gap, set `onDemand: false` (pre-sync) and drop
  the `certs.d` fallback. This replaced the earlier `crane copy` explicit image mirror — no image
  enumeration, new tags self-populate.
- **Third-party charts** (cert-manager, Worker Controller + CRDs) are still pushed explicitly by
  `just mirror-deps` (`helm pull|push` → `oci://localhost:5001/charts`): cert-manager ships from a
  **classic** Helm repo (not OCI, so not pull-through-cacheable), and one uniform `repoURL` for all
  add-on Applications is simpler. This is a tiny, fixed set.
- **The orders-workers chart** is published to the same registry by `just chart-publish`.
- **All Applications are seeded by Terraform** (`kubectl_manifest`), not a git-backed app-of-apps —
  so startup has no GitHub dependency. The add-on Application *definitions* remain committed YAML
  (read + inlined by TF); the account-bearing orders-workers values are injected from cloud state.
- **ArgoCD reaches the registry through a TLS proxy** (`registry-tls.kube-public.svc`, nginx, in
  `registry-proxy.tf`). ArgoCD v3's repo `insecure: true` means *skip-TLS-verify*, not
  *plain-HTTP* — its repo-server speaks HTTPS, so it can't pull from the plain-HTTP registry
  directly. The proxy terminates HTTPS (self-signed; ArgoCD skip-verifies) and forwards to the
  HTTP registry. Every other client (host `docker push`, node containerd, `crane`, `helm`) keeps
  using the registry over HTTP unchanged.

`just platform-up` orchestrates the whole bring-up: cluster-up → mirror-deps → ci → chart-publish →
digest-pin → `terraform apply`.

## Consequences

- ArgoCD delivery is fully local/offline-capable; verified end-to-end (all Applications Synced/
  Healthy, workers registered on Cloud nonprod) with no GitHub or public-Helm pulls.
- `git push origin main` is unchanged — ArgoCD simply no longer reads git. The pure-GitOps-from-a-
  remote shape is still available by pointing `repoURL` at a git/OCI remote.
- One extra in-cluster component (the TLS proxy) and its self-signed cert. Isolated: it cannot
  affect the verified host/node image+chart paths.
- Re-publishing a changed chart is `just chart-publish` (vs a git push); changed worker code is
  `just ci` (rebuild + push, new digest → new Build ID).
- **Remaining internet dependency:** ArgoCD itself (its chart via the Terraform `helm_release`, its
  images from quay.io) is the bootstrap tool and is *not* mirrored. For strict air-gap, mirror the
  argo-cd chart + images too and point the `helm_release` at the local registry — noted, not done.
- **Disk management:** zot has `dedupe` (shared blobs stored once) plus scheduled GC (`gcInterval`/
  `gcDelay`) and `retention` policies (keep the N most-recently-pushed tags + a `pulledWithin`
  window, delete untagged) in `zot-config.json`, so cached + pushed artifacts self-prune. Manual
  reset: `docker volume rm kind-registry-data` (rebuilt on demand). Because we deploy by digest,
  retention is kept generous enough not to evict a digest a running pod may re-pull.
- **One version source:** the third-party versions (cert-manager, Worker Controller, argo-cd, zot,
  nginx) live once in `config/dependencies.yaml` — read by Terraform (`yamldecode`; injects each
  Application's `targetRevision`, the argo-cd chart version, and the proxy image) and by the bash
  scripts (rendered to `config/.generated/deps.env` by `compose/scripts/render-deps.py`). Kills the
  earlier cert-manager/worker-controller version duplication. Same pattern as `namespaces.yaml`.
