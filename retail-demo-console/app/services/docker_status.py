import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import docker
import httpx
from app.shared.temporal_ids import TaskQueue

# In-network ports for probe fallback (must match docker-compose.yml container ports,
# not host-published ports from the operator machine).
# No exposed port; treat as running when core dependencies are healthy.
INFERRED_IF_DEPS_HEALTHY = ("orders-workflow-worker", "orders-activity-worker")
INFERRED_DEPS = ("temporal", "orders-service")

SERVICE_REGISTRY = {
    "temporal": {
        "group": "Temporal Cloud",
        "icon_key": "server-rack",
        "display_name": "Temporal Server",
        "description": "Orchestration engine for long-running workflows",
        "http_probe": None,
        "tcp_port": 7233,
    },
    "temporal-ui": {
        "group": "Temporal Cloud",
        "icon_key": "window",
        "display_name": "Temporal UI",
        "description": "Temporal Web Console",
        "http_probe": None,
        "tcp_port": 8080,
    },
    "postgresql": {
        "group": "Temporal Cloud",
        "icon_key": "database",
        "display_name": "Temporal DB",
        "description": "Internal state store",
        "http_probe": None,
        "tcp_port": 5432,
    },
    "orders-service": {
        "group": "Customer Environment",
        "icon_key": "api",
        "display_name": "Orders Service",
        "description": "Primary business API",
        "http_probe": "http://orders-service:8000/health",
    },
    "orders-workflow-worker": {
        "group": "Customer Environment",
        "icon_key": "worker",
        "display_name": "Workflow Worker",
        "description": "Orchestrates order workflows",
        "http_probe": None,
        "task_queue": TaskQueue.WORKFLOW,
    },
    "orders-activity-worker": {
        "group": "Customer Environment",
        "icon_key": "worker",
        "display_name": "Activity Worker",
        "description": "Executes activities (IO/Side-effects)",
        "http_probe": None,
        "task_queue": TaskQueue.ACTIVITY,
    },
    "orders-db": {
        "group": "Customer Environment",
        "icon_key": "database",
        "display_name": "Orders DB",
        "description": "Customer orders db",
        "http_probe": None,
        "tcp_port": 5432,
    },
    "mock-api": {
        "group": "Customer Environment",
        "icon_key": "api",
        "display_name": "External Systems Mock",
        "description": "Simulates Stripe/Fedex/etc",
        "http_probe": "http://mock-api:8000/health",
    },
    # Tooling strip
    "retail-demo-console": {
        "group": "Tooling",
        "icon_key": "window",
        "display_name": "Demo Console",
        "description": "This application",
        "http_probe": "http://retail-demo-console:8086/healthz",
    },
    "ui-proxy": {
        "group": "Tooling",
        "icon_key": "network",
        "display_name": "Temporal UI Proxy",
        "description": "Nginx CSP proxy",
        "http_probe": None,
        "tcp_port": 8081,
    },
    "pgweb-orders": {
        "group": "Tooling",
        "icon_key": "window",
        "display_name": "DB Browser (Orders)",
        "description": "pgweb",
        "http_probe": None,
        "tcp_port": 8081,
    },
    "pgweb-temporal": {
        "group": "Tooling",
        "icon_key": "window",
        "display_name": "DB Browser (Temporal)",
        "description": "pgweb",
        "http_probe": None,
        "tcp_port": 8081,
    },
    "lgtm": {
        "group": "Tooling",
        "icon_key": "chart",
        "display_name": "Observability (LGTM)",
        "description": "Grafana, Loki, Tempo, Prometheus",
        "http_probe": "http://lgtm:3000/api/health",
    },
}

_current_snapshot = {}


class StatusBroker:
    def __init__(self):
        self.connections: set[asyncio.Queue] = set()

    async def connect(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.connections.add(q)
        # Push immediate snapshot for fast initial render
        if _current_snapshot:
            q.put_nowait(_current_snapshot)
        return q

    def disconnect(self, q: asyncio.Queue):
        self.connections.discard(q)

    def publish(self, message: dict[str, Any]):
        for q in self.connections:
            try:
                q.put_nowait(message)
            except Exception as e:
                # Use print for now to match file style
                print(f"Status broker publish error: {e}")


broker = StatusBroker()


def get_snapshot():
    return _current_snapshot


def derive_status(
    docker_state: str, docker_health: str | None, http_ok: bool | None
) -> str:
    if docker_state in ("exited", "dead", "not-found"):
        return "down"
    if docker_state == "paused":
        return "paused"
    if docker_state == "restarting":
        return "restarting"

    if docker_state == "running":
        if http_ok is False:
            return "degraded"
        if http_ok is True:
            return "healthy"

        if docker_health == "unhealthy":
            return "degraded"
        if docker_health == "starting":
            return "starting"

        return "healthy"

    return "unknown"


async def check_tcp(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def check_http(url: str, client: httpx.AsyncClient) -> dict:
    start = datetime.now()
    try:
        resp = await client.get(url, timeout=2.0)
        return {
            "url": url,
            "ok": resp.status_code == 200,
            "latency_ms": int((datetime.now() - start).total_seconds() * 1000),
            "checked_at": datetime.now(UTC).isoformat(),
        }
    except Exception:
        return {
            "url": url,
            "ok": False,
            "latency_ms": int((datetime.now() - start).total_seconds() * 1000),
            "checked_at": datetime.now(UTC).isoformat(),
        }


async def _no_probe():
    return None


def _service_entry(
    compose_svc: str,
    config: dict,
    *,
    docker_state: str,
    docker_health: str | None,
    http_res: dict | None,
    container_name: str,
    image: str,
    ports: list[str],
    status_source: str,
) -> dict:
    return {
        **config,
        "service_key": compose_svc,
        "container_name": container_name,
        "image": image,
        "compose_service": compose_svc,
        "docker_state": docker_state,
        "docker_health": docker_health,
        "http_probe": http_res,
        "derived_status": derive_status(
            docker_state, docker_health, http_res.get("ok") if http_res else None
        ),
        "ports": ports,
        "status_source": status_source,
    }


async def _gather_http_probes(http_client: httpx.AsyncClient) -> list:
    tasks = []
    for config in SERVICE_REGISTRY.values():
        if config.get("http_probe"):
            tasks.append(check_http(config["http_probe"], http_client))
        else:
            tasks.append(_no_probe())
    return await asyncio.gather(*tasks)


async def build_snapshot_via_probes(http_client: httpx.AsyncClient) -> dict:
    http_results = await _gather_http_probes(http_client)

    tcp_targets = [
        (compose_svc, config["tcp_port"])
        for compose_svc, config in SERVICE_REGISTRY.items()
        if config.get("tcp_port") is not None and not config.get("http_probe")
    ]
    tcp_checks = await asyncio.gather(
        *[check_tcp(host, port) for host, port in tcp_targets]
    )
    tcp_results = dict(zip((host for host, _ in tcp_targets), tcp_checks, strict=True))

    snapshot: dict[str, dict] = {}
    for compose_svc, config, http_res in zip(
        SERVICE_REGISTRY.keys(), SERVICE_REGISTRY.values(), http_results, strict=True
    ):
        reachable = False
        if http_res is not None:
            reachable = http_res.get("ok", False)
        elif config.get("tcp_port") is not None:
            reachable = tcp_results.get(compose_svc, False)

        snapshot[compose_svc] = _service_entry(
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
        config = SERVICE_REGISTRY[compose_svc]
        running = deps_healthy
        snapshot[compose_svc] = _service_entry(
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


async def build_snapshot_via_docker(client, http_client: httpx.AsyncClient) -> dict:
    all_containers = await asyncio.to_thread(lambda: client.containers.list(all=True))
    containers = {
        c.labels.get("com.docker.compose.service"): c
        for c in all_containers
        if c.labels.get("com.docker.compose.service")
    }

    tasks = []
    for compose_svc, config in SERVICE_REGISTRY.items():
        if (
            config.get("http_probe")
            and compose_svc in containers
            and containers[compose_svc].status == "running"
        ):
            tasks.append(check_http(config["http_probe"], http_client))
        else:
            tasks.append(_no_probe())
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
    for compose_svc, config, http_res in zip(
        SERVICE_REGISTRY.keys(), SERVICE_REGISTRY.values(), results, strict=True
    ):
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

            snapshot[compose_svc] = _service_entry(
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
            snapshot[compose_svc] = _service_entry(
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


def _connect_docker_client():
    return docker.from_env()


async def poll_status_loop():
    runtime_client = None
    use_probes_only = False
    probe_fallback_logged = False

    async with httpx.AsyncClient() as http_client:
        while True:
            if runtime_client is None and not use_probes_only:
                try:
                    runtime_client = await asyncio.to_thread(_connect_docker_client)
                except Exception as e:
                    if not probe_fallback_logged:
                        host = os.environ.get("DOCKER_HOST", "default socket")
                        print(
                            f"Container runtime socket unavailable ({host}): {e}. "
                            "Falling back to HTTP/TCP probes on the compose network."
                        )
                        probe_fallback_logged = True
                    use_probes_only = True

            try:
                if use_probes_only:
                    new_snapshot = await build_snapshot_via_probes(http_client)
                else:
                    new_snapshot = await build_snapshot_via_docker(
                        runtime_client, http_client
                    )
            except Exception as e:
                print(f"Docker API error: {e}; using HTTP/TCP probes for this cycle.")
                use_probes_only = True
                runtime_client = None
                new_snapshot = await build_snapshot_via_probes(http_client)

            global _current_snapshot
            _current_snapshot = new_snapshot
            try:
                broker.publish(new_snapshot)
            except Exception as e:
                print(f"Failed to publish status: {e}")

            await asyncio.sleep(3)
