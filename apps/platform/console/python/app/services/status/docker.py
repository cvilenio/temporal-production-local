"""DockerProvider — the Compose-substrate status source (the original behavior).

Reads container state over the Docker socket; falls back to HTTP/TCP probes on the
Compose network when the socket is unavailable. `exclude` lets the composite drop
the cluster-owned services on the kind substrate so they aren't probed in vain.
"""

import asyncio
import os

import docker
import httpx
from app.services.status.core import (
    INFERRED_DEPS,
    INFERRED_IF_DEPS_HEALTHY,
    SERVICE_REGISTRY,
    check_http,
    check_tcp,
    service_entry,
)


def _connect_docker_client():
    return docker.from_env()


class DockerProvider:
    def __init__(self) -> None:
        self._client = None
        self._use_probes_only = False
        self._probe_fallback_logged = False

    async def poll(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str] = frozenset()
    ) -> dict[str, dict]:
        if self._client is None and not self._use_probes_only:
            try:
                self._client = await asyncio.to_thread(_connect_docker_client)
            except Exception as e:
                if not self._probe_fallback_logged:
                    host = os.environ.get("DOCKER_HOST", "default socket")
                    print(
                        f"Container runtime socket unavailable ({host}): {e}. "
                        "Falling back to HTTP/TCP probes on the compose network."
                    )
                    self._probe_fallback_logged = True
                self._use_probes_only = True

        try:
            if self._use_probes_only:
                return await self._via_probes(http_client, exclude)
            return await self._via_docker(http_client, exclude)
        except Exception as e:
            print(f"Docker API error: {e}; using HTTP/TCP probes for this cycle.")
            self._use_probes_only = True
            self._client = None
            return await self._via_probes(http_client, exclude)

    def _keys(self, exclude: frozenset[str]) -> list[str]:
        return [k for k in SERVICE_REGISTRY if k not in exclude]

    async def _via_probes(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str]
    ) -> dict[str, dict]:
        keys = self._keys(exclude)

        http_results = await asyncio.gather(
            *[
                check_http(SERVICE_REGISTRY[k]["http_probe"], http_client)
                if SERVICE_REGISTRY[k].get("http_probe")
                else _none()
                for k in keys
            ]
        )

        tcp_targets = [
            (k, SERVICE_REGISTRY[k]["tcp_port"])
            for k in keys
            if SERVICE_REGISTRY[k].get("tcp_port") is not None
            and not SERVICE_REGISTRY[k].get("http_probe")
        ]
        tcp_checks = await asyncio.gather(
            *[check_tcp(host, port) for host, port in tcp_targets]
        )
        tcp_results = dict(
            zip((host for host, _ in tcp_targets), tcp_checks, strict=True)
        )

        snapshot: dict[str, dict] = {}
        for compose_svc, http_res in zip(keys, http_results, strict=True):
            config = SERVICE_REGISTRY[compose_svc]
            reachable = False
            if http_res is not None:
                reachable = http_res.get("ok", False)
            elif config.get("tcp_port") is not None:
                reachable = tcp_results.get(compose_svc, False)

            snapshot[compose_svc] = service_entry(
                compose_svc,
                config,
                docker_state="running" if reachable else "not-found",
                docker_health=None,
                http_res=http_res,
                container_name=compose_svc,
                image="probe",
                ports=[],
                status_source="probe",
            )

        dep_statuses = [
            snapshot[d]["derived_status"] for d in INFERRED_DEPS if d in snapshot
        ]
        deps_healthy = dep_statuses and all(s == "healthy" for s in dep_statuses)

        for compose_svc in INFERRED_IF_DEPS_HEALTHY:
            if compose_svc in exclude:
                continue
            config = SERVICE_REGISTRY[compose_svc]
            running = bool(deps_healthy)
            snapshot[compose_svc] = service_entry(
                compose_svc,
                config,
                docker_state="running" if running else "not-found",
                docker_health=None,
                http_res=None,
                container_name=compose_svc,
                image="probe",
                ports=[],
                status_source="inferred",
            )

        return snapshot

    async def _via_docker(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str]
    ) -> dict[str, dict]:
        client = self._client
        assert client is not None  # poll() only routes here once connected
        all_containers = await asyncio.to_thread(
            lambda: client.containers.list(all=True)
        )
        containers = {
            c.labels.get("com.docker.compose.service"): c
            for c in all_containers
            if c.labels.get("com.docker.compose.service")
        }

        keys = self._keys(exclude)
        tasks = []
        for compose_svc in keys:
            config = SERVICE_REGISTRY[compose_svc]
            if (
                config.get("http_probe")
                and compose_svc in containers
                and containers[compose_svc].status == "running"
            ):
                tasks.append(check_http(config["http_probe"], http_client))
            else:
                tasks.append(_none())
        results = await asyncio.gather(*tasks)

        def get_container_info(c):
            state = c.status
            health = None
            if "Health" in c.attrs.get("State", {}):
                health = c.attrs["State"]["Health"]["Status"]

            ports = []
            for port, bindings in (
                c.attrs.get("NetworkSettings", {}).get("Ports", {}).items()
            ):
                if bindings:
                    ports.append(port)

            image_tag = "unknown"
            if c.image and c.image.tags:
                image_tag = c.image.tags[0]

            return state, health, ports, image_tag, c.name

        snapshot: dict[str, dict] = {}
        for compose_svc, http_res in zip(keys, results, strict=True):
            config = SERVICE_REGISTRY[compose_svc]
            c = containers.get(compose_svc)
            if c:
                try:
                    state, health, ports, image_tag, c_name = await asyncio.to_thread(
                        get_container_info, c
                    )
                except Exception as e:
                    print(f"Error reading container {compose_svc}: {e}")
                    state, health, ports, image_tag, c_name = (
                        "unknown",
                        None,
                        [],
                        "unknown",
                        compose_svc,
                    )

                snapshot[compose_svc] = service_entry(
                    compose_svc,
                    config,
                    docker_state=state,
                    docker_health=health,
                    http_res=http_res,
                    container_name=c_name,
                    image=image_tag,
                    ports=ports,
                    status_source="docker",
                )
            else:
                snapshot[compose_svc] = service_entry(
                    compose_svc,
                    config,
                    docker_state="not-found",
                    docker_health=None,
                    http_res=None,
                    container_name="not-found",
                    image="unknown",
                    ports=[],
                    status_source="docker",
                )

        return snapshot


async def _none():
    return None
