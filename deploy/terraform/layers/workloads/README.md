# layers/workloads — ArgoCD-owned (NOT Terraform)

There is intentionally no Terraform here. Everything that runs on the cluster — workers,
apps, codec server, observability — is delivered by **ArgoCD → Helm**, defined under
[`deploy/argocd/`](../../../argocd/) and [`deploy/charts/`](../../../charts/).

Target shape (see `docs/ARCHITECTURE.md`):

- **Single production-shaped environment, no nonprod/prod env split** (ADR-0017). Release
  *progression* is **worker versioning within the live namespace** — the Worker Controller
  ramps a new PINNED Build ID (rainbow deploy, drain old), which is how Temporal versioning
  is meant to be used — plus git-tag/image-digest pinning for delivery (`targetRevision`).
  That replaces a nonprod→prod artifact promotion (an axis that bought no Temporal feature).
- Per **domain** (not env): an Application set targets each domain's Temporal Cloud namespace
  (`<domain>.<account-id>`, e.g. `ziggymart`) via the k8s Secret created by the
  [cluster layer](../cluster/README.md). New domains (`payments`, …) are what unlock Nexus.

## One documented exception: the orders-workers Application

ArgoCD reconciles every workload, but the **orders-workers** Application is *seeded by the
[cluster layer](../cluster/README.md)* (Terraform), not committed under
[`deploy/argocd/applications/`](../../../argocd/applications/). Reason: it carries the
account-bearing namespace handle + regional endpoint, which must not live in git
(`.githooks/pre-commit`). Terraform reads those from cloud state and injects them into the
Application's `valuesObject`; ArgoCD still does the reconciling. Secret-free add-ons
(cert-manager, Worker Controller) remain pure-GitOps under `applications/`. Same control-plane vs
data-plane asymmetry as ADR-0007; see ADR-0009.
