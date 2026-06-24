#!/usr/bin/env bash
#
# Static validation for the repo's Kubernetes YAML — the schema-aware companion
# to ruff/pyright (Python) and `terraform fmt`/`docker compose config` (their
# planes). Two layers:
#
#   1. helm lint   — the orders-workers chart (templating + best-practice checks).
#   2. kubeconform — validates rendered manifests against the Kubernetes OpenAPI
#      schema AND the CRD schemas this repo uses (ArgoCD Application, Temporal
#      WorkerDeployment/Connection), pulled from the datreeio CRDs-catalog. This
#      is what catches a bad apiVersion/kind/field BEFORE ArgoCD fails the sync.
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

chart="deploy/charts/orders-workers"

# datreeio CRDs-catalog: {{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json
# carries argoproj.io Application + temporal.io WorkerDeployment/Connection.
catalog='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

echo "== helm lint $chart =="
helm lint "$chart"

if ! command -v kubeconform >/dev/null 2>&1; then
  echo "⚠ kubeconform not found — skipped schema validation (chart still helm-linted)." >&2
  echo "  Install: brew install kubeconform" >&2
  exit 0
fi

kc=(kubeconform -strict -summary
    -schema-location default
    -schema-location "$catalog")

echo "== kubeconform: rendered orders-workers chart =="
helm template "$chart" | "${kc[@]}" -

echo "== kubeconform: plain manifests (argocd applications + kind registry-hosting) =="
"${kc[@]}" deploy/argocd deploy/kind/local-registry-hosting.yaml

echo "manifest validation ok."
