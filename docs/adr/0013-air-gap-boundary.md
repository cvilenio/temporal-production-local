# ADR-0013: Air-gap boundary — cache artifacts, not source indexes

- **Status:** Accepted
- **Date:** 2026-06-24

## Context

ADR-0011 made the delivery plane local-only: zot hosts/pull-through-caches every OCI artifact, so
the running cluster needs no internet. That raised a fair question — the **build** plane is still
online: `docker build` pulls a base image, the uv binary, and resolves Python deps from PyPI; each
new language adds its own index (Go module proxy, npm, Maven Central, crates, …). Fully air-gapping
that means mirroring every package ecosystem — an open-ended, per-language, ongoing burden that is
disproportionate to a local-rehearsal demo and contradicts ADR-0002 ("no rabbit holes").

We need a stated limit: what is reasonable to air-gap vs. not, as a principle going forward.

## Decision

**The air-gap boundary is the OCI registry. Anything that crosses *into the cluster* must be an
immutable OCI artifact (image or chart) served locally (zot). Anything *upstream of producing*
those artifacts — language package indexes, base images, tool binaries — may require the network.**

Cache the **outputs** of builds; connect/proxy for the **inputs**. We do **not** mirror PyPI / npm /
Go proxy / Maven / crates / apt, nor mirror base images for the build step.

### The offline contract (three tiers)

| Tier | Activity | Offline guarantee |
|---|---|---|
| **1. Run** | deploy + execute workloads on a warm cluster | **Guaranteed** — fully local |
| **2. Rebuild** | rebuild an image from *already-resolved* deps | **Best-effort** — only free wins (the uv cache mount in `images/python.Dockerfile`); not guaranteed |
| **3. Resolve** | pull *new/changed* source deps, base images, tool binaries | **Requires network** — by design |

One line: **a warmed cache runs and re-runs the platform offline; producing new artifacts requires
connectivity.** You go offline *after* a warm build, not before.

### Why this seam

- **It's where regulated estates actually draw it** — the air-gapped *cluster* pulls from an internal
  registry; package resolution sits behind a *proxy*, which is a platform product, not a demo's job.
- **Content-addressability** — images/charts are finite, versioned, digest-pinned (cheap to cache in
  one place). Source deps are a transitive, version-exploding graph (a different cost class).
- **Value alignment** — the point of local-offline is rehearsing the prod *delivery* path; that is
  delivered once images exist and the cluster pulls locally. Re-resolving PyPI is a build concern the
  offline-*run* goal doesn't need.
- **No combinatorial blow-up** — each language would otherwise add another index to mirror.

## Consequences

- Tier 1 is fully invested (ADR-0011). Tier 2 takes only free wins (existing `uv` cache mount). Tier
  3 is explicitly out of scope.
- Consistent caveats, not exceptions: zot is a *cache*, so its first pull-through needs internet; and
  ArgoCD's own bootstrap chart/images come from upstream. Both are tier-3 "warm it once."
- **Escape hatch for a customer that truly needs tier-3 offline:** stand up Artifactory/Harbor (or
  similar) as a pull-through proxy for all package types and point uv/npm/Go/etc. at it. Named, not
  built — it is the customer's platform investment, outside this repo's boundary.
- New rule for contributors: if something must run *in the cluster*, it ships as an OCI artifact via
  zot. If something is a *build input*, it may fetch from the internet — do not add a mirror for it.
