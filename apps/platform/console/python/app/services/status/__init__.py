"""Substrate-aware live-status service.

Public surface (imported by main.py / routes):
  - `broker`, `get_snapshot` — the SSE broker and last snapshot
  - `poll_status_loop` — the background poller (selects the provider by substrate)

Substrate is injected via `CONSOLE_SUBSTRATE` (compose | kind), never inferred —
the same config-not-code contract the rest of the repo follows (RUNMODES.md). On
`kind` the snapshot is a UNION: KubeProvider for the cluster-resident workloads,
DockerProvider for the host-plane tooling that still runs in Compose. See ADR-0015.
"""

import asyncio
import os

import httpx
from app.services.status.core import (
    KUBE_OWNED_KEYS,
    StatusProvider,
    broker,
    get_snapshot,
    set_snapshot,
)
from app.services.status.docker import DockerProvider
from app.services.status.kube import KubeProvider

__all__ = ["broker", "get_snapshot", "poll_status_loop"]

POLL_INTERVAL_S = 3


class CompositeProvider:
    """kind substrate: cluster workloads from Kube, host-plane tooling from Docker."""

    def __init__(self) -> None:
        self._docker = DockerProvider()
        self._kube = KubeProvider()

    async def poll(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str] = frozenset()
    ) -> dict[str, dict]:
        docker_snap, kube_snap = await asyncio.gather(
            # Docker owns everything the cluster does not.
            self._docker.poll(http_client, exclude=KUBE_OWNED_KEYS | exclude),
            self._kube.poll(http_client, exclude=exclude),
        )
        return {**docker_snap, **kube_snap}


def _select_provider() -> StatusProvider:
    substrate = os.environ.get("CONSOLE_SUBSTRATE", "compose").strip().lower()
    if substrate == "kind":
        print("Status provider: composite (kind = Kube cluster + Docker host-plane)")
        return CompositeProvider()
    print("Status provider: docker (compose substrate)")
    return DockerProvider()


async def poll_status_loop():
    provider = _select_provider()
    async with httpx.AsyncClient() as http_client:
        while True:
            try:
                snapshot = await provider.poll(http_client)
            except Exception as e:
                print(f"Status poll failed: {e}")
                snapshot = get_snapshot()

            set_snapshot(snapshot)
            try:
                broker.publish(snapshot)
            except Exception as e:
                print(f"Failed to publish status: {e}")

            await asyncio.sleep(POLL_INTERVAL_S)
