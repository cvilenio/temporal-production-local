"""Shared status machinery: the service registry, the SSE broker, the snapshot
store, and the substrate-neutral status vocabulary.

A `StatusProvider` produces a snapshot — a dict keyed by service_key, each value a
node descriptor the architecture page renders. Two providers exist: `DockerProvider`
(host containers, the Compose substrate) and `KubeProvider` (kind pods); the loop
selects/combines them from the injected `CONSOLE_SUBSTRATE` (ADR-0015 phase-2).
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from app.shared.temporal_ids import TaskQueue

# In-network ports for the Docker probe fallback (must match docker-compose.yml
# container ports, not host-published ports).
# No exposed port; treat as running when core dependencies are healthy.
INFERRED_IF_DEPS_HEALTHY = ("orders-workflow-worker", "orders-activity-worker")
INFERRED_DEPS = ("temporal", "orders-service")

# SERVICE_REGISTRY — the canonical node catalog the architecture page renders.
#
# `group` is the logical plane (Temporal Cloud / Customer Environment / External
# Systems / Tooling). `kube` is the OPTIONAL cluster locator: on the kind
# substrate, KubeProvider sources these services from pods matching the selector
# instead of from the Docker socket. Services WITHOUT a `kube` locator (host-plane
# tooling, the Cloud-sim nodes) stay Docker/probe-sourced in every substrate.
SERVICE_REGISTRY = {
    # The managed Temporal Cloud endpoint, shown on the cloud backend. Its status is
    # NOT container-derived — the CloudStatusProvider probes my namespace's
    # reachability (SDK health check) AND the public Temporal Statuspage, and emits
    # this entry. The `temporal`/`temporal-ui`/`postgresql` trio below are the
    # OSS-on-Compose simulators kept for the (future) OSS backend.
    "temporal-cloud": {
        "group": "Temporal Cloud",
        "icon_key": "server-rack",
        "display_name": "Temporal Cloud",
        "description": "Managed Temporal namespace (account/region provisioned by Terraform)",
        "http_probe": None,
    },
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
        # On kind this is the orders-api Deployment (Service name is orders-service).
        "kube": {
            "namespace": "orders",
            "selector": "app.kubernetes.io/name=orders-api",
        },
    },
    "orders-workflow-worker": {
        "group": "Customer Environment",
        "icon_key": "worker",
        "display_name": "Workflow Worker",
        "description": "Orchestrates order workflows",
        "http_probe": None,
        "task_queue": TaskQueue.WORKFLOW,
        "kube": {
            "namespace": "orders",
            "selector": "app.kubernetes.io/name=orders-workflow",
        },
    },
    "orders-activity-worker": {
        "group": "Customer Environment",
        "icon_key": "worker",
        "display_name": "Activity Worker",
        "description": "Executes activities (IO/Side-effects)",
        "http_probe": None,
        "task_queue": TaskQueue.ACTIVITY,
        "kube": {
            "namespace": "orders",
            "selector": "app.kubernetes.io/name=orders-activity",
        },
    },
    "orders-db": {
        "group": "Customer Environment",
        "icon_key": "database",
        "display_name": "Orders DB",
        "description": "Customer orders db",
        "http_probe": None,
        "tcp_port": 5432,
        # CloudNativePG labels instance pods with cnpg.io/cluster=<clusterName>.
        "kube": {"namespace": "orders", "selector": "cnpg.io/cluster=orders-db"},
    },
    "mock-api": {
        # Stands apart from the customer's own environment: it represents systems
        # EXTERNAL to the business (payment, shipping, inventory providers) that the
        # demo mocks. Own group so the architecture view reads that boundary clearly.
        # Host-plane in EVERY substrate (the cluster does not run it), so no `kube`.
        "group": "External Systems",
        "icon_key": "api",
        "display_name": "External System Mocks",
        "description": "Simulated external dependencies (payment, shipping, inventory)",
        "http_probe": "http://mock-api:8000/health",
    },
    # Tooling strip
    "platform-console": {
        "group": "Tooling",
        "subgroup": "Consoles & Utilities",
        "icon_key": "window",
        "display_name": "Platform Console",
        "description": "This application",
        "http_probe": "http://platform-console:8086/healthz",
    },
    "ui-proxy": {
        "group": "Tooling",
        "subgroup": "Consoles & Utilities",
        "icon_key": "network",
        "display_name": "Temporal UI Proxy",
        "description": "Nginx CSP proxy",
        "http_probe": None,
        "tcp_port": 8081,
    },
    "pgweb-orders": {
        "group": "Tooling",
        "subgroup": "Consoles & Utilities",
        "icon_key": "window",
        "display_name": "DB Browser (Orders)",
        "description": "pgweb",
        "http_probe": None,
        "tcp_port": 8081,
    },
    "pgweb-temporal": {
        "group": "Tooling",
        "subgroup": "Consoles & Utilities",
        "icon_key": "window",
        "display_name": "DB Browser (Temporal)",
        "description": "pgweb",
        "http_probe": None,
        "tcp_port": 8081,
    },
    "lgtm": {
        "group": "Tooling",
        "subgroup": "Observability",
        "icon_key": "chart",
        "display_name": "Observability (LGTM)",
        "description": "Grafana, Tempo, Prometheus",
        "http_probe": "http://lgtm:3000/api/health",
    },
    "clickhouse": {
        "group": "Tooling",
        "subgroup": "Observability",
        "icon_key": "database",
        "display_name": "ClickHouse (Logs)",
        "description": "Log store (OTel otel_logs), read by Grafana",
        # /ping returns 200 "Ok." without auth — a clean liveness signal.
        "http_probe": "http://clickhouse:8123/ping",
    },
    "otel-collector": {
        "group": "Tooling",
        "subgroup": "Observability",
        "icon_key": "network",
        "display_name": "OTel Collector",
        "description": "OTLP→ClickHouse log ingest gateway",
        "http_probe": None,
        # No HTTP health endpoint; a TCP connect to the OTLP/HTTP receiver port
        # (in-network container port) confirms the gateway is listening.
        "tcp_port": 4318,
    },
    "codec-server": {
        "group": "Tooling",
        "subgroup": "Consoles & Utilities",
        "icon_key": "network",
        "display_name": "Codec Server",
        "description": "Decodes encrypted payloads for the Temporal UI/CLI",
        "http_probe": None,
    },
    # ── Cluster-visibility tooling (kind only — see KIND_ONLY_KEYS) ──────────
    "headlamp": {
        "group": "Tooling",
        "subgroup": "Cluster & Delivery",
        "icon_key": "window",
        "display_name": "Headlamp",
        "description": "Kubernetes cluster explorer",
        "http_probe": None,
    },
    "viz-proxy": {
        "group": "Tooling",
        "subgroup": "Cluster & Delivery",
        "icon_key": "network",
        "display_name": "Visibility Proxy",
        "description": "Frame-stripping proxy fronting Headlamp + ArgoCD",
        "http_probe": None,
        "tcp_port": 8087,
    },
    # ArgoCD runs IN the cluster (argocd namespace), so it is kube-sourced like the
    # orders workloads. The read-only console SA has cluster-wide pod get/list/watch.
    "argocd": {
        "group": "Tooling",
        "subgroup": "Cluster & Delivery",
        "icon_key": "network",
        "display_name": "ArgoCD",
        "description": "GitOps delivery controller",
        "http_probe": None,
        "kube": {
            "namespace": "argocd",
            "selector": "app.kubernetes.io/name=argocd-server",
        },
    },
    # ── Cluster substrate (kind only — see KIND_ONLY_KEYS) ───────────────────
    # The cluster itself and its image registry. NEITHER is a Compose service —
    # kind nodes and the zot registry are created by the cluster-up scripts, not
    # docker-compose — so they carry a `docker_container` NAME locator instead of
    # being matched by the `com.docker.compose.service` label like every other
    # Docker-sourced node. kind is represented by its control-plane container
    # (cluster liveness); the worker nodes follow it up/down.
    "kind-cluster": {
        "group": "Tooling",
        "subgroup": "Cluster & Delivery",
        "icon_key": "server-rack",
        "display_name": "kind Cluster",
        "description": "Local Kubernetes cluster (control-plane node)",
        "http_probe": None,
        "docker_container": "kind-control-plane",
    },
    "artifact-registry": {
        "group": "Tooling",
        "subgroup": "Cluster & Delivery",
        "icon_key": "database",
        "display_name": "Artifact Registry",
        "description": "Local OCI registry (zot) serving images to kind",
        "http_probe": None,
        "docker_container": "artifact-registry",
    },
}

# Service keys the cluster owns on the kind substrate (those with a `kube` locator).
KUBE_OWNED_KEYS = frozenset(
    key for key, cfg in SERVICE_REGISTRY.items() if cfg.get("kube")
)

# Substrate/backend applicability. The status loop excludes the irrelevant set so a
# service that only exists on the other substrate never shows up as spurious "down":
#   - OSS_ONLY_KEYS: the OSS-on-Compose Temporal simulators + their dev UIs. Present
#     only on the `oss` backend; excluded on the kind+Cloud path (the temporal-cloud
#     entry replaces them).
#   - KIND_ONLY_KEYS: cluster-visibility tooling that is meaningless without a kind
#     cluster. Excluded on the `compose` substrate.
OSS_ONLY_KEYS = frozenset(
    {"temporal", "temporal-ui", "postgresql", "ui-proxy", "pgweb-temporal"}
)
KIND_ONLY_KEYS = frozenset(
    {"headlamp", "viz-proxy", "argocd", "kind-cluster", "artifact-registry"}
)

_current_snapshot: dict[str, dict] = {}


class StatusProvider(Protocol):
    """Produces a partial snapshot keyed by service_key. Implementations own their
    own connection lifecycle and degrade gracefully (never raise out of poll)."""

    async def poll(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str] = frozenset()
    ) -> dict[str, dict]: ...


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


def get_snapshot() -> dict[str, dict]:
    return _current_snapshot


def set_snapshot(snapshot: dict[str, dict]) -> None:
    global _current_snapshot
    _current_snapshot = snapshot


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


def service_entry(
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
    derived_status: str | None = None,
) -> dict:
    """Build a node descriptor in the shape the architecture page consumes.

    `derived_status` is computed from the Docker tri-state by default; providers
    that already know the status (Kube) pass it explicitly.
    """
    return {
        **config,
        "service_key": compose_svc,
        "container_name": container_name,
        "image": image,
        "compose_service": compose_svc,
        "docker_state": docker_state,
        "docker_health": docker_health,
        "http_probe": http_res,
        "derived_status": derived_status
        if derived_status is not None
        else derive_status(
            docker_state, docker_health, http_res.get("ok") if http_res else None
        ),
        "ports": ports,
        "status_source": status_source,
    }
