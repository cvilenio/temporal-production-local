"""Mock shipping endpoints — all under the /shipping prefix.

Address-driven failure scenarios (per idempotency key):
  - "ghost": label IS created but the response is lost (504); read-after-write status
    later finds it.
  - "flaky": first attempt hangs then 504s; the retry succeeds.
  - "lost":  every attempt hangs then 504s.
"""

import asyncio
import uuid

from dependencies import (
    cache_lock,
    idempotency_cache,
    log,
    shipping_attempts,
    shipping_ghost_cache,
    simulate_latency,
    with_idempotency,
)
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from settings import settings

router = APIRouter(prefix="/shipping")


class ShippingRequest(BaseModel):
    address: str
    order_id: str


class ShippingCancelRequest(BaseModel):
    tracking_id: str


@router.post("/request")
async def request_shipping(
    request: ShippingRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    addr = request.address.lower()
    async with cache_lock:
        attempt = shipping_attempts.get(idempotency_key, 0) + 1
        shipping_attempts[idempotency_key] = attempt

    hang_ms = settings.mock_shipping_hang_ms

    if "ghost" in addr:
        if attempt == 1:
            tracking_id = f"TRK-{uuid.uuid4()}"
            shipping_ghost_cache[idempotency_key] = tracking_id
            log.warning(
                "shipping ghost: label created, hanging",
                idempotency_key=idempotency_key,
                tracking_id=tracking_id,
                hang_ms=hang_ms,
            )
        else:
            log.warning(
                "shipping ghost: label already exists, hanging",
                idempotency_key=idempotency_key,
                hang_ms=hang_ms,
            )
        await asyncio.sleep(hang_ms / 1000)
        raise HTTPException(status_code=504, detail="Gateway Timeout")

    if "flaky" in addr:
        if attempt == 1:
            log.warning(
                "shipping flaky: attempt 1 hanging",
                idempotency_key=idempotency_key,
                hang_ms=hang_ms,
            )
            await asyncio.sleep(hang_ms / 1000)
            raise HTTPException(status_code=504, detail="Gateway Timeout")
        log.info(
            "shipping flaky: succeeding",
            idempotency_key=idempotency_key,
            attempt=attempt,
        )

    if "lost" in addr:
        log.warning(
            "shipping lost: hanging",
            idempotency_key=idempotency_key,
            attempt=attempt,
            hang_ms=hang_ms,
        )
        await asyncio.sleep(hang_ms / 1000)
        raise HTTPException(status_code=504, detail="Gateway Timeout")

    async def compute() -> dict:
        tracking_id = f"TRK-{uuid.uuid4()}"
        await simulate_latency(settings.mock_shipping_latency_ms)
        return {"status": "success", "tracking_id": tracking_id}

    return await with_idempotency(idempotency_key, compute)


@router.get("/status/{idem_key}")
async def get_shipping_status(idem_key: str):
    await simulate_latency(settings.mock_shipping_latency_ms)
    # Check ghost cache first (labels created but response lost)
    ghost_tracking = shipping_ghost_cache.get(idem_key)
    if ghost_tracking:
        return {"status": "confirmed", "tracking_id": ghost_tracking}
    async with cache_lock:
        cached = idempotency_cache.get(idem_key)
    if cached and "tracking_id" in cached:
        return {"status": "confirmed", "tracking_id": cached["tracking_id"]}
    return {"status": "not_found"}


@router.post("/cancel")
async def cancel_shipment(
    request: ShippingCancelRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async def compute() -> dict:
        await simulate_latency(settings.mock_shipping_latency_ms)
        return {"status": "success"}

    return await with_idempotency(idempotency_key, compute)
