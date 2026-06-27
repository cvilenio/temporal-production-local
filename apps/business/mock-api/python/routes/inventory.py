"""Mock inventory endpoints — all under the /inventory prefix.

`reserve` simulates a flaky dependency for item ids containing "flaky": it 503s on the
first two attempts (per idempotency key) and succeeds on the third, exercising Temporal's
activity retry policy.
"""

from dependencies import (
    cache_lock,
    inventory_attempts,
    log,
    simulate_latency,
    with_idempotency,
)
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from settings import settings

router = APIRouter(prefix="/inventory")


class InventoryReserveRequest(BaseModel):
    item_id: str
    quantity: int


class InventoryReleaseRequest(BaseModel):
    reservation_id: str
    item_id: str
    quantity: int


@router.post("/reserve")
async def reserve_inventory(
    request: InventoryReserveRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    item_id = request.item_id.lower()
    if "flaky" in item_id:
        async with cache_lock:
            attempt = inventory_attempts.get(idempotency_key, 0) + 1
            inventory_attempts[idempotency_key] = attempt

        if attempt <= 2:
            log.warning(
                "inventory flaky: returning 503",
                idempotency_key=idempotency_key,
                attempt=attempt,
            )
            raise HTTPException(status_code=503, detail="Service Unavailable")
        log.info(
            "inventory flaky: succeeding",
            idempotency_key=idempotency_key,
            attempt=attempt,
        )

    async def compute() -> dict:
        await simulate_latency(settings.mock_inventory_latency_ms)
        return {"status": "success"}

    return await with_idempotency(idempotency_key, compute)


@router.post("/release")
async def release_inventory(
    request: InventoryReleaseRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async def compute() -> dict:
        await simulate_latency(settings.mock_inventory_latency_ms)
        return {"status": "success"}

    return await with_idempotency(idempotency_key, compute)
