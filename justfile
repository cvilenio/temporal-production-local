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
#   just up         local OSS app stack
#   just ci         python gate + image build/push
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

# Verify config/domains/*.yaml against namespaces.yaml and kernel task-queue constants.
verify-domains:
    uv run python compose/scripts/verify-domains.py

# Scaffold a new domain from templates/domain/<lang>/ (Python today; Java in M6).
scaffold-domain NAME LANG="python":
    uv run python compose/scripts/scaffold_domain.py --name {{NAME}} --lang {{LANG}}

# All static checks: python (poe) + k8s manifests (helm/kubeconform) + proto lint
# + dependency-version drift (versions-audit vs config/dependencies.yaml)
# + domain descriptor consistency (verify-domains).
lint:
    uv run poe lint
    just lint-manifests
    just proto-lint
    just versions-audit
    just verify-domains

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

# Local OSS server + app tier (no workers — those run on kind).
up: render-oss-bootstrap grafana-plugins
    set -a; . config/local-oss.env; set +a; docker compose -f docker-compose.yml -f compose/host-apptier.yml -f compose/oss-server.yml up --build

# Stop the local-OSS stack and drop volumes (also sweeps a stray default-project + the shared net).
down:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml -f compose/oss-server.yml down -v --remove-orphans; docker compose -p "${PWD##*/}" -f docker-compose.yml -f compose/host-apptier.yml -f compose/oss-server.yml down -v --remove-orphans || true; docker network rm temporal-network 2>/dev/null || true

# Recreate the local-OSS stack.
fresh: down up

# Host visibility + console + mock-api for the kind+Cloud path (kind owns the
# workers AND the app tier). Bring this up FIRST before any live kind testing.
up-cloud-kind: headlamp-plugins grafana-plugins
    set -a; . .secrets/keys/cloud.env; set +a; docker compose -f docker-compose.yml up --build

# Host visibility + console + mock-api for the kind + self-hosted OSS path
# (ADR-0003). Same host stack as up-cloud-kind, but sourcing the OSS connection
# profile (CONSOLE_BACKEND=oss, empty Cloud vars). Bring this up FIRST before any
# live kind testing, then `just platform-up oss`.
up-oss-kind: headlamp-plugins grafana-plugins
    set -a; . config/local-oss-kind.env; set +a; docker compose -f docker-compose.yml up --build

# Stop the Cloud-backed host stack and drop volumes.
down-cloud:
    docker compose -f docker-compose.yml -f compose/host-apptier.yml down -v --remove-orphans; docker compose -p "${PWD##*/}" -f docker-compose.yml -f compose/host-apptier.yml down -v --remove-orphans || true; docker network rm temporal-network 2>/dev/null || true

# --- Worker/API images (docker — cross-language artifact build) ---------------
# Tagged with git-describe so a build is immutable + uniquely addressable; a
# dirty tree carries a `-dirty` suffix. Deploys pin by DIGEST (image-digests);
# the tag is for humans. REGISTRY defaults to the local registry from cluster-up.

# Build the worker images + orders-api, tagged <registry>/<name>:<git-describe>.
build-images:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    for profile in workflow activity; do
      docker build -f images/python.Dockerfile \
        --build-arg APP_GROUP=workers \
        --build-arg APP_PATH=apps/temporal/workers/python/$profile \
        --build-arg APP_MODULE=main \
        --build-arg APP_CMD=python \
        -t "$REGISTRY/orders-worker-$profile:$TAG" .
    done
    docker build -f images/python.Dockerfile \
      --build-arg APP_GROUP=orders-api \
      --build-arg APP_PATH=apps/business/orders-api/python \
      --build-arg APP_MODULE=main:app \
      --build-arg APP_CMD=uvicorn \
      -t "$REGISTRY/orders-api:$TAG" .
    docker build -f images/go.Dockerfile \
      --build-arg APP_PATH=apps/platform/temporal-worker-autoscaler/go \
      -t "$REGISTRY/temporal-worker-autoscaler:$TAG" .
    docker build -f images/java.Dockerfile \
      --build-arg DOMAIN=orders \
      --build-arg APP_MODULE=:orders-activity-java-worker \
      --build-arg WORKER_REL_PATH=apps/temporal/workers/java/orders/activity \
      --build-arg APP_JAR=orders-activity-java-worker \
      -t "$REGISTRY/orders-worker-activity-java:$TAG" .
    echo "Built $REGISTRY/orders-worker-{workflow,activity,activity-java}:$TAG, orders-api:$TAG, temporal-worker-autoscaler:$TAG"

# Push the worker images + orders-api to the local registry.
push-images:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    for profile in workflow activity; do
      docker push "$REGISTRY/orders-worker-$profile:$TAG"
    done
    docker push "$REGISTRY/orders-api:$TAG"
    docker push "$REGISTRY/temporal-worker-autoscaler:$TAG"
    docker push "$REGISTRY/orders-worker-activity-java:$TAG"
    echo "Pushed $REGISTRY/orders-worker-{workflow,activity,activity-java}:$TAG, orders-api:$TAG, temporal-worker-autoscaler:$TAG"

# Print the image tag (git-describe) for the current tree.
image-tag:
    @git describe --tags --always --dirty --abbrev=12

# Print pushed image digests (name=sha256:...) for deploy-by-digest.
image-digests:
    #!/usr/bin/env bash
    set -euo pipefail
    REGISTRY="${REGISTRY:-localhost:5001}"
    TAG="$(git describe --tags --always --dirty --abbrev=12)"
    for profile in workflow activity; do
      echo "$profile=$(crane digest "$REGISTRY/orders-worker-$profile:$TAG" --insecure)"
    done
    echo "orders-api=$(crane digest "$REGISTRY/orders-api:$TAG" --insecure)"
    echo "temporal-worker-autoscaler=$(crane digest "$REGISTRY/temporal-worker-autoscaler:$TAG" --insecure)"
    echo "activity-java=$(crane digest "$REGISTRY/orders-worker-activity-java:$TAG" --insecure)"

# --- Local cluster (kind + local registry) -----------------------------------

# Bring up the kind cluster + local registry (kubeconfig under .secrets/).
cluster-up: render-deps
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
    for wd in orders/orders-workflow orders/orders-activity; do
      echo "releasing ManagerIdentity: $wd"
      temporal worker deployment manager-identity unset "${A[@]}" --deployment-name "$wd" 2>/dev/null \
        || echo "  (skip: $wd not found or already released)"
    done

# Tear down the kind cluster (keeps the registry; KEEP_REGISTRY=false to remove).
# Releases Cloud Worker Deployment ownership first (graceful decommission) so the
# next cluster's controller can reclaim routing — see release-worker-deployments.
cluster-down: release-worker-deployments
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
    for chart in orders-workers orders-data orders-api alloy temporal-worker-autoscaler temporal-server; do
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
    just preflight
    L="deploy/terraform/layers/cluster"
    cur="$(terraform -chdir=$L output -raw temporal_backend 2>/dev/null || echo cloud)"
    # Default to the NON-destructive value on a failed read: a switch must never
    # prune the OSS server (only `temporal-server-down` does). Guessing false here
    # would set oss_server_enabled=false → Terraform destroys the server + CNPG.
    srv="$(terraform -chdir=$L output -raw oss_server_enabled 2>/dev/null || echo true)"
    if [ "$cur" = "{{target}}" ]; then echo "Already on backend '{{target}}' — nothing to do."; exit 0; fi
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
    export TF_VAR_worker_image_digests="{\"workflow\":\"$(crane digest localhost:{{registry_port}}/orders-worker-workflow:$tag --insecure)\",\"activity\":\"$(crane digest localhost:{{registry_port}}/orders-worker-activity:$tag --insecure)\",\"activity-java\":\"$(crane digest localhost:{{registry_port}}/orders-worker-activity-java:$tag --insecure)\"}"
    export TF_VAR_orders_api_image_digest="$(crane digest localhost:{{registry_port}}/orders-api:$tag --insecure)"
    export TF_VAR_autoscaler_image_digest="$(crane digest localhost:{{registry_port}}/temporal-worker-autoscaler:$tag --insecure)"
    export TF_VAR_temporal_backend="{{target}}"
    export TF_VAR_oss_server_enabled="$new_srv"
    terraform -chdir=$L apply -auto-approve
    echo "Cluster repointed to {{target}}. Waiting for ArgoCD to reconcile the workers..."

    # Recreate the host console with the target profile (flips CONSOLE_BACKEND etc).
    profile=".secrets/keys/cloud.env"; [ "{{target}}" = "oss" ] && profile="config/local-oss-kind.env"
    set -a; . "$profile"; set +a
    docker compose -f docker-compose.yml up -d --no-deps --force-recreate console
    just headlamp-reload 2>/dev/null || true
    echo "Switched to backend '{{target}}'. Console recreated with the {{target}} profile."

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
    export TF_VAR_worker_image_digests="{\"workflow\":\"$(crane digest localhost:{{registry_port}}/orders-worker-workflow:$tag --insecure)\",\"activity\":\"$(crane digest localhost:{{registry_port}}/orders-worker-activity:$tag --insecure)\",\"activity-java\":\"$(crane digest localhost:{{registry_port}}/orders-worker-activity-java:$tag --insecure)\"}"
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
# once fetched — `up` and `up-cloud-kind` run it first so the ClickHouse
# datasource plugin is present on boot. Bump a version/sha in the manifest to
# re-fetch, then `docker restart lgtm` to load it.
grafana-plugins:
    @uv run python compose/scripts/fetch-grafana-plugins.py

# Probe that the platform-console is up. Required before ANY live kind testing so
# the operator can follow along in real time — see AGENTS.md / docs/RUNMODES.md.
# Wraps `poe preflight-console`; exits non-zero with how-to-fix if the console is down.
preflight:
    uv run poe preflight-console

# Full local bring-up: cluster + registry, mirror deps, CI (build/push), publish chart,
# pin workers by digest, apply the cluster layer. One command, each step idempotent.
# Gated on the console being up first (preflight) so the bring-up is never blind.
#
# The positional `backend` selects the Temporal backend for this FRESH bring-up:
# `cloud` (default, the supported path) or `oss` (the in-cluster self-hosted server) —
# `just platform-up` vs `just platform-up oss`. On oss it also creates the
# temporal-server Application. To SWITCH an already-running stack use the guarded
# `just switch-backend` — do NOT re-run platform-up to flip a live backend.
platform-up backend="cloud":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{backend}}" in cloud|oss) ;; *) echo "backend must be 'cloud' or 'oss'"; exit 1;; esac
    just preflight
    just cluster-up
    just mirror-deps
    just ci
    just chart-publish
    tag="$(git describe --tags --always --dirty --abbrev=12)"
    wf="$(crane digest localhost:{{registry_port}}/orders-worker-workflow:$tag --insecure)"
    ac="$(crane digest localhost:{{registry_port}}/orders-worker-activity:$tag --insecure)"
    ja="$(crane digest localhost:{{registry_port}}/orders-worker-activity-java:$tag --insecure)"
    api="$(crane digest localhost:{{registry_port}}/orders-api:$tag --insecure)"
    aut="$(crane digest localhost:{{registry_port}}/temporal-worker-autoscaler:$tag --insecure)"
    export TF_VAR_worker_image_digests="{\"workflow\":\"$wf\",\"activity\":\"$ac\",\"activity-java\":\"$ja\"}"
    export TF_VAR_orders_api_image_digest="$api"
    export TF_VAR_autoscaler_image_digest="$aut"
    export TF_VAR_temporal_backend="{{backend}}"
    # Fresh bring-up: the OSS server exists iff this is an OSS bring-up. (Switching a
    # live stack is switch-backend's job, which preserves the server independently.)
    [ "{{backend}}" = "oss" ] && export TF_VAR_oss_server_enabled=true || export TF_VAR_oss_server_enabled=false
    terraform -chdir=deploy/terraform/layers/cluster init -input=false
    terraform -chdir=deploy/terraform/layers/cluster apply -auto-approve
    just headlamp-reload 2>/dev/null || true
    echo "platform up (backend={{backend}})."
    echo "  Console (all UIs): http://localhost:8086   ArgoCD: http://localhost:8088   Headlamp: http://localhost:8087"
    echo "  (If the host stack wasn't running, start it with 'just up-cloud-kind' then 'just headlamp-reload'."
    echo "   up-cloud-kind runs the app tier + visibility WITHOUT workers — the cluster runs those.)"
