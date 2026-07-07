"""Per-domain Temporal clients for the generic trigger path (boot-resilient, ADR-0015).

Each domain namespace gets its own client built via appkit.build_temporal_client with
that domain's data converter. Connection failures degrade to a recorded error — they
never take down the console process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from appkit import build_temporal_client, data_converter_for_domain
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from .domain_catalog import CatalogDomain, WorkflowSample, load_catalog
from .settings import settings


@dataclass
class DomainClientState:
    client: Client | None = None
    error: str | None = None


@dataclass
class TriggerResult:
    workflow_id: str
    run_id: str
    temporal_ui_url: str


class DomainClientPool:
    def __init__(self) -> None:
        self._states: dict[str, DomainClientState] = {}

    def _exclude(self) -> set[str]:
        raw = settings.generic_trigger_exclude_domains.strip()
        if not raw:
            return set()
        return {part.strip() for part in raw.split(",") if part.strip()}

    def catalog(self) -> list[CatalogDomain]:
        return load_catalog(exclude_domains=self._exclude())

    def _trigger_enabled(self) -> bool:
        return bool(settings.temporal_trigger_address.strip())

    async def _get_client(self, domain: str) -> DomainClientState:
        if domain in self._states:
            return self._states[domain]
        state = DomainClientState()
        self._states[domain] = state
        if not self._trigger_enabled():
            state.error = "temporal trigger not configured (set TEMPORAL_TRIGGER_ADDRESS)"
            return state
        try:
            converter = data_converter_for_domain(domain)
        except Exception as exc:
            state.error = f"descriptor/converter: {exc}"
            return state
        try:
            state.client = await build_temporal_client(
                address=settings.temporal_trigger_address,
                namespace=domain,
                tls=settings.temporal_trigger_tls,
                api_key=settings.temporal_trigger_api_key or None,
                tls_client_cert_path=settings.temporal_tls_client_cert_path or None,
                tls_client_key_path=settings.temporal_tls_client_key_path or None,
                tls_server_ca_cert_path=settings.temporal_tls_server_ca_cert_path or None,
                tls_domain=settings.temporal_trigger_tls_domain or None,
                data_converter=converter,
            )
        except Exception as exc:
            state.error = f"connect failed: {exc}"
            state.client = None
        return state

    def _find_sample(
        self, domain: str, workflow_type: str, sample_label: str
    ) -> tuple[CatalogDomain, WorkflowSample, str] | None:
        for entry in self.catalog():
            if entry.domain != domain:
                continue
            for wf in entry.workflows:
                if wf.type != workflow_type:
                    continue
                for sample in wf.samples:
                    if sample.label == sample_label:
                        return entry, sample, wf.task_queue
        return None

    def temporal_ui_url(self, namespace: str, workflow_id: str, run_id: str) -> str:
        base = settings.temporal_ui_embed_url.rstrip("/")
        if not base:
            return ""
        ns = quote(namespace, safe="")
        wid = quote(workflow_id, safe="")
        if not run_id:
            return f"{base}/namespaces/{ns}/workflows/{wid}/history"
        rid = quote(run_id, safe="")
        return f"{base}/namespaces/{ns}/workflows/{wid}/{rid}/history"

    async def trigger(
        self,
        *,
        domain: str,
        workflow_type: str,
        sample_label: str,
        count: int,
    ) -> dict[str, Any]:
        if count < 1 or count > 5:
            return {"ok": False, "error": "count must be between 1 and 5"}
        match = self._find_sample(domain, workflow_type, sample_label)
        if match is None:
            return {"ok": False, "error": "unknown domain/workflow/sample combination"}
        _, sample, task_queue = match
        state = await self._get_client(domain)
        if state.client is None:
            return {
                "ok": False,
                "error": state.error or "Temporal client unavailable for domain",
                "domain": domain,
            }
        started: list[TriggerResult] = []
        errors: list[str] = []
        for _ in range(count):
            wf_id = f"{domain}-{sample_label}-{uuid.uuid4().hex[:10]}"
            try:
                handle = await state.client.start_workflow(
                    workflow_type,
                    sample.input,
                    id=wf_id,
                    task_queue=task_queue,
                )
                run_id = handle.result_run_id or ""
                started.append(
                    TriggerResult(
                        workflow_id=handle.id,
                        run_id=run_id,
                        temporal_ui_url=self.temporal_ui_url(domain, handle.id, run_id),
                    )
                )
            except Exception as exc:
                errors.append(str(exc))
        return {
            "ok": len(started) > 0 and not errors,
            "domain": domain,
            "workflow_type": workflow_type,
            "sample_label": sample_label,
            "task_queue": task_queue,
            "started": [
                {
                    "workflow_id": r.workflow_id,
                    "run_id": r.run_id,
                    "temporal_ui_url": r.temporal_ui_url,
                }
                for r in started
            ],
            "errors": errors,
        }

    async def describe_task_queue(
        self, domain: str, task_queue: str, *, activity: bool = False
    ) -> dict[str, Any]:
        state = await self._get_client(domain)
        if state.client is None:
            return {
                "ok": False,
                "domain": domain,
                "task_queue": task_queue,
                "error": state.error or "client unavailable",
            }
        queue_type = (
            TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY
            if activity
            else TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW
        )
        try:
            resp = await state.client.workflow_service.describe_task_queue(
                DescribeTaskQueueRequest(
                    namespace=domain,
                    task_queue={"name": task_queue},
                    task_queue_type=queue_type,
                )
            )
            pollers = len(resp.pollers or [])
            return {
                "ok": True,
                "domain": domain,
                "task_queue": task_queue,
                "pollers": pollers,
                "live": pollers > 0,
            }
        except Exception as exc:
            return {
                "ok": False,
                "domain": domain,
                "task_queue": task_queue,
                "error": str(exc),
            }

    async def domain_status(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in self.catalog():
            state = await self._get_client(entry.domain)
            row: dict[str, Any] = {
                "domain": entry.domain,
                "connected": state.client is not None,
                "error": state.error,
            }
            if entry.workflow_task_queue:
                row["workflow_queue"] = await self.describe_task_queue(
                    entry.domain, entry.workflow_task_queue, activity=False
                )
            if entry.activity_task_queue:
                row["activity_queue"] = await self.describe_task_queue(
                    entry.domain, entry.activity_task_queue, activity=True
                )
            rows.append(row)
        return rows


domain_client_pool = DomainClientPool()
