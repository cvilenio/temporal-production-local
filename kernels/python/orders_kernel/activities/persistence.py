from temporalio import activity

from orders_kernel.clients.orders_service import OrdersServiceClient
from orders_kernel.shared.activity_io import (
    CreateOrderRecordRequest,
    FinalizeOrderRequest,
    MarkOrderFailedRequest,
    PersistInventoryReservationRequest,
    PersistPaymentCaptureRequest,
    PersistShipmentRequest,
)
from orders_kernel.shared.temporal_ids import ActivityName


def make_persistence_activities(client: OrdersServiceClient) -> list:
    @activity.defn(name=ActivityName.CREATE_ORDER_RECORD)
    async def create_order_record(req: CreateOrderRecordRequest) -> None:
        await client.ensure_order(req.model_dump(mode="json"))

    @activity.defn(name=ActivityName.PERSIST_INVENTORY_RESERVATION)
    async def persist_inventory_reservation(
        req: PersistInventoryReservationRequest,
    ) -> None:
        await client.persist_inventory_reservation(req.order_id, req.reservation_id)

    @activity.defn(name=ActivityName.PERSIST_SHIPMENT)
    async def persist_shipment(req: PersistShipmentRequest) -> None:
        await client.persist_shipment(req.order_id, req.tracking_id)

    @activity.defn(name=ActivityName.PERSIST_PAYMENT_CAPTURE)
    async def persist_payment_capture(req: PersistPaymentCaptureRequest) -> None:
        await client.persist_payment_capture(req.order_id, req.capture_id)

    @activity.defn(name=ActivityName.MARK_ORDER_FAILED)
    async def mark_order_failed(req: MarkOrderFailedRequest) -> None:
        payload = req.model_dump(mode="json")
        order_id = payload.pop("order_id")
        await client.mark_order_failed(order_id, payload)

    @activity.defn(name=ActivityName.FINALIZE_ORDER)
    async def finalize_order(req: FinalizeOrderRequest) -> None:
        await client.finalize_order(req.order_id)

    return [
        create_order_record,
        persist_inventory_reservation,
        persist_shipment,
        persist_payment_capture,
        mark_order_failed,
        finalize_order,
    ]
