"""Internal persistence routes — the workflow / activity callback surface.

All paths share the `/internal/orders` base (enforced by the router prefix). The orders
workflow's activities call these to idempotently ensure the order record exists and to
record each state transition (customer status, reservation, shipment, payment capture,
failure, finalize). Not part of the customer-facing API.
"""

from dependencies import get_db_session
from fastapi import APIRouter, Depends, HTTPException
from orders.db.models import Order
from orders.shared.models import (
    CustomerStatusUpdate,
    InventoryReservationUpdate,
    OrderFailRequest,
    PaymentCaptureUpdate,
    ShipmentUpdate,
)
from sqlalchemy import select

router = APIRouter(prefix="/internal/orders")


@router.post("/ensure")
async def ensure_order(order_data: dict, session=Depends(get_db_session)):
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


@router.patch("/{order_id}/customer-status")
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


@router.patch("/{order_id}/inventory-reservation")
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


@router.patch("/{order_id}/shipment")
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


@router.patch("/{order_id}/payment-capture")
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


@router.post("/{order_id}/fail")
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


@router.post("/{order_id}/finalize")
async def finalize_order(order_id: str, session=Depends(get_db_session)):
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.status = "completed"
        await session.commit()
    return {"ok": True}
