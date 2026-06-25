"""CloudStatusProvider — the Temporal Cloud liveness source (console-owned).

Cloud liveness is a PLATFORM concern, so the console owns it directly rather than
delegating to any single business app (otherwise every business scope we add would
have to duplicate the check). Two complementary signals are combined into one
`temporal-cloud` snapshot entry:

  1. **My namespace/region reachability** — a lazy read-only Temporal client built
     from the injected Cloud profile (TEMPORAL_ADDRESS / NAMESPACE / TLS / API_KEY)
     calls `check_health()` against the regional frontend. This reflects the exact
     account/region/namespace Terraform provisioned.
  2. **Temporal platform health** — the public Statuspage summary
     (status.temporal.io), the signal a customer watches for platform-wide incidents
     even when their own namespace still answers.

Active only on the `cloud` backend (the poll loop skips it otherwise). Like the
KubeProvider, it connects lazily and degrades to "down"/"unknown" rather than
raising, so the console boots fine with no Cloud reachable.
"""

import os
from datetime import timedelta
from typing import Any

import httpx
from app.services.status.core import SERVICE_REGISTRY, service_entry

_CLOUD_KEY = "temporal-cloud"
_HEALTH_TIMEOUT_S = 3
_STATUS_URL = os.environ.get(
    "TEMPORAL_STATUS_URL", "https://status.temporal.io/api/v2/summary.json"
)

# Statuspage indicator → our substrate-neutral vocabulary. `none` means all systems
# operational; anything else is an active platform incident (we surface as degraded
# while my namespace still answers — an unreachable namespace dominates to "down").
_INDICATOR_TO_STATUS = {
    "none": "healthy",
    "minor": "degraded",
    "major": "degraded",
    "critical": "degraded",
}

# Cloud Ops API (saas-api.tmprl.cloud) — account-level namespace/region inventory,
# reachable only with the read-only OBSERVER key (TEMPORAL_CLOUD_OPS_API_KEY); the
# per-namespace worker key cannot call it. Regions + namespaces change rarely, so
# the inventory is cached and refreshed every _OPS_REFRESH_EVERY poll cycles rather
# than every 3s (gentle on the Ops API).
_OPS_REFRESH_EVERY = 20  # poll cycles (~60s at POLL_INTERVAL_S=3)
# Cloud Ops API version header (proto VERSION bundled with the SDK). Overridable.
_OPS_API_VERSION = os.environ.get("TEMPORAL_CLOUD_API_VERSION", "v0.16.0")

# Temporal Cloud namespace ResourceState enum → our substrate-neutral vocabulary.
_NS_STATE = {
    0: "unknown",  # UNSPECIFIED
    1: "starting",  # ACTIVATING
    2: "degraded",  # ACTIVATION_FAILED
    3: "healthy",  # ACTIVE
    4: "starting",  # UPDATING
    5: "degraded",  # UPDATE_FAILED
    6: "down",  # DELETING
    7: "degraded",  # DELETE_FAILED
    8: "down",  # DELETED
    9: "down",  # SUSPENDED
    10: "degraded",  # EXPIRED
}


class CloudStatusProvider:
    def __init__(self) -> None:
        # temporalio Client — typed Any; imported lazily so the console boots even
        # when Cloud isn't configured/reachable.
        self._client: Any = None
        # Cloud Ops API client + cached inventory (regions/namespaces), refreshed
        # on a throttle (see _fetch_ops_inventory).
        self._ops_client: Any = None
        self._ops_cache: dict | None = None
        self._ops_tick: int = 0

    async def _ensure_client(self) -> bool:
        """Connect a namespaced client from the injected Cloud profile. Returns
        False if no address is configured or the connect fails (degrade, never
        raise out of poll)."""
        if self._client is not None:
            return True
        address = os.environ.get("TEMPORAL_ADDRESS", "").strip()
        namespace = os.environ.get("TEMPORAL_NAMESPACE", "").strip()
        if not address or not namespace:
            return False
        tls = os.environ.get("TEMPORAL_TLS", "false").strip().lower() == "true"
        api_key = os.environ.get("TEMPORAL_API_KEY", "").strip() or None
        try:
            from temporalio.client import Client

            self._client = await Client.connect(
                address, namespace=namespace, tls=tls, api_key=api_key
            )
            return True
        except Exception as e:
            print(f"CloudStatusProvider: connect failed ({address}): {e}")
            self._client = None
            return False

    async def _check_namespace(self) -> tuple[bool | None, int | None]:
        """(reachable, latency_ms). reachable is None when no client is configured.

        Uses DescribeNamespace, not the gRPC health service: Temporal Cloud API keys
        are NOT authorized for the bare health check (it returns "Request
        unauthorized"), but they ARE scoped to read the namespace they belong to. So
        a successful DescribeNamespace is the meaningful, authorized signal that *my*
        Terraform-provisioned namespace is reachable + the credential is valid.
        """
        if not await self._ensure_client():
            return (None, None)
        from datetime import datetime

        from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest

        namespace = os.environ.get("TEMPORAL_NAMESPACE", "").strip()
        start = datetime.now()
        try:
            await self._client.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace=namespace),
                timeout=timedelta(seconds=_HEALTH_TIMEOUT_S),
            )
            latency = int((datetime.now() - start).total_seconds() * 1000)
            return (True, latency)
        except Exception as e:
            print(f"CloudStatusProvider: describe_namespace failed: {e}")
            self._client = None  # drop so we reconnect next poll
            return (False, None)

    async def _check_statuspage(
        self, http_client: httpx.AsyncClient
    ) -> tuple[str | None, str | None]:
        """(indicator, description) from the public Statuspage; (None, None) on error."""
        try:
            resp = await http_client.get(_STATUS_URL, timeout=3.0)
            data = resp.json()
            status = data.get("status", {})
            return (status.get("indicator"), status.get("description"))
        except Exception as e:
            print(f"CloudStatusProvider: statuspage fetch failed: {e}")
            return (None, None)

    async def _ensure_ops_client(self) -> bool:
        """Connect the Cloud Ops API client from the read-only observer key. Returns
        False when no observer key is configured or the connect fails."""
        if self._ops_client is not None:
            return True
        ops_key = os.environ.get("TEMPORAL_CLOUD_OPS_API_KEY", "").strip()
        if not ops_key:
            return False
        try:
            from temporalio.client import CloudOperationsClient

            # The Cloud Ops API requires a `temporal-cloud-api-version` header (else
            # "cloud API version must be specified"); the bundled proto VERSION
            # tracks the SDK build. Overridable via env.
            self._ops_client = await CloudOperationsClient.connect(
                api_key=ops_key, version=_OPS_API_VERSION
            )
            return True
        except Exception as e:
            print(f"CloudStatusProvider: ops connect failed: {e}")
            self._ops_client = None
            return False

    async def _fetch_ops_inventory(self) -> dict | None:
        """Account-level regions + namespaces from the Cloud Ops API. Cached and
        refreshed every _OPS_REFRESH_EVERY polls. Returns None when no observer key
        is configured; returns the last good cache on a transient error."""
        if not os.environ.get("TEMPORAL_CLOUD_OPS_API_KEY", "").strip():
            return None
        do_fetch = self._ops_cache is None or (self._ops_tick % _OPS_REFRESH_EVERY == 0)
        self._ops_tick += 1
        if not do_fetch:
            return self._ops_cache
        if not await self._ensure_ops_client():
            return self._ops_cache
        try:
            from temporalio.api.cloud.cloudservice.v1 import (
                GetNamespacesRequest,
                GetRegionsRequest,
            )

            self_ns = os.environ.get("TEMPORAL_NAMESPACE", "").strip()
            # GetRegions returns the FULL Cloud region catalog (~20). We only want
            # the regions actually USED by our namespaces, so use the catalog purely
            # to enrich display (location) and compute "used" from the namespaces.
            rresp = await self._ops_client.cloud_service.get_regions(
                GetRegionsRequest()
            )
            catalog = {
                r.id: {"region": r.cloud_provider_region, "location": r.location}
                for r in rresp.regions
            }
            namespaces: list[dict] = []
            used_region_ids: list[str] = []
            token = ""
            for _ in range(10):  # page cap — guards a runaway loop
                nresp = await self._ops_client.cloud_service.get_namespaces(
                    GetNamespacesRequest(page_size=100, page_token=token)
                )
                for n in nresp.namespaces:
                    ns_regions = list(n.spec.regions)
                    for rid in ns_regions:
                        if rid not in used_region_ids:
                            used_region_ids.append(rid)
                    namespaces.append(
                        {
                            "handle": n.namespace,
                            "name": n.spec.name,
                            "regions": ns_regions,
                            "active_region": n.active_region,
                            "state": _NS_STATE.get(n.state, "unknown"),
                            "is_self": n.namespace == self_ns,
                        }
                    )
                token = nresp.next_page_token
                if not token:
                    break
            regions = [
                {"id": rid, **catalog.get(rid, {"region": rid, "location": ""})}
                for rid in used_region_ids
            ]
            self._ops_cache = {"regions": regions, "namespaces": namespaces}
            return self._ops_cache
        except Exception as e:
            print(f"CloudStatusProvider: ops inventory failed: {e}")
            self._ops_client = None  # drop so we reconnect next refresh
            return self._ops_cache

    async def poll(
        self, http_client: httpx.AsyncClient, exclude: frozenset[str] = frozenset()
    ) -> dict[str, dict]:
        if _CLOUD_KEY in exclude:
            return {}

        reachable, latency_ms = await self._check_namespace()
        indicator, description = await self._check_statuspage(http_client)
        inventory = await self._fetch_ops_inventory()

        # Combine: an unreachable namespace dominates (that's what my apps depend on);
        # otherwise a platform incident degrades; otherwise healthy.
        if reachable is False:
            derived = "down"
        elif indicator in ("minor", "major", "critical"):
            derived = "degraded"
        elif reachable is True or indicator == "none":
            derived = "healthy"
        else:
            derived = "unknown"

        address = os.environ.get("TEMPORAL_ADDRESS", "").strip()
        namespace = os.environ.get("TEMPORAL_NAMESPACE", "").strip()

        entry = service_entry(
            _CLOUD_KEY,
            SERVICE_REGISTRY[_CLOUD_KEY],
            docker_state="reachable" if reachable else "unreachable",
            docker_health=(f"{latency_ms}ms" if latency_ms is not None else None),
            http_res=None,
            container_name=namespace or "—",
            image=address or "temporal.cloud",
            ports=[],
            status_source="cloud",
            derived_status=derived,
        )
        # Cloud-specific tooltip fields (consumed by the architecture page's
        # __temporal_cloud__ tooltip on the cloud backend).
        entry["cloud"] = {
            "endpoint": address,
            "namespace": namespace,
            "namespace_reachable": reachable,
            "latency_ms": latency_ms,
            "statuspage_indicator": indicator,
            "statuspage_description": description,
            "statuspage_url": "https://status.temporal.io",
            # Account-level inventory (empty unless the observer key is configured).
            "regions": inventory["regions"] if inventory else [],
            "namespaces": inventory["namespaces"] if inventory else [],
        }
        return {_CLOUD_KEY: entry}
