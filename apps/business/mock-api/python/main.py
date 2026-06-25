import asyncio
import os
import random
import socket
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from obslog import get_logger, init_logging
from pydantic import BaseModel

log = get_logger("mock-api")

# Cache and Lock for Idempotency
_idempotency_cache: dict[str, dict] = {}
# Brief global guard for the cache dict + the per-key lock registry below.
# NOTE: never hold this across the latency-simulating compute_fn — doing so
# serializes EVERY request behind one lock (each held for the full simulated
# delay), which under concurrent load cascades into activity timeouts.
_cache_lock = asyncio.Lock()
# Per-idempotency-key locks: only requests sharing a key (genuine duplicates)
# serialize; distinct orders run concurrently.
_key_locks: dict[str, asyncio.Lock] = {}

# Ghost cache: tracks shipping labels created but whose response was "lost"
_shipping_ghost_cache: dict[str, str] = {}

# Attempt counters for demo purposes
_shipping_attempts: dict[str, int] = {}
_inventory_attempts: dict[str, int] = {}


async def _with_idempotency(
    idem_key: str | None, compute_fn: Callable[[], Awaitable[dict]]
) -> dict:
    if not idem_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    # Fast path + grab a per-key lock, all under a brief global guard.
    async with _cache_lock:
        if idem_key in _idempotency_cache:
            return _idempotency_cache[idem_key]
        key_lock = _key_locks.setdefault(idem_key, asyncio.Lock())

    # Serialize only same-key (duplicate) requests; the slow compute_fn runs
    # WITHOUT the global lock so distinct orders proceed concurrently.
    async with key_lock:
        async with _cache_lock:
            if idem_key in _idempotency_cache:
                return _idempotency_cache[idem_key]

        response = await compute_fn()

        async with _cache_lock:
            _idempotency_cache[idem_key] = response

    return response


# Models
class PaymentChargeRequest(BaseModel):
    token: str
    amount: float


class PaymentRefundRequest(BaseModel):
    capture_id: str
    amount: float


class InventoryReserveRequest(BaseModel):
    item_id: str
    quantity: int


class InventoryReleaseRequest(BaseModel):
    reservation_id: str
    item_id: str
    quantity: int


class ShippingRequest(BaseModel):
    address: str
    order_id: str


class ShippingCancelRequest(BaseModel):
    tracking_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Host-plane business mock: emit JSON to stdout (Docker Desktop) AND push
    # OTLP straight to the observability backend (lgtm), since no node agent
    # collects host containers. On Kubernetes this would flip to stdout-only.
    # See ADR-0018.
    push = os.getenv("LOG_OTLP_PUSH", "true").lower() != "false"
    handle = init_logging(
        os.getenv("OTEL_SERVICE_NAME", "mock-api"),
        level=os.getenv("LOG_LEVEL", "INFO"),
        fmt=os.getenv("LOG_FORMAT", "json"),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lgtm:4317")
        if push
        else None,
        namespace=os.getenv("SERVICE_NAMESPACE"),
        instance_id=os.getenv("HOSTNAME") or socket.gethostname(),
    )
    log.info("mock external systems API up")
    yield
    handle.shutdown()


app = FastAPI(title="Mock External Systems API", lifespan=lifespan)


async def _simulate_latency(base_ms_env: str, default_ms: str):
    base_ms = int(os.getenv(base_ms_env, default_ms))
    if base_ms > 0:
        # +/- 20% jitter
        jitter = random.uniform(0.8, 1.2)
        actual_ms = int(base_ms * jitter)
        log.debug("simulating response latency", actual_ms=actual_ms, base_ms=base_ms)
        await asyncio.sleep(actual_ms / 1000.0)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Endpoints
@app.post("/payment/charge")
async def charge_payment(
    request: PaymentChargeRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    async def compute() -> dict:
        await _simulate_latency("MOCK_PAYMENT_LATENCY_MS", "5000")
        return {"status": "success"}

    return await _with_idempotency(idempotency_key, compute)


@app.post("/payment/refund")
async def refund_payment(
    request: PaymentRefundRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async def compute() -> dict:
        await _simulate_latency("MOCK_PAYMENT_LATENCY_MS", "5000")
        return {"status": "success"}

    return await _with_idempotency(idempotency_key, compute)


@app.post("/inventory/reserve")
async def reserve_inventory(
    request: InventoryReserveRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    item_id = request.item_id.lower()
    if "flaky" in item_id:
        async with _cache_lock:
            attempt = _inventory_attempts.get(idempotency_key, 0) + 1
            _inventory_attempts[idempotency_key] = attempt

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
        await _simulate_latency("MOCK_INVENTORY_LATENCY_MS", "5000")
        return {"status": "success"}

    return await _with_idempotency(idempotency_key, compute)


@app.post("/inventory/release")
async def release_inventory(
    request: InventoryReleaseRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async def compute() -> dict:
        await _simulate_latency("MOCK_INVENTORY_LATENCY_MS", "5000")
        return {"status": "success"}

    return await _with_idempotency(idempotency_key, compute)


@app.post("/shipping/request")
async def request_shipping(
    request: ShippingRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    addr = request.address.lower()
    async with _cache_lock:
        attempt = _shipping_attempts.get(idempotency_key, 0) + 1
        _shipping_attempts[idempotency_key] = attempt

    hang_ms = int(os.getenv("MOCK_SHIPPING_HANG_MS", "15000"))

    if "ghost" in addr:
        if attempt == 1:
            tracking_id = f"TRK-{uuid.uuid4()}"
            _shipping_ghost_cache[idempotency_key] = tracking_id
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
        await _simulate_latency("MOCK_SHIPPING_LATENCY_MS", "8000")
        return {"status": "success", "tracking_id": tracking_id}

    return await _with_idempotency(idempotency_key, compute)


@app.get("/shipping/status/{idem_key}")
async def get_shipping_status(idem_key: str):
    await _simulate_latency("MOCK_SHIPPING_LATENCY_MS", "8000")
    # Check ghost cache first (labels created but response lost)
    ghost_tracking = _shipping_ghost_cache.get(idem_key)
    if ghost_tracking:
        return {"status": "confirmed", "tracking_id": ghost_tracking}
    async with _cache_lock:
        cached = _idempotency_cache.get(idem_key)
    if cached and "tracking_id" in cached:
        return {"status": "confirmed", "tracking_id": cached["tracking_id"]}
    return {"status": "not_found"}


@app.post("/shipping/cancel")
async def cancel_shipment(
    request: ShippingCancelRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async def compute() -> dict:
        await _simulate_latency("MOCK_SHIPPING_LATENCY_MS", "8000")
        return {"status": "success"}

    return await _with_idempotency(idempotency_key, compute)
