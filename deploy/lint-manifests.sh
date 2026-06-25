#!/usr/bin/env bash
#
# Static validation for the repo's Kubernetes YAML — the schema-aware companion
# to ruff/pyright (Python) and `terraform fmt`/`docker compose config` (their
# planes). Three layers, over the local charts (orders-workers, orders-data,
# orders-api):
#
#   1. helm lint   — templating + best-practice checks.
#   2. sync-wave   — fails if a resource references a Secret/ConfigMap in an
#      equal-or-later ArgoCD sync-wave (the deadlock class from checkpoint 0011;
#      see ADR-0016 + deploy/check-sync-waves.py). helm + python only.
#   3. kubeconform — validates rendered manifests against the Kubernetes OpenAPI
#      schema AND the CRD schemas this repo uses (ArgoCD Application, Temporal
#      WorkerDeployment/Connection, CNPG Cluster), pulled from the datreeio
#      CRDs-catalog. Catches a bad apiVersion/kind/field BEFORE ArgoCD fails the sync.
#
# Scope (k8s manifests only): the rendered orders-workers chart + the plain
# ArgoCD Applications + the kind registry-hosting ConfigMap. Deliberately NOT
# covered here (validated by their own planes): docker-compose*.yml
# (`docker compose config -q`), config/*.yaml data specs (no k8s schema),
# deploy/terraform/kind-config.yaml (kind's own `Cluster` kind), Grafana
# provisioning YAML.
#
# helm is a hard dependency of this repo (cluster recipes use it). kubeconform is
# optional: if it isn't installed we warn and skip its layer rather than fail —
# same posture as the pre-commit hook when the `claude` CLI is absent. Install:
#   brew install kubeconform     (or: go install github.com/yannh/kubeconform/cmd/kubeconform@latest)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# The repo's local Helm charts (templated + schema-validated below).
charts=(deploy/charts/orders-workers deploy/charts/orders-data deploy/charts/orders-api deploy/charts/alloy)

# datreeio CRDs-catalog: {{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json
# carries argoproj.io Application, temporal.io WorkerDeployment/Connection, and
# postgresql.cnpg.io Cluster (orders-app's CNPG datastore).
catalog='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

for chart in "${charts[@]}"; do
  echo "== helm lint $chart =="
  helm lint "$chart"
done

# sync-wave ordering gate (helm + python only; runs regardless of kubeconform).
# Fails if a resource references a Secret/ConfigMap in an equal-or-later wave —
# the deadlock class from checkpoint 0011 (see ADR-0016 + deploy/check-sync-waves.py).
echo "== sync-wave ordering ($(echo "${charts[@]}" | wc -w | tr -d ' ') charts) =="
for chart in "${charts[@]}"; do
  helm template "$chart" | python3 "$repo_root/deploy/check-sync-waves.py" --name "$(basename "$chart")"
done

if ! command -v kubeconform >/dev/null 2>&1; then
  echo "⚠ kubeconform not found — skipped schema validation (chart still helm-linted)." >&2
  echo "  Install: brew install kubeconform" >&2
  exit 0
fi

kc=(kubeconform -strict -summary
    -schema-location default
    -schema-location "$catalog")

for chart in "${charts[@]}"; do
  echo "== kubeconform: rendered $(basename "$chart") chart =="
  helm template "$chart" | "${kc[@]}" -
done

echo "== kubeconform: plain manifests (argocd apps + kind registry-hosting + console-reader RBAC) =="
"${kc[@]}" deploy/argocd deploy/kind/local-registry-hosting.yaml deploy/kind/console-reader-rbac.yaml

echo "manifest validation ok."
