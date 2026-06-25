import hashlib
import json
import os
import socket
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from obslog import bound, get_logger
from sqlalchemy import select, text

from orders.config import settings
from orders.db.engine import Database
from orders.db.models import Base, IdempotencyRecord, Order
from orders.resources import container, get_database, get_temporal_service
from orders.services.temporal import TemporalService
from orders.shared.ids import order_id_from_key
from orders.shared.models import (
    CustomerStatusUpdate,
    InventoryReservationUpdate,
    OrderFailRequest,
    OrderRequest,
    OrderStatus,
    PaymentCaptureUpdate,
    ShipmentUpdate,
)
from orders.shared.workflow_io import OrderWorkflowInput

log = get_logger(__name__)


async def get_db_session(db: Database = Depends(get_database)):
    async for session in db.get_session():
        yield session


# --- App Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Per-process service name override — must happen before init_resources so
    # the telemetry resource initialises with the correct service name.
    container.config.otel_service_name.override("orders-service")
    container.config.service_instance_id.override(
        os.getenv("HOSTNAME") or socket.gethostname()
    )
    # Start telemetry (OTel providers + Prometheus metrics endpoint + obslog).
    # Not awaited: the telemetry resource is a sync generator.
    container.init_resources()

    # Initialize DB schema
    db = get_database()
    async with db._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Connect Temporal client (TemporalService carries the OTel Runtime +
    # TracingInterceptor injected by the container).
    temporal_service = get_temporal_service()
    await temporal_service.connect()

    yield

    await db.disconnect()
    # Flush in-flight spans / logs / metrics before the process exits.
    container.shutdown_resources()


app = FastAPI(title="Orders Service", lifespan=lifespan)


@app.middleware("http")
async def bind_request_context(request: Request, call_next):
    """Bind a per-request id + route into the log context (concurrency-safe via
    contextvars) so every log emitted while handling the request — including
    library logs — carries it without threading it through each call."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    with bound(request_id=request_id, method=request.method, path=request.url.path):
        response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


# --- Endpoints ---
@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/submit-order")
async def submit_order(
    request: OrderRequest,
    raw_request: Request,
    x_idempotency_key: str = Header(None),
    temporal_service: TemporalService = Depends(get_temporal_service),
    session=Depends(get_db_session),
):
    if not x_idempotency_key:
        raise HTTPException(
            status_code=400, detail="X-Idempotency-Key header is required"
        )

    # 1. Cart version hash check — hash what the client actually sent,
    # not the Pydantic-hydrated dict (defaults would pollute the hash).
    raw_body = await raw_request.json()
    client_hash = raw_body.pop("cart_version", None)
    if client_hash is None:
        raise HTTPException(status_code=400, detail="cart_version is required")

    canonical_body = json.dumps(raw_body, sort_keys=True, separators=(",", ":"))
    server_hash = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()

    if client_hash != server_hash:
        raise HTTPException(
            status_code=409, detail="Cart version mismatch. Please review your cart."
        )

    # 2. Check Idempotency cache
    idem_record = await session.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == x_idempotency_key)
    )
    existing_record = idem_record.scalars().first()

    if existing_record:
        if existing_record.request_hash == server_hash:
            return Response(
                content=existing_record.response_json,
                status_code=existing_record.status_code,
                media_type="application/json",
            )
        else:
            raise HTTPException(
                status_code=422, detail="Idempotency key reused with different payload"
            )

    # 3. Generate IDs
    # order_id is derived deterministically from the client idempotency key, so a
    # retried submission yields the same id. workflow_id mirrors it, so a retried
    # start collides on the same id and USE_EXISTING attaches instead of spawning
    # a duplicate workflow.
    order_id = order_id_from_key(x_idempotency_key)
    workflow_id = order_id

    # 4. Insert DB record
    order = Order(
        id=order_id,
        trace_id=request.trace_id,
        workflow_id=workflow_id,
        item_id=request.item_id,
        quantity=request.quantity,
        user_id=request.user_id,
        address=request.address,
        payment_authorization_id=request.payment_authorization_id,
        payment_last_four=request.payment_authorization_id[-4:].upper()
        if request.payment_authorization_id
        else None,
        amount=request.amount,
        status="pending",
    )
    session.add(order)
    await session.commit()

    # 5. Start Temporal workflow
    workflow_input = OrderWorkflowInput(
        order_id=order_id,
        item_id=request.item_id,
        quantity=request.quantity,
        user_id=request.user_id,
        address=request.address,
        payment_authorization_id=request.payment_authorization_id,
        amount=request.amount,
        trace_id=request.trace_id,
    )

    await temporal_service.start_order_workflow(
        workflow_id=workflow_id,
        order_id=order_id,
        trace_id=request.trace_id,
        order_input=workflow_input,
    )

    log.info(
        "order workflow started",
        order_id=order_id,
        workflow_id=workflow_id,
        trace_id=request.trace_id,
    )

    response_data = {
        "status": "success",
        "order_id": order_id,
        "workflow_id": workflow_id,
        "trace_id": request.trace_id,
    }

    # 6. Save Idempotency record
    new_idem_record = IdempotencyRecord(
        key=x_idempotency_key,
        request_hash=server_hash,
        response_json=json.dumps(response_data),
        status_code=200,
    )
    session.add(new_idem_record)
    await session.commit()

    return response_data


@app.get("/orders/{order_id}")
async def get_order(order_id: str, session=Depends(get_db_session)):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return {
        "id": order.id,
        "status": order.status,
        "amount": float(order.amount),
        "payment_authorization_id": order.payment_authorization_id,
        "payment_last_four": order.payment_last_four,
        "reservation_id": order.reservation_id,
        "tracking_id": order.tracking_id,
        "capture_id": order.capture_id,
        "customer_message": order.customer_message,
        "customer_message_level": order.customer_message_level,
        "store_credit_cents": order.store_credit_cents,
        "address": order.address,
        "failure_reason": order.failure_reason,
        "last_reached_status": order.last_reached_status,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "trace_id": order.trace_id,
    }


# --- Internal / Workflow Endpoints ---


@app.post("/internal/orders/ensure")
async def internal_ensure_order(order_data: dict, session=Depends(get_db_session)):
    """Idempotent order record creation for the workflow."""
    order_id = order_data["order_id"]
    result = await session.execute(select(Order).where(Order.id == order_id))
    existing = result.scalars().first()
    if existing:
        return {"ok": True, "status": "exists"}

    order = Order(
        id=order_id,
        workflow_id=order_data["workflow_id"],
        trace_id=order_data.get("trace_id"),
        item_id=order_data["item_id"],
        quantity=order_data["quantity"],
        user_id=order_data["user_id"],
        address=order_data["address"],
        amount=order_data["amount"],
        status="pending",
    )
    session.add(order)
    await session.commit()
    return {"ok": True, "status": "created"}


@app.patch("/orders/{order_id}/customer-status")
async def update_customer_status(
    order_id: str, payload: CustomerStatusUpdate, session=Depends(get_db_session)
):
    """Updates the customer-facing status and message on the order."""
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = payload.status
    order.customer_message = payload.message
    order.customer_message_level = payload.level
    if payload.store_credit_cents is not None:
        order.store_credit_cents = payload.store_credit_cents

    await session.commit()
    return {"ok": True}


@app.patch("/internal/orders/{order_id}/inventory-reservation")
async def persist_inventory_reservation(
    order_id: str, payload: InventoryReservationUpdate, session=Depends(get_db_session)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.reservation_id = payload.reservation_id
        order.status = "inventory_reserved"
        await session.commit()
    return {"ok": True}


@app.patch("/internal/orders/{order_id}/shipment")
async def persist_shipment(
    order_id: str, payload: ShipmentUpdate, session=Depends(get_db_session)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.tracking_id = payload.tracking_id
        order.status = "shipment_created"
        await session.commit()
    return {"ok": True}


@app.patch("/internal/orders/{order_id}/payment-capture")
async def persist_payment_capture(
    order_id: str, payload: PaymentCaptureUpdate, session=Depends(get_db_session)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.capture_id = payload.capture_id
        order.status = "payment_captured"
        await session.commit()
    return {"ok": True}


@app.post("/orders/{order_id}/fail")
async def mark_order_failed(
    order_id: str, payload: OrderFailRequest, session=Depends(get_db_session)
):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.status = payload.status
        order.failure_reason = payload.failure_reason
        order.customer_message = payload.customer_message
        order.customer_message_level = payload.customer_message_level
        order.last_reached_status = payload.last_reached_status
        if payload.store_credit_cents is not None:
            order.store_credit_cents = payload.store_credit_cents
        await session.commit()
    return {"ok": True}


@app.post("/internal/orders/{order_id}/finalize")
async def finalize_order(order_id: str, session=Depends(get_db_session)):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.status = "completed"
        await session.commit()
    return {"ok": True}


@app.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    temporal_service: TemporalService = Depends(get_temporal_service),
    session=Depends(get_db_session),
):
    """Cancel a running workflow."""
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Only cancel if still in-flight
    terminal_statuses = {s.value for s in OrderStatus.terminal_statuses()}
    if order.status in terminal_statuses:
        return {
            "requested": False,
            "reason": "already_terminal",
            "status": order.status,
        }

    res = await temporal_service.cancel_order(order.workflow_id)
    return {
        "requested": res.get("requested"),
        "reason": res.get("reason"),
        "status": order.status,
    }


@app.post("/orders/cancel-batch")
async def cancel_batch(
    payload: dict,
    temporal_service: TemporalService = Depends(get_temporal_service),
    session=Depends(get_db_session),
):
    """Cancel all in-flight orders in a batch."""
    order_ids = payload.get("order_ids", [])
    requested = 0
    skipped = 0

    if not order_ids:
        return {"requested": 0, "skipped": 0}

    result = await session.execute(select(Order).where(Order.id.in_(order_ids)))
    orders = result.scalars().all()

    terminal_statuses = {s.value for s in OrderStatus.terminal_statuses()}

    for order in orders:
        if order.status in terminal_statuses:
            skipped += 1
            continue
        res = await temporal_service.cancel_order(order.workflow_id)
        if res.get("requested"):
            requested += 1
        else:
            skipped += 1

    return {"requested": requested, "skipped": skipped}


# --- Admin / Demo Reset ---

# App tables truncated on reset. Listed explicitly (not reflected) so a future
# table isn't silently wiped without a deliberate edit here.
_RESET_TABLES = ("orders", "idempotency_keys")


@app.post("/admin/reset")
async def admin_reset(
    delete_closed: bool = True,
    local_only: bool = False,
    temporal_service: TemporalService = Depends(get_temporal_service),
    session=Depends(get_db_session),
):
    """Reset the demo to a clean slate: terminate (and optionally delete) all
    workflows in the namespace, then truncate the app's order tables.

    Destructive and irreversible — gated behind DEMO_RESET_ENABLED. The Temporal
    pass runs first; if it raises (e.g. cluster unreachable) the DB is left
    untouched and the caller gets a 5xx rather than a half-reset.

    `local_only` skips the Temporal pass entirely and truncates only the local
    order tables. This is the safe scope against a managed/shared **Temporal Cloud**
    namespace, where the console must never terminate or delete workflows it doesn't
    own. The console sets it on the cloud backend (see ADR-0015 / the reset modal).
    """
    if not settings.demo_reset_enabled:
        raise HTTPException(
            status_code=403,
            detail="Reset disabled. Set DEMO_RESET_ENABLED=true to enable.",
        )

    # 1. Temporal namespace — terminate open, optionally delete closed. Skipped
    #    entirely on the local-only (Cloud-safe) path.
    workflows = None
    if not local_only:
        workflows = await temporal_service.reset_workflows(delete_closed=delete_closed)

    # 2. App database — wipe orders + idempotency cache. TRUNCATE both in one
    #    statement so there's no window where one is cleared and the other isn't.
    await session.execute(text(f"TRUNCATE TABLE {', '.join(_RESET_TABLES)}"))
    await session.commit()

    return {
        "ok": True,
        "local_only": local_only,
        "workflows": workflows,
        "database": {"truncated": list(_RESET_TABLES)},
    }
