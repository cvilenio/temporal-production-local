#!/usr/bin/env bash
# =============================================================================
# Bring up the local kind cluster wired to a local OCI registry.
#
# This is the production-faithful local substrate: a real Kubernetes API (kind)
# plus a real registry with push/pull semantics, so image refs and the
# ArgoCD/Worker-Controller pull path behave exactly as they would on GKE +
# Artifact Registry. Driven by `just cluster-up`. Implements the upstream
# kind+registry recipe (https://kind.sigs.k8s.io/docs/user/local-registry/).
#
# Env (set by the justfile; defaults here keep the script standalone):
#   CLUSTER_NAME   kind cluster name              (default: temporal-platform)
#   REGISTRY_NAME  registry container name        (default: kind-registry)
#   REGISTRY_PORT  host port for the registry     (default: 5001)
#   KUBECONFIG_PATH where kind writes kubeconfig   (default: .secrets/kube/<cluster>.kubeconfig)
#   KIND_CONFIG    kind cluster config file       (default: deploy/terraform/kind-config.yaml)
# =============================================================================
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-temporal-platform}"
REGISTRY_NAME="${REGISTRY_NAME:-kind-registry}"
REGISTRY_PORT="${REGISTRY_PORT:-5001}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-.secrets/kube/${CLUSTER_NAME}.kubeconfig}"
KIND_CONFIG="${KIND_CONFIG:-deploy/terraform/kind-config.yaml}"

# 1. Local registry container (idempotent): zot — an OCI-native registry that both
#    HOSTS our pushes (worker images, charts) AND PULL-THROUGH-CACHES upstreams
#    on demand (deploy/kind/zot-config.json sync extension). So third-party images
#    are fetched-and-cached on first use, not pre-copied. Served over HTTP (the
#    nginx TLS proxy in the cluster layer fronts it for ArgoCD).
# zot version comes from the single source: config/dependencies.yaml -> deps.env.
DEPS_ENV="config/.generated/deps.env"
[ -f "$DEPS_ENV" ] || { echo "✖ $DEPS_ENV missing — run 'just render-deps'" >&2; exit 1; }
# shellcheck disable=SC1090
. "$DEPS_ENV"
ZOT_VERSION="${ZOT_VERSION:-v2.1.18}"
case "$(uname -m)" in
  x86_64 | amd64) zot_arch=amd64 ;;
  arm64 | aarch64) zot_arch=arm64 ;;
  *) echo "✖ unsupported arch for zot: $(uname -m)" >&2; exit 1 ;;
esac
ZOT_IMAGE="ghcr.io/project-zot/zot-linux-${zot_arch}:${ZOT_VERSION}"
if [ "$(docker inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null || true)" != 'true' ]; then
  echo "==> Starting zot registry '${REGISTRY_NAME}' (${ZOT_IMAGE}) on 127.0.0.1:${REGISTRY_PORT}"
  docker run -d --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" \
    --network bridge --name "${REGISTRY_NAME}" \
    -v "${REGISTRY_NAME}-data:/var/lib/registry" \
    -v "$(pwd)/deploy/kind/zot-config.json:/etc/zot/config.json:ro" \
    "${ZOT_IMAGE}" serve /etc/zot/config.json
fi

# 2. kind cluster (idempotent). Kubeconfig lands under the hardened .secrets dir.
mkdir -p "$(dirname "${KUBECONFIG_PATH}")"
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "==> kind cluster '${CLUSTER_NAME}' already exists"
  kind export kubeconfig --name "${CLUSTER_NAME}" --kubeconfig "${KUBECONFIG_PATH}"
else
  echo "==> Creating kind cluster '${CLUSTER_NAME}'"
  kind create cluster --name "${CLUSTER_NAME}" \
    --config "${KIND_CONFIG}" --kubeconfig "${KUBECONFIG_PATH}" --wait 120s
fi

# 3. Tell each node's containerd where to find images (certs.d hosts.toml):
#    (a) localhost:<port> -> our registry (the address we push to).
#    (b) upstream hosts   -> our registry as a pull-through MIRROR, with the real
#        upstream kept as a fallback. Combined with deploy/kind/mirror-deps.sh
#        (which copies the bytes locally), third-party images resolve from the
#        local registry while their charts keep their ORIGINAL image refs — no
#        per-chart image overrides. For strict air-gap, drop the `server =`
#        fallback lines below.
UPSTREAM_MIRRORS=(quay.io registry.k8s.io docker.io)
for node in $(kind get nodes --name "${CLUSTER_NAME}"); do
  d="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
  docker exec "${node}" mkdir -p "${d}"
  printf '[host."http://%s:5000"]\n  capabilities = ["pull", "resolve", "push"]\n' \
    "${REGISTRY_NAME}" | docker exec -i "${node}" cp /dev/stdin "${d}/hosts.toml"
  for host in "${UPSTREAM_MIRRORS[@]}"; do
    case "${host}" in
      docker.io) upstream="https://registry-1.docker.io" ;;
      *) upstream="https://${host}" ;;
    esac
    d="/etc/containerd/certs.d/${host}"
    docker exec "${node}" mkdir -p "${d}"
    printf 'server = "%s"\n\n[host."http://%s:5000"]\n  capabilities = ["pull", "resolve"]\n' \
      "${upstream}" "${REGISTRY_NAME}" | docker exec -i "${node}" cp /dev/stdin "${d}/hosts.toml"
  done
done

# 4. Join the registry to the kind network so nodes can reach it by name.
if [ "$(docker inspect -f '{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}")" = 'null' ]; then
  echo "==> Connecting '${REGISTRY_NAME}' to the kind network"
  docker network connect kind "${REGISTRY_NAME}"
fi

# 5. Advertise the registry to in-cluster tooling (KEP-1755 ConfigMap).
KUBECONFIG="${KUBECONFIG_PATH}" kubectl apply -f deploy/kind/local-registry-hosting.yaml

# 6. Make the registry reachable from IN-CLUSTER pods (ArgoCD's repo-server pulls
#    OCI Helm charts over the pod network — certs.d only covers node containerd).
#    A selector-less Service + a hand-maintained EndpointSlice point at the
#    registry container's kind-network IP. Re-derived every run, so it self-heals
#    if the container's IP changes. In-cluster name: kind-registry.kube-public.svc:5000
REG_IP="$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' "${REGISTRY_NAME}")"
echo "==> Wiring in-cluster Service kind-registry.kube-public.svc:5000 -> ${REG_IP}:5000"
KUBECONFIG="${KUBECONFIG_PATH}" kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${REGISTRY_NAME}
  namespace: kube-public
spec:
  ports:
    - name: registry
      port: 5000
      targetPort: 5000
      protocol: TCP
---
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: ${REGISTRY_NAME}
  namespace: kube-public
  labels:
    kubernetes.io/service-name: ${REGISTRY_NAME}
addressType: IPv4
ports:
  - name: registry
    port: 5000
    protocol: TCP
endpoints:
  - addresses: ["${REG_IP}"]
    conditions:
      ready: true
EOF

echo
echo "kind '${CLUSTER_NAME}' is up."
echo "  registry   : localhost:${REGISTRY_PORT} (host push)  |  ${REGISTRY_NAME}:5000 (node pull)  |  ${REGISTRY_NAME}.kube-public.svc:5000 (in-cluster)"
echo "  kubeconfig : ${KUBECONFIG_PATH}"
echo "  next       : just ci   then   terraform -chdir=deploy/terraform/layers/cluster apply"
