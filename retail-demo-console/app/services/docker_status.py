import asyncio
import docker
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Set
from app.shared.temporal_ids import TaskQueue

SERVICE_REGISTRY = {
    "temporal": {
        "group": "Temporal Cloud",
        "icon_key": "server-rack",
        "display_name": "Temporal Server",
        "description": "Orchestration engine for long-running workflows",
        "http_probe": None,
    },

    "temporal-ui": {
        "group": "Temporal Cloud",
        "icon_key": "window",
        "display_name": "Temporal UI",
        "description": "Temporal Web Console",
        "http_probe": None,
    },
    "postgresql": {
        "group": "Temporal Cloud",
        "icon_key": "database",
        "display_name": "Temporal DB",
        "description": "Internal state store",
        "http_probe": None,
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
    },
    "pgweb-orders": {
        "group": "Tooling",
        "icon_key": "window",
        "display_name": "DB Browser (Orders)",
        "description": "pgweb",
        "http_probe": None,
    },
    "pgweb-temporal": {
        "group": "Tooling",
        "icon_key": "window",
        "display_name": "DB Browser (Temporal)",
        "description": "pgweb",
        "http_probe": None,
    }
}

_current_snapshot = {}

class StatusBroker:
    def __init__(self):
        self.connections: Set[asyncio.Queue] = set()

    async def connect(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.connections.add(q)
        # Push immediate snapshot for fast initial render
        if _current_snapshot:
            q.put_nowait(_current_snapshot)
        return q

    def disconnect(self, q: asyncio.Queue):
        self.connections.discard(q)

    def publish(self, message: Dict[str, Any]):
        for q in self.connections:
            try:
                q.put_nowait(message)
            except Exception as e:
                # Use print for now to match file style
                print(f"Status broker publish error: {e}")

broker = StatusBroker()

def get_snapshot():
    return _current_snapshot

def derive_status(docker_state: str, docker_health: str | None, http_ok: bool | None) -> str:
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

async def check_http(url: str, client: httpx.AsyncClient) -> dict:
    start = datetime.now()
    try:
        resp = await client.get(url, timeout=2.0)
        return {
            "url": url,
            "ok": resp.status_code == 200,
            "latency_ms": int((datetime.now() - start).total_seconds() * 1000),
            "checked_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception:
        return {
            "url": url,
            "ok": False,
            "latency_ms": int((datetime.now() - start).total_seconds() * 1000),
            "checked_at": datetime.now(timezone.utc).isoformat()
        }

async def _no_probe():
    return None

async def poll_status_loop():
    client = None
    while client is None:
        try:
            client = await asyncio.to_thread(docker.from_env)
        except Exception as e:
            print(f"Failed to connect to docker socket: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

    async with httpx.AsyncClient() as http_client:
        while True:
            try:
                # get only compose project containers
                all_containers = await asyncio.to_thread(lambda: client.containers.list(all=True))
                containers = {c.labels.get("com.docker.compose.service"): c for c in all_containers}
            except Exception as e:
                print(f"Docker API error: {e}")
                containers = {}

            tasks = []
            service_keys = []
            for compose_svc, config in SERVICE_REGISTRY.items():
                if config.get("http_probe") and compose_svc in containers and containers[compose_svc].status == "running":
                    tasks.append(check_http(config["http_probe"], http_client))
                else:
                    tasks.append(_no_probe())
                service_keys.append(compose_svc)
            
            results = await asyncio.gather(*tasks)

            def get_container_info(c):
                # Run attribute access in thread since it can trigger lazy HTTP calls
                state = c.status
                health = None
                if "Health" in c.attrs.get("State", {}):
                    health = c.attrs["State"]["Health"]["Status"]
                
                ports = []
                for port, bindings in c.attrs.get("NetworkSettings", {}).get("Ports", {}).items():
                    if bindings:
                        ports.append(port)
                        
                image_tag = "unknown"
                if c.image and c.image.tags:
                    image_tag = c.image.tags[0]
                    
                return state, health, ports, image_tag, c.name

            new_snapshot = {}
            for compose_svc, config, http_res in zip(service_keys, SERVICE_REGISTRY.values(), results):
                c = containers.get(compose_svc)
                if c:
                    try:
                        state, health, ports, image_tag, c_name = await asyncio.to_thread(get_container_info, c)
                    except Exception as e:
                        print(f"Error reading container {compose_svc}: {e}")
                        state, health, ports, image_tag, c_name = "unknown", None, [], "unknown", "unknown"
                    
                    new_snapshot[compose_svc] = {
                        **config,
                        "service_key": compose_svc,
                        "container_name": c_name,
                        "image": image_tag,
                        "compose_service": compose_svc,
                        "docker_state": state,
                        "docker_health": health,
                        "http_probe": http_res,
                        "derived_status": derive_status(state, health, http_res.get("ok") if http_res else None),
                        "ports": ports,
                    }
                else:
                    new_snapshot[compose_svc] = {
                        **config,
                        "service_key": compose_svc,
                        "container_name": "not-found",
                        "image": "unknown",
                        "compose_service": compose_svc,
                        "docker_state": "not-found",
                        "docker_health": None,
                        "http_probe": None,
                        "derived_status": "down",
                        "ports": [],
                    }
            
            global _current_snapshot
            _current_snapshot = new_snapshot
            try:
                broker.publish(new_snapshot)
            except Exception as e:
                print(f"Failed to publish status: {e}")
            
            await asyncio.sleep(3)
