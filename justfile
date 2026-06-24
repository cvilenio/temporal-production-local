# =============================================================================
# justfile — language-agnostic front door for this polyglot repo.
#
# `just` is the recognizable entry point; it delegates Python work to `poe`
# (the Python task layer in pyproject.toml) and shells out for cluster/infra
# work (kind, terraform, registry). As Go/TS/Java land, their native runners
# hang off the same recipes — no Python toolchain needed to drive the repo.
#
#   just            list all recipes
#   just up         local OSS app stack            (-> poe up)
#   just ci         local CI gate + image build    (-> poe ci)
#   just cluster-up kind + local registry          (see deploy/terraform/kind-config.yaml)
# =============================================================================

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Local registry + kind cluster names/ports (kind+registry recipe).
registry_name := "artifact-registry"
registry_port := "5001"
cluster_name := "kind"
kubeconfig := ".secrets/kube/kind.kubeconfig"

# List recipes (default).
default:
    @just --list

# --- Python app stack (delegates to poe) -------------------------------------

# Local OSS server + apps.
up:
    uv run poe up

# Stop the local-OSS stack and drop volumes.
down:
    uv run poe down

# Recreate the local-OSS stack.
fresh:
    uv run poe fresh

# Apps against Temporal Cloud (nonprod).
up-cloud:
    uv run poe up-cloud

# Apps against Temporal Cloud (prod).
up-cloud-prod:
    uv run poe up-cloud-prod

# Stop the Cloud-backed app stack.
down-cloud:
    uv run poe down-cloud

# --- Quality + CI (delegates to poe) -----------------------------------------

# All static checks (lint, format-check, typecheck).
lint:
    uv run poe lint

# Run tests.
test:
    uv run poe test

# Full gate: lint + test.
check:
    uv run poe check

# Lint and autofix.
fix:
    uv run poe fix

# Local CI gate: lint + test + build + push worker images.
ci:
    uv run poe ci

# Build both worker images (tagged with the short git SHA).
build-images:
    uv run poe build-images

# Push both worker images to the local registry.
push-images:
    uv run poe push-images

# Print the image tag (short git SHA) for the current commit.
image-tag:
    @uv run poe image-tag

# --- Local cluster (kind + local registry) -----------------------------------

# Render the dependency manifest -> config/.generated/deps.env (single source of versions).
render-deps:
    @uv run poe render-deps

# Bring up the kind cluster + local registry (kubeconfig under .secrets/).
cluster-up: render-deps
    CLUSTER_NAME={{cluster_name}} REGISTRY_NAME={{registry_name}} REGISTRY_PORT={{registry_port}} \
    KUBECONFIG_PATH={{kubeconfig}} KIND_CONFIG=deploy/terraform/kind-config.yaml \
    bash deploy/kind/cluster-up.sh

# Tear down the kind cluster (keeps the registry; KEEP_REGISTRY=false to remove).
cluster-down:
    CLUSTER_NAME={{cluster_name}} REGISTRY_NAME={{registry_name}} \
    KUBECONFIG_PATH={{kubeconfig}} bash deploy/kind/cluster-down.sh

# Stop, NOT delete: preserves node image cache + zot volume so a deleted cluster's
# tier-3 bootstrap inputs (kindest/node, argo-cd chart + images) are never needed.
# Use before going offline; resume with `just cluster-start`. See ADR-0013.

# Stop the cluster + registry without deleting (offline-resume friendly).
cluster-stop:
    #!/usr/bin/env bash
    set -euo pipefail
    nodes="$(kind get nodes --name {{cluster_name}} 2>/dev/null || true)"
    if [ -z "$nodes" ]; then echo "cluster '{{cluster_name}}' not found — nothing to stop"; exit 0; fi
    echo "Stopping cluster '{{cluster_name}}' + registry (state preserved for offline restart)..."
    docker stop $nodes {{registry_name}} >/dev/null
    echo "Stopped. Resume offline with: just cluster-start"

# Start a previously-stopped cluster + registry — fully offline (everything cached).
cluster-start:
    #!/usr/bin/env bash
    set -euo pipefail
    nodes="$(kind get nodes --name {{cluster_name}} 2>/dev/null || true)"
    if [ -z "$nodes" ]; then echo "cluster '{{cluster_name}}' not found — run 'just cluster-up' (needs internet)"; exit 1; fi
    echo "Starting registry + cluster '{{cluster_name}}' (offline-safe)..."
    docker start {{registry_name}} $nodes >/dev/null
    echo "Waiting for the API to serve (past the startup RBAC race)..."
    for _ in $(seq 1 40); do
      if KUBECONFIG={{kubeconfig}} kubectl get --raw='/readyz' >/dev/null 2>&1; then break; fi
      sleep 3
    done
    KUBECONFIG={{kubeconfig}} kubectl wait --for=condition=Ready nodes --all --timeout=120s || true
    echo "Up. Check workloads: just k get pods -A"

# Mirror third-party charts (cert-manager, worker-controller) into the local registry.
mirror-deps: render-deps
    REGISTRY_PORT={{registry_port}} bash deploy/kind/mirror-deps.sh

# Package the orders-workers chart and push it to the local OCI registry (ArgoCD pulls it from there).
chart-publish:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    helm package deploy/charts/orders-workers -d "$tmp" >/dev/null
    helm push "$tmp"/orders-workers-*.tgz oci://localhost:{{registry_port}}/charts --plain-http
    ver="$(helm show chart deploy/charts/orders-workers | awk '/^version:/{print $2}')"
    echo "published oci://localhost:{{registry_port}}/charts/orders-workers:${ver}"

# kubectl against the kind cluster, e.g. `just k get pods -A`.
k *args:
    @KUBECONFIG={{kubeconfig}} kubectl {{args}}

# Force Headlamp to re-read the kubeconfig now. Headlamp already WATCHES it and
# auto-loads the cluster within ~10s, so this is only an immediate-refresh shortcut.
headlamp-reload:
    @docker restart headlamp >/dev/null && echo "headlamp restarted — kubeconfig reloaded"

# Full local bring-up: cluster + registry, mirror deps, CI (build/push), publish chart,
# pin workers by digest, apply the cluster layer. One command, each step idempotent.
platform-up:
    #!/usr/bin/env bash
    set -euo pipefail
    just cluster-up
    just mirror-deps
    just ci
    just chart-publish
    tag="$(git describe --tags --always --dirty --abbrev=12)"
    wf="$(crane digest localhost:{{registry_port}}/orders-worker-workflow:$tag --insecure)"
    ac="$(crane digest localhost:{{registry_port}}/orders-worker-activity:$tag --insecure)"
    export TF_VAR_worker_image_digests="{\"workflow\":\"$wf\",\"activity\":\"$ac\"}"
    terraform -chdir=deploy/terraform/layers/cluster init -input=false
    terraform -chdir=deploy/terraform/layers/cluster apply -auto-approve
    just headlamp-reload 2>/dev/null || true
    echo "platform up."
    echo "  Console (all UIs): http://localhost:8086   ArgoCD: http://localhost:8088   Headlamp: http://localhost:8087"
    echo "  (If the console stack wasn't running, start it with 'just up-cloud' then 'just headlamp-reload'.)"
