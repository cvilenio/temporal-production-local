"""Substrate- and backend-aware live-status service.

Public surface (imported by main.py / routes):
  - `broker`, `get_snapshot` — the SSE broker and last snapshot
  - `poll_status_loop` — the background poller (selects providers by substrate/backend)

Two injected descriptors drive the snapshot, never inferred — the config-not-code
contract the rest of the repo follows (RUNMODES.md, ADR-0015):

  - `CONSOLE_SUBSTRATE` (compose | kind) — WHERE the workloads run. On `kind` the
    snapshot is a UNION: KubeProvider for the cluster-resident workloads,
    DockerProvider for the host-plane tooling that still runs in Compose.
  - `CONSOLE_BACKEND` (cloud | oss) — the Temporal backend. On `cloud` the
    console-owned CloudStatusProvider contributes a real `temporal-cloud` entry
    (namespace reachability + public Statuspage). On `oss` the in-Compose
    temporal/temporal-ui/postgresql containers supply it instead.

Irrelevant nodes are excluded so nothing renders as spurious "down": OSS-only nodes
off the `oss` backend, cluster-visibility tooling off the `kind` substrate, and the
`temporal-cloud` node off the base providers (it is cloud-probe-owned).
"""

import asyncio
import logging
import os

import httpx
from app.services.status.cloud import CloudStatusProvider
from app.services.status.core import (
    KIND_ONLY_KEYS,
    KUBE_OWNED_KEYS,
    OSS_ONLY_KEYS,
    StatusProvider,
    broker,
    get_snapshot,
    set_snapshot,
)
from app.services.status.docker import DockerProvider
from app.services.status.kube import KubeProvider

__all__ = ["broker", "get_snapshot", "poll_status_loop"]

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 3

# The Cloud endpoint node is produced ONLY by the CloudStatusProvider; the base
# (docker/composite) providers must never emit it (they would mark it "down").
_CLOUD_KEY = "temporal-cloud"


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


class RootProvider:
    """Top-level provider: a substrate base (docker/composite) plus the optional
    Cloud probe, with the substrate/backend exclusions applied to the base."""

    def __init__(
        self,
        base: StatusProvider,
        cloud: CloudStatusProvider | None,
        base_exclude: frozenset[str],
    ) -> None:
        self._base = base
        self._cloud = cloud
        self._base_exclude = base_exclude

    async def poll(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str] = frozenset()
    ) -> dict[str, dict]:
        ex = self._base_exclude | exclude
        if self._cloud is None:
            return await self._base.poll(http_client, exclude=ex)
        base_snap, cloud_snap = await asyncio.gather(
            self._base.poll(http_client, exclude=ex),
            self._cloud.poll(http_client, exclude=exclude),
        )
        return {**base_snap, **cloud_snap}


def _select_provider() -> StatusProvider:
    substrate = os.environ.get("CONSOLE_SUBSTRATE", "compose").strip().lower()
    backend = os.environ.get("CONSOLE_BACKEND", "cloud").strip().lower()

    base: StatusProvider = (
        CompositeProvider() if substrate == "kind" else DockerProvider()
    )

    # Exclusions for the base providers. temporal-cloud is always cloud-owned.
    base_exclude = {_CLOUD_KEY}
    if backend != "oss":
        base_exclude |= OSS_ONLY_KEYS  # no in-Compose Temporal cluster on Cloud
    if substrate != "kind":
        base_exclude |= KIND_ONLY_KEYS  # cluster-visibility tooling needs a cluster

    cloud = CloudStatusProvider() if backend == "cloud" else None

    logger.info(
        "status providers selected",
        extra={
            "base": "composite" if substrate == "kind" else "docker",
            "substrate": substrate,
            "backend": backend,
            "cloud_probe": "on" if cloud else "off",
        },
    )
    return RootProvider(base, cloud, frozenset(base_exclude))


async def poll_status_loop():
    provider = _select_provider()
    async with httpx.AsyncClient() as http_client:
        while True:
            try:
                snapshot = await provider.poll(http_client)
            except Exception as e:
                logger.warning("status poll failed", extra={"error": repr(e)})
                snapshot = get_snapshot()

            set_snapshot(snapshot)
            try:
                broker.publish(snapshot)
            except Exception as e:
                logger.warning("failed to publish status", extra={"error": repr(e)})

            await asyncio.sleep(POLL_INTERVAL_S)
