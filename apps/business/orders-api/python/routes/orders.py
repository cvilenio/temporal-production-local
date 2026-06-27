"""Public order routes — the customer / console surface.

Submit an order (idempotent, cart-version-checked), read it, and cancel it
(single or batch). Workflow/activity callbacks live in routes/internal.py; the
destructive demo reset lives in routes/admin.py.
"""

import hashlib
import json

from composition import get_db_session, get_temporal_service
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from obslog import get_logger
from orders.db.models import IdempotencyRecord, Order
from orders.services.temporal import TemporalService
from orders.shared.ids import order_id_from_key
from orders.shared.models import OrderRequest, OrderStatus
from orders.shared.workflow_io import OrderWorkflowInput
from sqlalchemy import select

log = get_logger(__name__)

router = APIRouter()


@router.post("/submit-order")
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
    # Convert the client-facing dollar amount to minor units (cents) at the edge;
    # the Temporal contract carries integer minor units (ADR-0021).
    workflow_input = OrderWorkflowInput(
        order_id=order_id,
        item_id=request.item_id,
        quantity=request.quantity,
        user_id=request.user_id,
        address=request.address,
        payment_authorization_id=request.payment_authorization_id,
        amount_minor=round(request.amount * 100),
        trace_id=request.trace_id or "",
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


@router.get("/orders/{order_id}")
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


@router.post("/orders/{order_id}/cancel")
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


@router.post("/orders/cancel-batch")
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
