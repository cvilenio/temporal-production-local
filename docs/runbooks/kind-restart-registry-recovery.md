# Runbook: kind cluster restart - stale registry EndpointSlice (ArgoCD 502)

**Symptom:** after the host machine (or Docker) is restarted, the kind cluster comes back but ArgoCD Applications sit `OutOfSync` / `Progressing` and cannot pull charts.
Chart syncs fail with a registry error (commonly HTTP 502) even though the local OCI registry container is running and `just chart-publish` succeeds from the host.

This is the restart-recovery class, distinct from the sync-deadlock class in [`argocd-stuck-sync.md`](argocd-stuck-sync.md).
There the sync operation is wedged; here the operation is fine but the in-cluster path to the registry is broken.

## Why this happens

The local OCI registry is a Docker container named `artifact-registry` on the `kind` Docker network (ADR-0011).
In-cluster clients (ArgoCD) reach it through a proxy `Service` in `kube-public`, whose `EndpointSlice` is **manually managed** and points at the container's IP on the kind network.

When Docker restarts, the container can come back with a **different** IP on the kind network, but the `EndpointSlice` still holds the old address.
The Service then routes to a dead IP, so every in-cluster pull returns 502 while host-side pushes (which hit the container directly) keep working.
This is the confusing part: `chart-publish` succeeds, the registry looks healthy, yet ArgoCD cannot fetch.

## The fast path

`just cluster-start` is the supported restart entry point and self-heals this (re-derives the registry IP, repairs the `EndpointSlice`, waits for ArgoCD Healthy).
Prefer it over any manual step below.
It is a sandbox with no production load, so reconciling to a known-good state is cheap and always safe - do not hand-patch around a stale cluster when the recipe will reset it deterministically.

```sh
just cluster-start        # start stopped registry + nodes, repair registry endpoint, wait for ArgoCD
```

## Manual recovery (only if the recipe is unavailable or still 502)

Confirm the mismatch, then patch the EndpointSlice to the container's current kind-network IP.

```sh
K="KUBECONFIG=.secrets/kube/kind.kubeconfig kubectl"

# 1. What IP does the in-cluster EndpointSlice think the registry has?
$K get endpointslice artifact-registry -n kube-public \
  -o jsonpath='{.endpoints[*].addresses}{"\n"}'

# 2. What IP does the container actually have on the kind network?
docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' artifact-registry

# 3. If they differ, patch the EndpointSlice to the real IP (replace <IP>):
$K patch endpointslice artifact-registry -n kube-public --type merge \
  -p '{"endpoints":[{"addresses":["<IP>"]}]}'
```

Then let ArgoCD retry (hard-refresh or `argocd app get <app>`); pulls should resolve within a sync cycle.

## Verify

```sh
just k get applications -n argocd            # all Synced/Healthy
just k get pods -A                           # zero non-Running/Completed
```

Cross-check the registry path end to end by confirming a workload image/chart the cluster had been failing to pull now reconciles.

## Prevent

- Always resume a stopped cluster with `just cluster-start`, never a bare `docker start` of the nodes - the recipe owns the EndpointSlice repair.
- Pair with `cluster-stop` for planned shutdowns so state is preserved for an offline restart.
