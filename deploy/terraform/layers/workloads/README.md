# layers/workloads — ArgoCD-owned (NOT Terraform)

There is intentionally no Terraform here. Everything that runs on the cluster — workers,
apps, codec server, observability — is delivered by **ArgoCD → Helm**, defined under
[`deploy/argocd/`](../../../argocd/) and [`deploy/charts/`](../../../charts/).

Target shape (see `docs/ARCHITECTURE.md` and the approved Cloud-layer plan):

- An ApplicationSet generates one Application per environment (`nonprod`, `prod`).
- Single trunk, no env branches. Env divergence lives in `values-<env>.yaml` plus the
  ArgoCD `targetRevision` / image tag:
  - **nonprod** tracks trunk (`targetRevision: HEAD`, images `:latest`/`:<sha>`),
  - **prod** pins an immutable git tag + image tag (`targetRevision: vX.Y.Z`).
- Promotion = advance the tag prod points at, after nonprod validates. Composes with the
  Worker Controller PINNED build-ID versioning.
- Each env targets its Temporal Cloud namespace (`ziggymart-<env>.<account-id>`) using the k8s
  Secret created by the [cluster layer](../cluster/README.md).
