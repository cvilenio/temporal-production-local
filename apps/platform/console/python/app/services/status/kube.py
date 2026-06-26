"""KubeProvider — the kind-substrate status source (ADR-0015 phase-2).

Reads pod state from the cluster via a read-only ServiceAccount kubeconfig (the
same `.secrets/kube` dir Headlamp mounts) and maps it to the substrate-neutral
status vocabulary. Only owns the services that carry a `kube` locator in the
registry; host-plane tooling stays Docker-sourced (see the composite).

The console is expected to boot BEFORE the cluster exists, so the client is
connected lazily and re-attempted every poll until the kubeconfig appears; a
down/absent cluster degrades to "down" rather than raising.
"""

import asyncio
import os
from typing import Any

import httpx
from app.services.status.core import (
    KUBE_OWNED_KEYS,
    SERVICE_REGISTRY,
    service_entry,
)

# Per-call API timeout — kept under the poll interval so an unreachable cluster
# never stalls the loop.
_API_TIMEOUT_S = 3


class KubeProvider:
    def __init__(self) -> None:
        # CoreV1Api — typed Any; the kubernetes client is imported lazily so the
        # console boots even when the package's heavy import tree isn't needed.
        self._v1: Any = None
        # mtime of the kubeconfig the cached client was built from. A cluster
        # recreate (`down` + `up`) assigns a NEW API-server port and rewrites this
        # file (cluster-up / headlamp-reload); the cached client still pins the old
        # endpoint and would poll a dead port forever. Rebuild when the file changes.
        self._loaded_mtime: float | None = None

    def _ensure_client(self) -> bool:
        """Load the kubeconfig and build a CoreV1Api (sync). Returns False if the
        kubeconfig isn't present/loadable yet (cluster not up) so poll can degrade.
        Rebuilds when the kubeconfig changes on disk so a cluster recreate self-heals
        without a console restart (the API-server endpoint moves on every recreate)."""
        kubeconfig = os.environ.get("KUBECONFIG")
        if not kubeconfig or not os.path.exists(kubeconfig):
            return False
        try:
            mtime = os.path.getmtime(kubeconfig)
        except OSError:
            return False
        if self._v1 is not None and mtime == self._loaded_mtime:
            return True
        try:
            from kubernetes import client, config

            config.load_kube_config(config_file=kubeconfig)
            self._v1 = client.CoreV1Api()
            self._loaded_mtime = mtime
            return True
        except Exception as e:
            print(f"KubeProvider: kubeconfig load failed ({kubeconfig}): {e}")
            self._v1 = None
            self._loaded_mtime = None
            return False

    async def poll(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str] = frozenset()
    ) -> dict[str, dict]:
        keys = [k for k in KUBE_OWNED_KEYS if k not in exclude]
        if not keys:
            return {}

        if not await asyncio.to_thread(self._ensure_client):
            # Cluster not reachable yet — report the cluster-owned services as down
            # (the page shows them red until the cluster comes up).
            return {k: self._down_entry(k) for k in keys}

        snapshots = await asyncio.gather(
            *[self._poll_one(k) for k in keys], return_exceptions=True
        )
        out: dict[str, dict] = {}
        for k, res in zip(keys, snapshots, strict=True):
            if isinstance(res, BaseException):
                print(f"KubeProvider: poll {k} failed: {res}")
                out[k] = self._down_entry(k)
            else:
                out[k] = res
        return out

    async def _poll_one(self, key: str) -> dict:
        config = SERVICE_REGISTRY[key]
        locator = config["kube"]
        pods = await asyncio.to_thread(self._list_pods, locator)
        return self._entry_from_pods(key, config, pods)

    def _list_pods(self, locator: dict) -> list:
        resp = self._v1.list_namespaced_pod(
            namespace=locator["namespace"],
            label_selector=locator["selector"],
            _request_timeout=_API_TIMEOUT_S,
        )
        return resp.items

    def _entry_from_pods(self, key: str, config: dict, pods: list) -> dict:
        if not pods:
            return self._down_entry(key)

        total = len(pods)
        ready = 0
        phases: list[str] = []
        rep = pods[0]  # representative pod for display
        for p in pods:
            phase = p.status.phase or "Unknown"
            phases.append(phase)
            statuses = p.status.container_statuses or []
            if phase == "Running" and statuses and all(cs.ready for cs in statuses):
                ready += 1
                rep = p  # prefer a ready pod for the display sample

        derived = self._derive(ready, total, phases)

        container_name = rep.metadata.name
        image = "unknown"
        spec_containers = rep.spec.containers or []
        if spec_containers:
            image = spec_containers[0].image or "unknown"

        return service_entry(
            key,
            config,
            docker_state=rep.status.phase or "Unknown",
            docker_health=f"{ready}/{total} ready",
            http_res=None,
            container_name=container_name,
            image=image,
            ports=[],
            status_source="kube",
            derived_status=derived,
        )

    @staticmethod
    def _derive(ready: int, total: int, phases: list[str]) -> str:
        if ready > 0:
            return "healthy"
        if any(ph in ("Pending",) for ph in phases):
            return "starting"
        if any(ph == "Running" for ph in phases):
            return "degraded"  # running but no pod is ready
        if phases and all(ph in ("Failed", "Succeeded") for ph in phases):
            return "down"
        return "unknown"

    def _down_entry(self, key: str) -> dict:
        return service_entry(
            key,
            SERVICE_REGISTRY[key],
            docker_state="not-found",
            docker_health=None,
            http_res=None,
            container_name="not-found",
            image="unknown",
            ports=[],
            status_source="kube",
        )
