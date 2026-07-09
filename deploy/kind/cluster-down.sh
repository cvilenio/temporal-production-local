#!/usr/bin/env bash
# =============================================================================
# Tear down the local kind cluster. Leaves the registry container by default so
# pushed images survive a cluster recreate; pass KEEP_REGISTRY=false to remove it.
# Driven by `just kind-down`.
# =============================================================================
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-kind}"
REGISTRY_NAME="${REGISTRY_NAME:-artifact-registry}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-.secrets/kube/${CLUSTER_NAME}.kubeconfig}"
KEEP_REGISTRY="${KEEP_REGISTRY:-true}"

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "==> Deleting kind cluster '${CLUSTER_NAME}'"
  kind delete cluster --name "${CLUSTER_NAME}"
fi
rm -f "${KUBECONFIG_PATH}"

if [ "${KEEP_REGISTRY}" != 'true' ]; then
  echo "==> Removing local registry '${REGISTRY_NAME}'"
  docker rm -f "${REGISTRY_NAME}" >/dev/null 2>&1 || true
else
  echo "==> Keeping registry '${REGISTRY_NAME}' (set KEEP_REGISTRY=false to remove)"
fi
