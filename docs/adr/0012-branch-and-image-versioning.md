# ADR-0012: Branch/promotion model and image versioning

- **Status:** Accepted
- **Date:** 2026-06-24

## Context

ArgoCD with `automated: { selfHeal, prune }` reconciles its tracked source continuously. If that
source is a fast-moving mutable branch, every commit lands in the cluster immediately — fine for a
personal kind cluster, dangerous on a shared/prod one (WIP commits thrash running workloads). We
also needed a defensible image-versioning scheme: the current short-SHA tag is traceable but
mutable, and a dirty tree could masquerade as a clean commit.

## Decision

### Branch / promotion model (single trunk, no env branches)

- **Develop on feature branches**; merge to `main`. `main` is the always-deployable integration
  line. **nonprod tracks `main`** and auto-syncs on merge.
- **prod tracks an immutable git tag** (`vX.Y.Z`), never a branch. Promotion = advance the tag prod
  points at, after nonprod validates. (Matches `layers/workloads/README.md`.)
- Do **not** point a prod environment at a mutable branch; do not commit WIP to a tracked revision.

Two independent anti-thrash layers protect different things:

1. **GitOps discipline** (feature branches + prod-tracks-tag) protects the *cluster* from
   half-baked config.
2. **Temporal Worker Versioning (PINNED)** protects *in-flight workflow executions*: a new worker
   version syncing does not migrate running workflows — they complete on their pinned Build ID
   (ADR-0004). A bad sync cannot strand live executions the way a naive rolling deploy would.

> Local note: with the local-OCI + TF-seeded delivery (ADR-0011), "what nonprod runs" is pinned by
> the published chart version + the image **digest** the cluster layer injects, rather than a git
> revision. The branch/tag model above is the production GitOps shape this mirrors.

### Image versioning

- **Tag = `git describe --tags --always --dirty --abbrev=12`** — human-readable, and a dirty tree
  is never confused with a clean commit (it carries a `-dirty` suffix).
- **Deploy by digest, not tag.** `just ci` captures each pushed image's `sha256` digest; the
  cluster layer pins `repository@sha256:…` in the WorkerDeployment pod template. The digest is the
  immutable contract; the tag is metadata.
- This composes with the Worker Controller, which derives the **Build ID from the pod-template
  hash**: pinning by digest makes the Build ID change **iff image content changes** (content-
  addressed versioning). A mutable tag could leave a stale Build ID against new bytes.

## Consequences

- Clear promotion path; prod is insulated from branch churn; live workflows are insulated from
  deploys.
- Every deployed worker is uniquely, immutably identified (digest → Build ID).
- Requires tagging discipline for prod releases; `latest` remains only a non-deploy fallback in the
  chart's `values.yaml`.
