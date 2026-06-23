# deploy/ — production-like lifecycle (kind + Temporal Cloud)

Two planes (see `docs/ARCHITECTURE.md`):

- **Control plane → Terraform** (`terraform/`): the kind cluster, Temporal Cloud
  (namespaces + API keys), and the ArgoCD install + root app-of-apps.
- **Workloads → ArgoCD → Helm** (`argocd/`, `charts/`): everything on kind — Temporal
  server, workers (versioned), apps, codec server, observability.

## Apply order

```bash
# 1. Control plane: cluster + Cloud + ArgoCD
cd deploy/terraform
terraform init
terraform apply            # creates kind cluster, (optionally) Cloud ns+key, installs ArgoCD

# 2. Workloads: ArgoCD syncs the app-of-apps
kubectl apply -f deploy/argocd/root-app.yaml   # (Terraform can also do this in bootstrap)
# ArgoCD then reconciles everything under argocd/applications/.
```

## Status of this scaffold

| Path | State | Notes |
|---|---|---|
| `terraform/kind-config.yaml` | **concrete** | port maps for Temporal gRPC (7233) + OTLP (4318) |
| `terraform/versions.tf` | **concrete** | provider pins |
| `terraform/main.tf`, `cloud.tf` | skeleton | provider blocks + resources with TODOs |
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
