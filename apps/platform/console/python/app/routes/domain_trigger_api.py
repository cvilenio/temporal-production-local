"""Generic descriptor-driven workflow trigger API (Phase B)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from ..domain_catalog import catalog_as_json
from ..domain_clients import domain_client_pool

router = APIRouter(prefix="/api/domain-trigger")


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    catalog = domain_client_pool.catalog()
    return {"domains": catalog_as_json(catalog)}


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return {"domains": await domain_client_pool.domain_status()}


@router.post("/trigger")
async def trigger_workflows(
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    domain = str(payload.get("domain") or "")
    workflow_type = str(payload.get("workflow_type") or "")
    sample_label = str(payload.get("sample_label") or "")
    raw_count = payload.get("count", 1)
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        return {"ok": False, "error": "count must be an integer between 1 and 5"}
    if not domain or not workflow_type or not sample_label:
        return {"ok": False, "error": "domain, workflow_type, and sample_label are required"}
    return await domain_client_pool.trigger(
        domain=domain,
        workflow_type=workflow_type,
        sample_label=sample_label,
        count=count,
    )
