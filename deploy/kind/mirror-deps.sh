#!/usr/bin/env bash
# =============================================================================
# Mirror third-party platform Helm CHARTS into the local registry so ArgoCD can
# pull them without reaching the public internet.
#
#   charts -> oci://localhost:5001/charts/<name>   (helm pull | helm push)
#
# Container IMAGES are NOT copied here: the local registry is zot with on-demand
# pull-through caching (deploy/kind/zot-config.json), so the charts keep their
# original image refs and the images are fetched-and-cached on first node pull
# (via the containerd certs.d redirects that cluster-up.sh installs). New image
# versions self-populate; no enumeration needed.
#
# Charts still need an explicit push because: cert-manager ships from a CLASSIC
# Helm repo (not an OCI registry, so it can't be pull-through-cached), and we want
# all add-on Applications to use one uniform repoURL (oci://.../charts).
#
# Idempotent; writes nothing into git (all bytes live in the local registry).
# =============================================================================
set -euo pipefail

REGISTRY_PORT="${REGISTRY_PORT:-5001}"
CHARTS_REPO="oci://localhost:${REGISTRY_PORT}/charts"

# Versions come from the single source: config/dependencies.yaml -> deps.env
# (rendered by `just render-deps`, run automatically by the just recipe).
DEPS_ENV="config/.generated/deps.env"
[ -f "$DEPS_ENV" ] || { echo "✖ $DEPS_ENV missing — run 'just render-deps'" >&2; exit 1; }
# shellcheck disable=SC1090
. "$DEPS_ENV"
CERT_MANAGER_REPO="${CERT_MANAGER_REPO:-https://charts.jetstack.io}"
CNPG_REPO="${CNPG_REPO:-https://cloudnative-pg.io/charts}"

command -v helm >/dev/null 2>&1 || { echo "✖ missing required tool: helm" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> Mirroring Helm charts to ${CHARTS_REPO}"
# cert-manager: classic Helm repo (can't be pull-through-cached → explicit copy).
helm pull cert-manager --repo "${CERT_MANAGER_REPO}" --version "${CERT_MANAGER_VERSION}" -d "$tmp" >/dev/null
# cloudnative-pg operator: classic Helm repo (same → explicit copy). Manages the
# orders-db Cluster; its CRDs ship with the chart (crds.create defaults true).
helm pull cloudnative-pg --repo "${CNPG_REPO}" --version "${CNPG_VERSION}" -d "$tmp" >/dev/null
# Temporal Worker Controller: upstream OCI charts (CRDs split from the controller).
helm pull oci://docker.io/temporalio/temporal-worker-controller-crds --version "${WORKER_CONTROLLER_VERSION}" -d "$tmp" >/dev/null
helm pull oci://docker.io/temporalio/temporal-worker-controller --version "${WORKER_CONTROLLER_VERSION}" -d "$tmp" >/dev/null
for tgz in "$tmp"/*.tgz; do
  echo "    push $(basename "$tgz")"
  helm push "$tgz" "${CHARTS_REPO}" --plain-http >/dev/null 2>&1
done

echo
echo "Charts mirrored to localhost:${REGISTRY_PORT}/charts. Container images are"
echo "pull-through-cached on demand by zot — no image copy needed."
