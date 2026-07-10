# =============================================================================
# justfile — cross-language front door for this polyglot repo.
#
# BOUNDARY TEST (keep this honest):
#   * Shells docker/compose/terraform/kubectl/helm, OR touches >1 language's
#     artifacts  ->  it's a `just` recipe (here).
#   * Shells the Python toolchain (ruff/pyright/pytest)  ->  it's a `poe` task
#     (pyproject.toml [tool.poe.tasks]). `just` fans poe (and future go/ts/java
#     leaf runners) in; no language's runner owns shared infra.
#
#   just            list all recipes
#   just legacy-up  local OSS app stack (all-on-host fallback)
#   just ci         python gate + image build/push
#   just kind-up kind + local registry             (see deploy/terraform/kind-config.yaml)
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

# --- Quality + CI (python leaf via poe; infra here; just fans in) -------------

# Render scripts: python-implemented, infra-DOMAIN — driven from just, not poe.
render-deps:
    @uv run python compose/scripts/render-deps.py

# Audit: assert every native version pin matches config/dependencies.yaml (the
# single source of truth, ADR-0025). Offline, stdlib + pyyaml. Tier-1/2 drift fails;
# Tier-3 (code deps) warns. Wired into `lint` so `just check` catches drift pre-push.
versions-audit:
    @uv run python compose/scripts/versions-audit.py

# Report Tier-1 (Temporal) pinned-vs-latest-stable from upstream registries (PyPI,
# Docker Hub, GitHub releases, Terraform Registry). NETWORK (Resolve tier, ADR-0013)
# — deliberately NOT in any gate; upstream releasing must never break CI. `--strict`
# makes it exit non-zero when anything is behind (honors GITHUB_TOKEN if set).
versions-upstream *ARGS:
    @uv run python compose/scripts/versions-upstream.py {{ARGS}}

render-oss-bootstrap:
    @uv run python compose/scripts/render-oss-bootstrap.py

# helm lint + kubeconform on the k8s manifests (soft-skips kubeconform if absent).
# Runs under `uv run` so the script's python3 (check-sync-waves.py needs pyyaml)
# resolves to the venv, not bare system python.
lint-manifests:
    uv run bash deploy/lint-manifests.sh

# Protobuf codegen — buf + remote plugins (network/Resolve tier, ADR-0013). The
# generated *_pb2.py/.pyi are committed (orders kernel ships them in the wheel),
# so this only runs when contracts change, never on the offline gate.
proto-gen:
    cd libs/orders/proto && buf generate

# Lint the proto contracts. Wired into the static gate.
proto-lint:
    cd libs/orders/proto && buf lint

# Check the contracts for wire-breaking changes vs main. Run on branches once the
# baseline exists on main; skipped from the gate because the first introduction
# has no baseline to compare against. This is the payload-compatibility guard.
proto-breaking:
    cd libs/orders/proto && buf breaking --against '../../../.git#branch=main,subdir=libs/orders/proto'

# Fail if committed generated code has drifted from the .proto sources (networked
# CI lane only — needs buf + remote plugins; do not add to the offline gate).
proto-check: proto-gen
    git diff --exit-code -- libs/orders/python/orders/_pb

# Verify config/domains/*.yaml against namespaces.yaml, worker dirs, queues, and charts.
verify-domains:
    uv run python compose/scripts/verify-domains.py

# Domain doctor — single domain by filename stem or domain key.
verify-domain NAME:
    uv run python compose/scripts/verify-domains.py {{NAME}}

# Write a commented starter config/domains/<domain>.yaml for human editing.
new-domain NAME:
    uv run python compose/scripts/new_domain.py --name {{NAME}}

# Idempotent generator — reads config/domains/<name>.yaml (no LANG flag).
scaffold-domain NAME:
    uv run python compose/scripts/scaffold_domain.py --name {{NAME}}

# Adopt a domain end-to-end: verify -> lock -> build -> push -> chart-publish -> apply -> bootstrap.
adopt-domain NAME:
    #!/usr/bin/env bash
    set -euo pipefail
    just verify-domain {{NAME}}
    uv lock
    just build-domain-images {{NAME}}
    just push-domain-images {{NAME}}
    just chart-publish
    L="deploy/terraform/layers/cluster"
    tag="$(git describe --tags --always --dirty --abbrev=12)"
    backend="$(terraform -chdir=$L output -raw temporal_backend 2>/dev/null || echo oss)"
    oss_enabled="$(terraform -chdir=$L output -raw oss_server_enabled 2>/dev/null || echo true)"
    export TF_VAR_worker_image_digests="$(just worker-digests-json {{NAME}})"
    export TF_VAR_orders_api_image_digest="$(KUBECONFIG={{kubeconfig}} kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | cut -d@ -f2 || crane digest localhost:{{registry_port}}/orders-api:$tag --insecure)"
    export TF_VAR_autoscaler_image_digest="$(KUBECONFIG={{kubeconfig}} kubectl -n orders get deploy temporal-worker-autoscaler -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | cut -d@ -f2 || crane digest localhost:{{registry_port}}/temporal-worker-autoscaler:$tag --insecure)"
    export TF_VAR_temporal_backend="$backend"
    export TF_VAR_oss_server_enabled="$oss_enabled"
    terraform -chdir=$L apply -auto-approve
    just bootstrap-oss-namespaces
    echo "adopt-domain {{NAME}} complete (backend=$backend)."

# Ensure every OSS namespace from config/temporal/namespaces.yaml exists on the in-cluster server.
bootstrap-oss-namespaces:
    #!/usr/bin/env bash
    set -euo pipefail
    just render-oss-bootstrap
    set -a; . config/temporal/.generated/oss-bootstrap.env; set +a
    ADDR="temporal-internal-frontend:7236"
    for NS in ${OSS_DOMAINS:-}; do
      ENV_KEY="$(echo "$NS" | tr '-' '_')"
      eval "RET=\${OSS_RETENTION_${ENV_KEY}:-30}"
      if KUBECONFIG={{kubeconfig}} kubectl -n temporal exec deploy/temporal-admintools -- \
        temporal operator namespace describe -n "$NS" --address "$ADDR" >/dev/null 2>&1; then
        echo "namespace $NS already exists"
      else
        echo "creating namespace $NS (retention ${RET}d)"
        KUBECONFIG={{kubeconfig}} kubectl -n temporal exec deploy/temporal-admintools -- \
          temporal operator namespace create -n "$NS" --retention "${RET}d" --address "$ADDR"
      fi
    done

# All static checks: python (poe) + k8s manifests (helm/kubeconform) + proto lint
# + dependency-version drift (versions-audit vs config/dependencies.yaml)
# + domain descriptor consistency (verify-domains).
lint:
    uv run poe lint
    just lint-manifests
    just proto-lint
    just versions-audit
    just verify-domains
    just lint-domain-templates

# Compile-check Go + typecheck TypeScript domain templates.
lint-domain-templates:
    uv run python compose/scripts/lint_domain_templates.py

# Run tests (python leaf).
test:
    uv run poe test

# Lint and autofix (python leaf).
fix:
    uv run poe fix

# Full gate: lint (python + manifests) + test.
check: lint test

# Compile appkit + all Java apps (Gradle leaf).
java-build:
    ./gradlew build

# Local CI gate: gate + build + push worker/api images.
ci: check build-images push-images

# --- Local OSS app stack (compose orchestration — cross-language) -------------

# Legacy Compose-only OSS fallback: server + app tier on the host (no workers).
# NEITHER plane in the ADR-0014 sense — it folds app-tier/OSS-server duties that
# normally live on kind onto the host instead. Prefer host-up + kind.
legacy-up: render-oss-bootstrap grafana-plugins
    set -a; . config/local-oss.env; set +a; docker compose -f docker-compose.yml -f compose/host-apptier.yml -f compose/oss-server.yml up --build

# Stop the legacy Compose-only OSS stack and drop volumes (also sweeps a stray
# default-project + the shared net).
legacy-down:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml -f compose/oss-server.yml down -v --remove-orphans; docker compose -p "${PWD##*/}" -f docker-compose.yml -f compose/host-apptier.yml -f compose/oss-server.yml down -v --remove-orphans || true; docker network rm temporal-network 2>/dev/null || true

# Recreate the legacy Compose-only OSS stack.
legacy-fresh: legacy-down legacy-up

# Host plane (ADR-0014): visibility + console + mock-api for the kind path.
# Bring up FIRST before live kind testing. Detached — tail logs with `just host-logs`.
# backend selects Cloud vs OSS connection profile for the console.
host-up backend="": headlamp-plugins grafana-plugins
    #!/usr/bin/env bash
    set -euo pipefail
    backend="{{backend}}"; if [ -z "$backend" ]; then backend="$(terraform -chdir=deploy/terraform/layers/cluster output -raw temporal_backend 2>/dev/null || echo cloud)"; fi
    case "$backend" in cloud|oss) ;; *) echo "backend must be 'cloud' or 'oss'"; exit 1;; esac
    profile=".secrets/keys/cloud.env"
    [ "$backend" = "oss" ] && profile="config/local-oss-kind.env"
    set -a; . "$profile"; set +a
    docker compose -f docker-compose.yml up -d --build

# Stop the host plane and drop volumes.
host-down:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml down -v --remove-orphans; docker compose -p "${PWD##*/}" -f docker-compose.yml -f compose/host-apptier.yml down -v --remove-orphans || true; docker network rm temporal-network 2>/dev/null || true

# Pause the host plane, keep volumes.
host-stop:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml stop

# Resume a previously-stopped host plane.
host-start:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml start

# Tear down and recreate the host plane.
host-refresh backend="":
    just host-down
    just host-up {{backend}}

# Follow host-plane compose logs.
host-logs:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml logs -f

# --- Worker/API images (docker — cross-language artifact build) ---------------
# Tagged with git-describe so a build is immutable + uniquely addressable; a
# dirty tree carries a `-dirty` suffix. Deploys pin by DIGEST (image-digests);
# the tag is for humans. REGISTRY defaults to the local registry from kind-up.

# Build/push worker images for one domain only (adopt-domain uses this to avoid churn).
build-domain-images NAME:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    uv run python compose/scripts/build_domain_images.py build \
      --registry "$REGISTRY" --tag "$TAG" --domain {{NAME}}

push-domain-images NAME:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    uv run python compose/scripts/build_domain_images.py push \
      --registry "$REGISTRY" --tag "$TAG" --domain {{NAME}}

# Build the worker images + orders-api, tagged <registry>/<name>:<git-describe>.
build-images:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    uv run python compose/scripts/build_domain_images.py build --registry "$REGISTRY" --tag "$TAG"
    docker build -f images/python.Dockerfile \
      --build-arg APP_GROUP=orders-api \
      --build-arg APP_PATH=apps/business/orders-api/python \
      --build-arg APP_MODULE=main:app \
      --build-arg APP_CMD=uvicorn \
      -t "$REGISTRY/orders-api:$TAG" .
    docker build -f images/go.Dockerfile \
      --build-arg APP_PATH=apps/platform/temporal-worker-autoscaler/go \
      -t "$REGISTRY/temporal-worker-autoscaler:$TAG" .
    echo "Built worker images from config/domains/*.yaml, orders-api:$TAG, temporal-worker-autoscaler:$TAG"

# Push the worker images + orders-api to the local registry.
push-images:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    uv run python compose/scripts/build_domain_images.py push --registry "$REGISTRY" --tag "$TAG"
    docker push "$REGISTRY/orders-api:$TAG"
    docker push "$REGISTRY/temporal-worker-autoscaler:$TAG"
    echo "Pushed worker images from config/domains/*.yaml, orders-api:$TAG, temporal-worker-autoscaler:$TAG"

# Print the image tag (git-describe) for the current tree.
image-tag:
    @git describe --tags --always --dirty --abbrev=12

# Print pushed image digests (name=sha256:...) for deploy-by-digest.
image-digests:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    uv run python compose/scripts/build_domain_images.py digests --registry "$REGISTRY" --tag "$TAG"
    echo "orders-api=$(crane digest "$REGISTRY/orders-api:$TAG" --insecure)"
    echo "temporal-worker-autoscaler=$(crane digest "$REGISTRY/temporal-worker-autoscaler:$TAG" --insecure)"

# JSON map of worker digests keyed <domain>-<profile> for TF_VAR_worker_image_digests.
# With ADOPT set, registry digests for that domain + live digests for all others (no churn).
worker-digests-json ADOPT="":
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    if [ -n "{{ADOPT}}" ]; then
      uv run python compose/scripts/build_domain_images.py digests-json \
        --registry "$REGISTRY" --tag "$TAG" --adopt {{ADOPT}} \
        --kubeconfig {{kubeconfig}}
    else
      uv run python compose/scripts/build_domain_images.py digests-json \
        --registry "$REGISTRY" --tag "$TAG"
    fi

# --- Lifecycle grammar (<scope>-<verb>) ---------------------------------------
#
# Five scopes (ADR-0014, workloads split from substrate):
#
#   host       — Compose visibility/console plane (detached; `just host-logs` to tail)
#   kind       — kind nodes + local OCI registry (empty substrate)
#   workloads  — ArgoCD apps / TF cluster layer on kind
#   cluster    — kind + workloads (whole kind side)
#   platform   — host + cluster (everything)
#
# Verbs: host/cluster/platform get up/down/stop/start/refresh; kind/workloads are
# up/down only (kind pause IS cluster-stop; workloads pause/refresh collapse to
# cluster-stop / workloads-up).
#
# Temporal backend is always `[cloud|oss]` on recipes that take it (never a
# separate command tree).
#
# --- kind (substrate) --------------------------------------------------------

# Bring up kind + local registry only (empty cluster — no ArgoCD apps yet).
# kubeconfig → .secrets/kube/. For apps use `just cluster-up`.
kind-up: render-deps
    CLUSTER_NAME={{cluster_name}} REGISTRY_NAME={{registry_name}} REGISTRY_PORT={{registry_port}} \
    KUBECONFIG_PATH={{kubeconfig}} KIND_CONFIG=deploy/terraform/kind-config.yaml \
    bash deploy/kind/cluster-up.sh

# Release the controller's ownership of the Cloud Worker Deployments before a
# teardown — the graceful-decommission step. The controller's ManagerIdentity is
# suffixed with the temporal-system namespace UID (per-cluster ownership, by
# design — stops a stale cluster clobbering a live one). That UID is regenerated
# on every fresh kind cluster, so without releasing, the NEXT cluster's controller
# can't reclaim routing (Current stays pinned to a dead version → workflows sit
# pending). Unset hands ownership back so the next controller claims cleanly on an
# empty identity. Best-effort + Cloud-only: skipped silently without cloud creds
# (e.g. an OSS-backed cluster). See ADR-0004 / docs/runbooks/argocd-stuck-sync.md.
release-worker-deployments:
    #!/usr/bin/env bash
    set -euo pipefail
    env=".secrets/keys/cloud.env"
    [ -f "$env" ] || { echo "no cloud creds ($env) — skipping Worker Deployment release"; exit 0; }
    set -a; . "$env"; set +a
    [ -n "${TEMPORAL_API_KEY:-}" ] || { echo "no TEMPORAL_API_KEY — skipping release"; exit 0; }
    A=(--address "$TEMPORAL_ADDRESS" --namespace "$TEMPORAL_NAMESPACE" --api-key "$TEMPORAL_API_KEY" --tls --yes)
    for wd in orders/orders-workflow-python orders/orders-activity-python orders/orders-finalization-java; do
      echo "releasing ManagerIdentity: $wd"
      temporal worker deployment manager-identity unset "${A[@]}" --deployment-name "$wd" 2>/dev/null \
        || echo "  (skip: $wd not found or already released)"
    done

# Releases Cloud Worker Deployment ownership first (graceful decommission) so the
# next cluster's controller can reclaim routing — see release-worker-deployments.
# Tear down kind + registry (KEEP_REGISTRY=false removes the registry).
kind-down: release-worker-deployments
    CLUSTER_NAME={{cluster_name}} REGISTRY_NAME={{registry_name}} \
    KUBECONFIG_PATH={{kubeconfig}} bash deploy/kind/cluster-down.sh

# Stop, NOT delete: preserves node image cache + zot volume so a deleted cluster's
# tier-3 bootstrap inputs (kindest/node, argo-cd chart + images) are never needed.
# Use before going offline; resume with `just cluster-start`. See ADR-0013.

# Re-derive artifact-registry's kind-network IP and repair kube-public EndpointSlice if stale.
_registry-endpoint-heal:
    #!/usr/bin/env bash
    set -euo pipefail
    # Ensure the registry is up first so heal is self-sufficient from any state
    # (kind-ready's in-place branch, or a manually-stopped registry). The IP is
    # not populated the instant the container starts, so derive it with a short retry.
    if [ "$(docker inspect -f '{{ "{{" }}.State.Status{{ "}}" }}' {{registry_name}} 2>/dev/null || echo missing)" != "running" ]; then
      echo "Registry '{{registry_name}}' not running — starting it..."
      docker start {{registry_name}}
    fi
    ip=""
    for _ in $(seq 1 10); do
      ip="$(docker inspect -f '{{ "{{" }}.NetworkSettings.Networks.kind.IPAddress{{ "}}" }}' {{registry_name}} 2>/dev/null || true)"
      [ -n "$ip" ] && break
      sleep 1
    done
    if [ -z "$ip" ]; then
      echo "ERROR: could not derive '{{registry_name}}' IP on the kind network (does the container exist? run 'just kind-up')"
      exit 1
    fi
    current="$(KUBECONFIG={{kubeconfig}} kubectl get endpointslice artifact-registry -n kube-public \
      -o jsonpath='{.endpoints[0].addresses[0]}' 2>/dev/null || true)"
    if [ "$current" = "$ip" ]; then
      echo "Registry EndpointSlice already correct ($ip)."
    else
      echo "Repairing registry EndpointSlice: ${current:-<missing>} -> $ip"
      KUBECONFIG={{kubeconfig}} kubectl patch endpointslice artifact-registry -n kube-public --type merge \
        -p "{\"endpoints\":[{\"addresses\":[\"$ip\"],\"conditions\":{\"ready\":true}}]}"
    fi

# Poll ArgoCD Applications until all are Synced/Healthy (~180s); warn on timeout, do not fail.
_wait-argocd:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Waiting for ArgoCD Applications (Synced/Healthy, up to 180s)..."
    deadline=$((SECONDS + 180))
    while [ "$SECONDS" -lt "$deadline" ]; do
      bad="$(KUBECONFIG={{kubeconfig}} kubectl get applications -n argocd \
        -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status \
        --no-headers 2>/dev/null | awk '$2!="Synced" || $3!="Healthy" {print}' || true)"
      total="$(KUBECONFIG={{kubeconfig}} kubectl get applications -n argocd --no-headers 2>/dev/null | wc -l | tr -d ' ')"
      if [ "${total:-0}" -gt 0 ] && [ -z "$bad" ]; then
        echo "ArgoCD recovered: all $total Applications Synced/Healthy."
        KUBECONFIG={{kubeconfig}} kubectl get applications -n argocd
        exit 0
      fi
      sleep 5
    done
    echo "WARN: ArgoCD not fully Synced/Healthy after 180s."
    KUBECONFIG={{kubeconfig}} kubectl get applications -n argocd || true
    echo "Inspect: docs/runbooks/kind-restart-registry-recovery.md"

# Print kind health signals (ArgoCD + non-Running/Completed pods).
_kind-health-report:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "=== ArgoCD Applications ==="
    KUBECONFIG={{kubeconfig}} kubectl get applications -n argocd || true
    echo "=== Pods not Running/Completed ==="
    bad_pods="$(KUBECONFIG={{kubeconfig}} kubectl get pods -A --no-headers 2>/dev/null \
      | awk '$4!="Running" && $4!="Completed" {print}' || true)"
    if [ -z "$bad_pods" ]; then
      echo "(none — all pods Running or Completed)"
    else
      echo "$bad_pods"
    fi

# Idempotent known-good entry: start stopped cluster or repair stale registry endpoint in place.
kind-ready:
    #!/usr/bin/env bash
    set -euo pipefail
    nodes="$(kind get nodes --name {{cluster_name}} 2>/dev/null || true)"
    if [ -z "$nodes" ]; then
      echo "cluster '{{cluster_name}}' not found — run 'just kind-up' (needs internet)"
      exit 1
    fi
    stopped=false
    for node in $nodes; do
      state="$(docker inspect -f '{{ "{{" }}.State.Status{{ "}}" }}' "$node" 2>/dev/null || echo missing)"
      if [ "$state" != "running" ]; then stopped=true; break; fi
    done
    if $stopped; then
      echo "Cluster stopped — starting via cluster-start..."
      just cluster-start
    else
      echo "Cluster running — repairing registry endpoint in place..."
      just _registry-endpoint-heal
      just _wait-argocd
    fi
    just _kind-health-report

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
    if [ -z "$nodes" ]; then echo "cluster '{{cluster_name}}' not found — run 'just kind-up' (needs internet)"; exit 1; fi
    echo "Starting registry + cluster '{{cluster_name}}' (offline-safe)..."
    docker start {{registry_name}} $nodes >/dev/null
    echo "Waiting for the API to serve (past the startup RBAC race)..."
    for _ in $(seq 1 40); do
      if KUBECONFIG={{kubeconfig}} kubectl get --raw='/readyz' >/dev/null 2>&1; then break; fi
      sleep 3
    done
    KUBECONFIG={{kubeconfig}} kubectl wait --for=condition=Ready nodes --all --timeout=120s || true
    just _registry-endpoint-heal
    just _wait-argocd
    echo "Up. Check workloads: just k get pods -A"

# Mirror third-party charts (cert-manager, worker-controller) into the local registry.
mirror-deps: render-deps
    REGISTRY_PORT={{registry_port}} bash deploy/kind/mirror-deps.sh

# Package the local charts (orders-workers + orders-data + orders-api + the alloy
# log agent + the temporal-worker-autoscaler controller) and push them to the local
# OCI registry (ArgoCD pulls them from there).
chart-publish: render-oss-bootstrap
    #!/usr/bin/env bash
    set -euo pipefail
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    # temporal-server is a wrapper over the official Temporal chart: vendor the
    # subchart from its classic repo (host has network; ArgoCD then pulls the
    # self-contained wrapper offline — ADR-0013), and embed the namespace/search-
    # attribute bootstrap spec rendered from config/temporal/namespaces.yaml (ADR-0007).
    mkdir -p deploy/charts/temporal-server/files
    cp config/temporal/.generated/oss-bootstrap.env deploy/charts/temporal-server/files/oss-bootstrap.env
    helm dependency build deploy/charts/temporal-server >/dev/null
    charts=()
    for d in deploy/charts/*-workers; do
      [ -d "$d" ] || continue
      charts+=("$(basename "$d")")
    done
    charts+=(orders-data orders-api alloy temporal-worker-autoscaler temporal-server)
    for chart in "${charts[@]}"; do
      helm package "deploy/charts/$chart" -d "$tmp" >/dev/null
      helm push "$tmp/$chart"-*.tgz oci://localhost:{{registry_port}}/charts --plain-http
      ver="$(helm show chart "deploy/charts/$chart" | awk '/^version:/{print $2}')"
      echo "published oci://localhost:{{registry_port}}/charts/$chart:${ver}"
    done

# kubectl against the kind cluster, e.g. `just k get pods -A`.
k *args:
    @KUBECONFIG={{kubeconfig}} kubectl {{args}}

# PHYSICALLY reset orders-db: delete the CNPG Cluster + its PVCs; ArgoCD selfHeal
# re-syncs orders-app and CNPG bootstraps a fresh, empty DB. DESTRUCTIVE — drops
# all order data. This is the *physical* reset (drop the datastore); for a
# *logical* reset that only truncates the app tables, use the console's "Reset
# demo" action or `POST /admin/reset` on orders-api. See docs/RUNMODES.md.
orders-db-reset:
    #!/usr/bin/env bash
    set -euo pipefail
    just preflight
    read -r -p "Delete orders-db Cluster + PVCs in namespace 'orders'? ALL order data will be lost. Type 'yes': " ans
    [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
    KUBECONFIG={{kubeconfig}} kubectl -n orders delete cluster.postgresql.cnpg.io orders-db --ignore-not-found
    KUBECONFIG={{kubeconfig}} kubectl -n orders delete pvc -l cnpg.io/cluster=orders-db --ignore-not-found
    echo "Deleted. ArgoCD will re-sync orders-app; CNPG bootstraps a fresh orders-db."

# =============================================================================
# Temporal backend switchover + OSS-server lifecycle (ADR-0003 / -0005).
#
# The switch is a DELIBERATE HARD SWITCH — no shadowing / Cloud↔OSS replication.
# The two directions are asymmetric: Cloud→OSS orphans Cloud workflows (Cloud
# preserves them; they resume on switch-back), OSS→Cloud leaves OSS workflows in
# the local Postgres (destroyed only by the explicit temporal-server-down /
# temporal-db-reset). The OSS server's existence is DECOUPLED from the toggle:
# switching to Cloud never prunes it. See docs/RUNMODES.md.
# =============================================================================

# Switch the live cluster's Temporal backend. THE official switchover process:
# detects open workflows on the current backend and prompts y/n before an
# orphaning/lossy switch; --drain waits for them to finish first; --yes skips the
# prompt (automation). Repoints workers/apps (apply), then recreates the host
# console with the target profile. Does NOT rebuild worker images (passes the
# current digests through) or prune the OSS server.
switch-backend target *FLAGS:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{target}}" in cloud|oss) ;; *) echo "usage: just switch-backend <cloud|oss> [--drain|--yes]"; exit 1;; esac
    FLAGS="{{FLAGS}}"; DRAIN=false; YES=false
    case "$FLAGS" in *--drain*) DRAIN=true;; esac
    case "$FLAGS" in *--yes*) YES=true;; esac
    uv run poe preflight-console
    L="deploy/terraform/layers/cluster"
    cur="$(terraform -chdir=$L output -raw temporal_backend 2>/dev/null || echo cloud)"
    # Default to the NON-destructive value on a failed read: a switch must never
    # prune the OSS server (only `temporal-server-down` does). Guessing false here
    # would set oss_server_enabled=false → Terraform destroys the server + CNPG.
    srv="$(terraform -chdir=$L output -raw oss_server_enabled 2>/dev/null || echo true)"
    reconcile_console() {
      local target="$1"
      local profile
      local current=""
      current="$(curl -sf --max-time 3 http://localhost:8086/healthz | uv run python -c 'import sys,json; print(json.load(sys.stdin).get("backend",""))' 2>/dev/null || true)"
      if [ -n "$current" ] && [ "$current" = "$target" ]; then
        echo "platform-console already aligned on backend '$target'."
        return 0
      fi
      profile=".secrets/keys/cloud.env"; [ "$target" = "oss" ] && profile="config/local-oss-kind.env"
      set -a; . "$profile"; set +a
      docker compose -f docker-compose.yml up -d --no-deps --force-recreate platform-console
      just headlamp-reload 2>/dev/null || true
      echo "Console recreated with the ${target} profile."
    }
    if [ "$cur" = "{{target}}" ]; then
      echo "Cluster already on backend '{{target}}' - reconciling platform-console to the {{target}} profile."
      reconcile_console "{{target}}"
      exit 0
    fi
    echo "Switching backend: $cur -> {{target}}"

    # Detect open workflows on the CURRENT backend (best-effort). Cloud: source the
    # Cloud profile. OSS: query the frontend via the non-mTLS internal endpoint from
    # inside a frontend pod. If the count can't be determined, treat as "unknown" and
    # still prompt (fail safe).
    count="?"
    if [ "$cur" = "cloud" ] && [ -f .secrets/keys/cloud.env ]; then
      set -a; . .secrets/keys/cloud.env; set +a
      count="$(temporal workflow count --address "$TEMPORAL_ADDRESS" --namespace "$TEMPORAL_NAMESPACE" --api-key "$TEMPORAL_API_KEY" --tls -q 'ExecutionStatus="Running"' 2>/dev/null | grep -oE '[0-9]+' | head -1 || echo '?')"
    elif [ "$cur" = "oss" ]; then
      count="$(KUBECONFIG={{kubeconfig}} kubectl -n temporal exec deploy/temporal-admintools -- \
        temporal workflow count --address temporal-internal-frontend:7236 --namespace ziggymart -q 'ExecutionStatus="Running"' 2>/dev/null | grep -oE '[0-9]+' | head -1 || echo '?')"
    fi

    if [ "$DRAIN" = true ] && { [ "$count" = "?" ] || [ "$count" -gt 0 ] 2>/dev/null; }; then
      echo "Draining: waiting for in-flight workflows on '$cur' to complete..."
      # Bounded: if the count never becomes a number (probe unreachable — missing
      # cloud.env, admintools down), abort instead of sleeping forever.
      drained=false
      for _ in $(seq 1 60); do
        [ "$count" != "?" ] && [ "$count" -le 0 ] 2>/dev/null && { drained=true; break; }
        sleep 10
        if [ "$cur" = "cloud" ]; then count="$(temporal workflow count --address "$TEMPORAL_ADDRESS" --namespace "$TEMPORAL_NAMESPACE" --api-key "$TEMPORAL_API_KEY" --tls -q 'ExecutionStatus="Running"' 2>/dev/null | grep -oE '[0-9]+' | head -1 || echo '?')"
        else count="$(KUBECONFIG={{kubeconfig}} kubectl -n temporal exec deploy/temporal-admintools -- temporal workflow count --address temporal-internal-frontend:7236 --namespace ziggymart -q 'ExecutionStatus="Running"' 2>/dev/null | grep -oE '[0-9]+' | head -1 || echo '?')"; fi
        echo "  still running: $count"
      done
      [ "$drained" = true ] || { echo "Drain timed out (10m) with count='$count' — aborting the switch. Re-run without --drain to force."; exit 1; }
      echo "Drained."
    elif [ "$YES" != true ] && { [ "$count" = "?" ] || [ "$count" -gt 0 ] 2>/dev/null; }; then
      echo
      echo "  In-flight workflows on the CURRENT ($cur) backend: $count"
      if [ "{{target}}" = "cloud" ]; then
        echo "  Switching to Cloud will ORPHAN them (Cloud keeps them; they resume when you switch back to $cur)."
      else
        echo "  Switching away from Cloud stops workers polling Cloud; those Cloud workflows are preserved but idle until you switch back."
      fi
      read -r -p "  Proceed with the switch? [y/N] " ans
      case "$ans" in y|Y|yes) ;; *) echo "aborted."; exit 1;; esac
    fi

    # Preserve the OSS server across the switch (decoupled). Ensure it's ON when
    # switching TO oss; keep whatever it was when switching to cloud.
    new_srv="$srv"; [ "{{target}}" = "oss" ] && new_srv=true
    tag="$(git describe --tags --always --dirty --abbrev=12)"
    export TF_VAR_worker_image_digests="$(just worker-digests-json)"
    export TF_VAR_orders_api_image_digest="$(crane digest localhost:{{registry_port}}/orders-api:$tag --insecure)"
    export TF_VAR_autoscaler_image_digest="$(crane digest localhost:{{registry_port}}/temporal-worker-autoscaler:$tag --insecure)"
    export TF_VAR_temporal_backend="{{target}}"
    export TF_VAR_oss_server_enabled="$new_srv"
    terraform -chdir=$L apply -auto-approve
    echo "Cluster repointed to {{target}}. Waiting for ArgoCD to reconcile the workers..."

    # Recreate the host console with the target profile (flips CONSOLE_BACKEND etc).
    reconcile_console "{{target}}"
    echo "Switched to backend '{{target}}'."

# Remove the in-cluster OSS temporal-server (Application + CNPG Postgres + certs).
# DESTRUCTIVE: drops all OSS workflow state. Refuses while the backend is still
# 'oss' (workers would break) — switch to cloud first. Reclaims host resources.
temporal-server-down:
    #!/usr/bin/env bash
    set -euo pipefail
    L="deploy/terraform/layers/cluster"
    cur="$(terraform -chdir=$L output -raw temporal_backend 2>/dev/null || echo cloud)"
    if [ "$cur" = "oss" ]; then
      echo "Backend is still 'oss' — workers point at this server. Run 'just switch-backend cloud' first."; exit 1
    fi
    read -r -p "Remove the OSS temporal-server + its Postgres (ALL OSS workflow state lost)? Type 'yes': " ans
    [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
    tag="$(git describe --tags --always --dirty --abbrev=12)"
    export TF_VAR_worker_image_digests="$(just worker-digests-json)"
    export TF_VAR_orders_api_image_digest="$(crane digest localhost:{{registry_port}}/orders-api:$tag --insecure)"
    export TF_VAR_autoscaler_image_digest="$(crane digest localhost:{{registry_port}}/temporal-worker-autoscaler:$tag --insecure)"
    export TF_VAR_temporal_backend=cloud
    export TF_VAR_oss_server_enabled=false
    terraform -chdir=$L apply -auto-approve
    echo "temporal-server removed. (PVCs pruned by ArgoCD; run 'just k get pvc -n temporal' to confirm.)"

# PHYSICALLY reset the OSS Temporal DB: drop the temporal-postgresql CNPG Cluster
# + PVCs; ArgoCD re-syncs temporal-server and the schema jobs re-run — the local
# "nuclear option" to re-pick numHistoryShards (immutable in-place). DESTRUCTIVE:
# drops all OSS workflow history. Parallels orders-db-reset.
temporal-db-reset:
    #!/usr/bin/env bash
    set -euo pipefail
    read -r -p "Delete temporal-postgresql Cluster + PVCs in namespace 'temporal'? ALL OSS workflow history lost. Type 'yes': " ans
    [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
    KUBECONFIG={{kubeconfig}} kubectl -n temporal delete cluster.postgresql.cnpg.io temporal-postgresql --ignore-not-found
    KUBECONFIG={{kubeconfig}} kubectl -n temporal delete pvc -l cnpg.io/cluster=temporal-postgresql --ignore-not-found
    echo "Deleted. ArgoCD re-syncs temporal-server; CNPG bootstraps a fresh DB + the schema jobs re-run."

# Fetch the pinned, sha256-verified Headlamp UI plugins (config/dependencies.yaml
# `headlamp.plugins`) into the bind-mounted compose/deployment/headlamp/plugins/.
# Idempotent + offline once fetched. Currently no plugins are pinned (the KEDA
# explorer was removed with KEDA; ADR-0023). Add a version/sha to re-enable fetch.
headlamp-plugins:
    @uv run python compose/scripts/fetch-headlamp-plugins.py

# Force Headlamp to re-read the kubeconfig now. Headlamp already WATCHES it and
# auto-loads the cluster within ~10s, so this is only an immediate-refresh shortcut.
# (Also reloads UI plugins — run after `just headlamp-plugins` pulls a new one.)
headlamp-reload:
    @docker restart headlamp >/dev/null && echo "headlamp restarted — kubeconfig + plugins reloaded"

# Fetch the pinned, sha256-verified Grafana plugins (config/dependencies.yaml
# `grafana.plugins`) into the bind-mounted compose/deployment/grafana/plugins/.
# GF_INSTALL_PLUGINS is a no-op on the otel-lgtm image and GF_PLUGINS_PREINSTALL
# hangs on boot (air-gap, ADR-0013) — this is the offline substitute. Idempotent
# once fetched — `legacy-up` and `host-up` run it first so the ClickHouse
# datasource plugin is present on boot. Bump a version/sha in the manifest to
# re-fetch, then `docker restart lgtm` to load it.
grafana-plugins:
    @uv run python compose/scripts/fetch-grafana-plugins.py

# Live-test gate: fails if the console is down OR its backend drifts from the target
# (expected arg, else live cluster state). Wraps poe preflight-console for liveness.
preflight expected="":
    #!/usr/bin/env bash
    set -euo pipefail
    uv run poe preflight-console
    console="$(curl -sf --max-time 3 http://localhost:8086/healthz | uv run python -c 'import sys,json; print(json.load(sys.stdin).get("backend",""))' 2>/dev/null || echo "")"
    target="{{expected}}"
    if [ -z "$target" ]; then
      target="$(terraform -chdir=deploy/terraform/layers/cluster output -raw temporal_backend 2>/dev/null || echo "")"
    fi
    if [ -n "$console" ] && [ -n "$target" ] && [ "$console" != "$target" ]; then
      echo "backend drift: platform-console is '$console' but the target backend is '$target'." >&2
      echo "Resync the console:  just host-refresh $target   (or run: just host-up $target)" >&2
      exit 1
    fi

# --- workloads ---------------------------------------------------------------

# Build, publish, and apply the GitOps stack onto an existing kind substrate.
# Steps: mirror-deps → ci (build/push) → chart-publish → terraform apply (ArgoCD apps).
# Gated on host plane (preflight). Does NOT run kind-up — substrate must exist.
# backend: `cloud` (default) or `oss` (also enables the in-cluster temporal-server).
# To switch a live backend use `just switch-backend`, not a re-run of this recipe.
# Deploy GitOps workloads onto an existing kind substrate (preflight-gated).
workloads-up backend="cloud":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{backend}}" in cloud|oss) ;; *) echo "backend must be 'cloud' or 'oss'"; exit 1;; esac
    just preflight {{backend}}
    just mirror-deps
    just ci
    just chart-publish
    tag="$(git describe --tags --always --dirty --abbrev=12)"
    export TF_VAR_worker_image_digests="$(just worker-digests-json)"
    api="$(crane digest localhost:{{registry_port}}/orders-api:$tag --insecure)"
    aut="$(crane digest localhost:{{registry_port}}/temporal-worker-autoscaler:$tag --insecure)"
    export TF_VAR_orders_api_image_digest="$api"
    export TF_VAR_autoscaler_image_digest="$aut"
    export TF_VAR_temporal_backend="{{backend}}"
    [ "{{backend}}" = "oss" ] && export TF_VAR_oss_server_enabled=true || export TF_VAR_oss_server_enabled=false
    terraform -chdir=deploy/terraform/layers/cluster init -input=false
    terraform -chdir=deploy/terraform/layers/cluster apply -auto-approve
    just headlamp-reload 2>/dev/null || true
    echo "workloads up (backend={{backend}})."
    echo "  Console (all UIs): http://localhost:8086   ArgoCD: http://localhost:8088   Headlamp: http://localhost:8087"

# Tear down ArgoCD apps / TF cluster layer; leaves kind + registry + ArgoCD running.
workloads-down:
    #!/usr/bin/env bash
    set -euo pipefail
    L="deploy/terraform/layers/cluster"
    backend="$(terraform -chdir=$L output -raw temporal_backend 2>/dev/null || echo cloud)"
    oss_enabled="$(terraform -chdir=$L output -raw oss_server_enabled 2>/dev/null || echo false)"
    export TF_VAR_temporal_backend="$backend"
    export TF_VAR_oss_server_enabled="$oss_enabled"
    terraform -chdir=$L init -input=false
    terraform -chdir=$L destroy -auto-approve

# --- cluster (kind + workloads) ----------------------------------------------

# Bring up kind substrate then deploy workloads. Preflight-gated (console must be up).
cluster-up backend="cloud":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{backend}}" in cloud|oss) ;; *) echo "backend must be 'cloud' or 'oss'"; exit 1;; esac
    just preflight {{backend}}
    just kind-up
    just workloads-up {{backend}}

# Teardown matches kind-down: deleting kind removes all workloads with it.
cluster-down:
    just kind-down

# Tear down and recreate the cluster (kind + workloads).
cluster-refresh backend="cloud":
    just cluster-down
    just cluster-up {{backend}}

# --- platform (host + cluster) -----------------------------------------------

# Cold-start one-shot: host (detached) + cluster. Polls console health before kind side.
platform-up backend="cloud":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{backend}}" in cloud|oss) ;; *) echo "backend must be 'cloud' or 'oss'"; exit 1;; esac
    just host-up {{backend}}
    echo "==> Waiting for platform-console (:8086/healthz)..."
    for i in $(seq 1 45); do
      if curl -sf -o /dev/null --max-time 2 http://localhost:8086/healthz; then
        echo "platform-console is up."
        break
      fi
      if [ "$i" -eq 45 ]; then
        echo "platform-console did not become healthy — check: docker ps && docker logs platform-console --tail 50"
        exit 1
      fi
      sleep 2
    done
    just cluster-up {{backend}}

# DESTRUCTIVE: host-down + cluster-down. Drops compose volumes and ALL in-cluster state.
# See RUNMODES.md "Full reset from scratch".
platform-down *FLAGS:
    #!/usr/bin/env bash
    set -euo pipefail
    KEEP=true
    case "{{FLAGS}}" in *--no-keep-registry*) KEEP=false;; esac
    read -r -p "Reset host plane + cluster? Drops compose volumes and ALL in-cluster state. Type 'yes': " ans
    [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
    echo "==> Host plane down (compose down -v)..."
    just host-down
    echo "==> Cluster down (releases Cloud Worker Deployments first)..."
    if [ "$KEEP" = true ]; then
      just cluster-down
    else
      KEEP_REGISTRY=false just cluster-down
    fi
    echo ""
    echo "Reset complete. Bring everything back up:"
    echo "  just platform-up              # Cloud (host + cluster)"
    echo "  just platform-up oss          # OSS backend variant"
    echo ""
    echo "Or host then cluster separately:"
    echo "  just host-up && just cluster-up"
    echo "  just host-up oss && just cluster-up oss"
    echo ""
    echo "Cloud workflow history in your namespace is unchanged. Logical demo reset only:"
    echo "  console → Reset demo, or POST /admin/reset on orders-api (Cloud: local tables only)."

# Pause host + cluster, keep all state.
platform-stop:
    just host-stop
    just cluster-stop

# Resume a previously-stopped host + cluster.
platform-start:
    just host-start
    just cluster-start

# DESTRUCTIVE: tear down everything then cold-start. Single confirmation prompt.
platform-refresh backend="cloud":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{backend}}" in cloud|oss) ;; *) echo "backend must be 'cloud' or 'oss'"; exit 1;; esac
    read -r -p "Refresh platform (host + cluster)? Drops compose volumes and ALL in-cluster state. Type 'yes': " ans
    [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
    echo "==> Host plane down (compose down -v)..."
    just host-down
    echo "==> Cluster down (releases Cloud Worker Deployments first)..."
    just cluster-down
    just platform-up {{backend}}
