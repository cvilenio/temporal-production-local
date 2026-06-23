# deploy/ — production-like lifecycle (kind + Temporal Cloud)

Two planes (see `docs/ARCHITECTURE.md`):

- **Control plane → Terraform** (`terraform/`): the kind cluster, Temporal Cloud
  (namespaces + API keys), and the ArgoCD install + root app-of-apps.
- **Workloads → ArgoCD → Helm** (`argocd/`, `charts/`): everything on kind — Temporal
  server, workers (versioned), apps, codec server, observability.

## Layers

Terraform is split into independent layers, each its own root module + state. Apply in
order; each layer pulls only the providers it needs.

```bash
# 1. Cloud layer (base): Temporal Cloud namespaces + service accounts + API keys for
#    both envs. Independent of the cluster — applies with no kind present.
cd deploy/terraform/layers/cloud   # see its README.md
export TEMPORAL_CLOUD_API_KEY=...
terraform init && terraform plan -out=cloud.plan && terraform apply cloud.plan

# 2. Cluster layer (STUB): kind cluster + ArgoCD + the Cloud-API-key k8s Secret.
#    Seeded by the legacy deploy/terraform/main.tf; not yet carved into layers/cluster.

# 3. Workloads: ArgoCD syncs the app-of-apps (no Terraform).
kubectl apply -f deploy/argocd/root-app.yaml
```

## Status of this scaffold

| Path | State | Notes |
|---|---|---|
| `terraform/layers/cloud/` | **concrete** | Temporal Cloud ns+SA+key, both envs via `for_each`; API-key auth |
| `terraform/modules/cloud-namespace/` | **concrete** | reusable per-env namespace building block |
| `terraform/layers/{cluster,workloads}/` | stub READMEs | TF↔Argo boundary documented; not built |
| `terraform/kind-config.yaml` | **concrete** | port maps for Temporal gRPC (7233) + OTLP (4318) |
| `terraform/{main.tf,versions.tf,variables.tf}` | legacy skeleton | kind + ArgoCD seed for `layers/cluster` |
| `argocd/root-app.yaml` | skeleton | app-of-apps pointing at `applications/` |
| `argocd/applications/orders-workers.yaml` | example | one full Application; copy for the rest |
| `charts/temporal-server/values.yaml` | starting point | official chart values, CNPG-backed (from `alexandreroman/temporal-k8s`) — verify against the chart version |
| `charts/orders-workers/` | **concrete-ish** | Worker Controller `WorkerDeployment` + `Connection` CRDs (the versioning crux) |
| `charts/{orders-api,console,mock-api,codec-server,observability}` | TODO | thin Deployments/Services; wrap the same images compose builds |

Scavenge sources captured inline: Temporal server Helm values, CNPG, kind gRPC exposure,
and backlog-driven HPA from `alexandreroman/temporal-k8s`; the `WorkerDeployment` CRD and
rollout semantics from `alexandreroman/temporal-versioning-demo`.

## Prerequisites

kind, kubectl, helm, terraform, the Temporal Worker Controller (installed via Terraform/
ArgoCD; depends on cert-manager). Container runtime: Docker.
