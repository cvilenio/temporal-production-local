# Runbook: ArgoCD Application stuck (deadlocked sync)

**Symptom:** an Application sits `Progressing` / `OutOfSync` indefinitely; a corrected
chart/config doesn't take effect; `selfHeal` and refresh do nothing. This is the sync-deadlock
class — a sync **operation** is waiting on a resource's health that will never come, and a hung
op is not a failed op, so it never restarts on its own. (Incident of record:
`ai_checkpoints/0011` — a CNPG `Cluster` waiting on a Secret in a later wave.)

See ADR-0016 for why this happens and how the repo is designed to prevent it. This runbook is for
when one slips through anyway.

## Detect

```sh
just k get applications -n argocd        # look for Progressing/OutOfSync that won't settle
# or with the kubeconfig directly:
KUBECONFIG=.secrets/kube/kind.kubeconfig kubectl get applications -n argocd
```

**Alert (when observability is wired onto kind — currently an open item):** page on any
Application `Progressing` beyond a threshold. Sketch (Prometheus / argocd-metrics):

```promql
# Apps reporting non-Healthy for too long → likely a stuck sync.
max_over_time(argocd_app_info{health_status!="Healthy"}[15m]) > 0
```

Tune the window to your slowest legitimate rollout. The point is MTTD: a silent deadlock looks
identical to "still deploying."

## Diagnose

```sh
K="KUBECONFIG=.secrets/kube/kind.kubeconfig kubectl"
app=orders-data   # the stuck one
# What is the sync operation waiting on?
$K get application -n argocd "$app" -o jsonpath='{.status.operationState.phase}{"  "}{.status.operationState.message}{"\n"}'
# e.g. "Running   waiting for healthy state of postgresql.cnpg.io/Cluster/orders-db"
```

The message names the resource whose health is blocking the wave. Inspect *why* it's unhealthy
(the usual root cause is an existence dependency missing — a Secret/ConfigMap/CRD it needs that
landed in an equal-or-later wave; that's exactly what the CI gate `deploy/check-sync-waves.py`
prevents).

## Recover

1. **Terminate the hung operation** — the canonical unstick, and the thing a hard-refresh does
   NOT do:

   ```sh
   argocd app terminate-op "$app"        # if the argocd CLI is available
   ```

   No CLI? Clear the missing existence-dependency by hand so the wait resolves, then let the next
   sync run cleanly — e.g. apply the chart's desired Secret directly:

   ```sh
   helm template deploy/charts/orders-data --show-only templates/db-secret.yaml \
     | KUBECONFIG=.secrets/kube/kind.kubeconfig kubectl apply -n orders -f -
   ```

2. **If a CR bootstrapped into a bad state** (e.g. CNPG initdb failed), delete it so the operator
   re-bootstraps once its dependency exists:

   ```sh
   just orders-db-reset      # deletes the CNPG Cluster + PVCs; ArgoCD re-syncs a fresh DB
   ```

3. **Re-sync / refresh:**

   ```sh
   $K patch application -n argocd "$app" --type merge \
     -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
   ```

## Related: Worker Deployment stuck (promotion / ManagerIdentity)

**Symptom:** `orders-workers` is `Degraded`, or orders submit but sit `pending` forever even
though the worker pods are Running/Ready. The WorkerDeployment condition says *"unable to set
current deployment version: ManagerIdentity '…' is set and does not match user identity '…'"*,
and `status.currentVersion.deployment` is `None` (Current is pinned to a dead version with no
pods).

**Cause:** the controller's routing-ownership identity is suffixed with the `temporal-system`
namespace UID, which is regenerated on every fresh kind cluster. The Temporal Cloud namespace +
its Worker Deployments persist across `cluster-down`, so a rebuilt controller (new identity)
can't reclaim a deployment still owned by a prior cluster's controller. (A manual
`set-current-version` causes the same standoff.) See ADR-0004.

**Fix — hand ownership back so the controller reclaims:**

```sh
set -a; . .secrets/keys/cloud.env; set +a
for wd in orders/orders-workflow-python orders/orders-activity-python orders/orders-finalization-java; do
  temporal worker deployment manager-identity unset --yes \
    --address "$TEMPORAL_ADDRESS" --namespace "$TEMPORAL_NAMESPACE" --api-key "$TEMPORAL_API_KEY" --tls \
    --deployment-name "$wd"
done
# On the next reconcile the controller claims the now-empty identity and promotes the live version.
```

**Prevent:** `just cluster-down` runs `just release-worker-deployments` first (graceful
decommission — unset ownership before deleting the cluster), so the next cluster claims cleanly.
There is **no** `temporal.io/ignore-last-modifier` annotation (a myth from an old ADR draft);
`manager-identity unset` is the supported mechanism.

## Prevent (so it doesn't recur)

- The CI gate (`poe lint` → `deploy/check-sync-waves.py`) fails a chart that orders a
  Secret/ConfigMap at an equal-or-later wave than its consumer. Keep it green.
- Follow ADR-0016: order **existence** deps with waves; handle **readiness** deps with k8s
  readiness (not waves); split Applications by failure domain.
