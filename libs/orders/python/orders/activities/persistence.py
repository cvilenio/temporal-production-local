from google.protobuf.json_format import MessageToDict
from temporalio import activity

from orders.activities.contract_gate import gate
from orders.clients.orders_service import OrdersServiceClient
from orders.shared.activity_io import (
    CreateOrderRecordRequest,
    FinalizeOrderRequest,
    MarkOrderFailedRequest,
    PersistInventoryReservationRequest,
    PersistPaymentCaptureRequest,
    PersistShipmentRequest,
)
from orders.shared.temporal_ids import ActivityName


def make_persistence_activities(client: OrdersServiceClient) -> list:
    @activity.defn(name=ActivityName.CREATE_ORDER_RECORD)
    async def create_order_record(req: CreateOrderRecordRequest) -> None:
        gate(req)
        # Hand-build the orders-service contract (major units, no envelope fields)
        # so the HTTP/DB layer stays decoupled from the wire contract's shape.
        await client.ensure_order(
            {
                "order_id": req.order_id,
                "workflow_id": req.workflow_id,
                "trace_id": req.trace_id or None,
                "item_id": req.item_id,
                "quantity": req.quantity,
                "user_id": req.user_id,
                "address": req.address,
                "amount": req.amount_minor / 100,
            }
        )

    @activity.defn(name=ActivityName.PERSIST_INVENTORY_RESERVATION)
    async def persist_inventory_reservation(
        req: PersistInventoryReservationRequest,
    ) -> None:
        gate(req)
        await client.persist_inventory_reservation(req.order_id, req.reservation_id)

    @activity.defn(name=ActivityName.PERSIST_SHIPMENT)
    async def persist_shipment(req: PersistShipmentRequest) -> None:
        gate(req)
        await client.persist_shipment(req.order_id, req.tracking_id)

    @activity.defn(name=ActivityName.PERSIST_PAYMENT_CAPTURE)
    async def persist_payment_capture(req: PersistPaymentCaptureRequest) -> None:
        gate(req)
        await client.persist_payment_capture(req.order_id, req.capture_id)

    @activity.defn(name=ActivityName.MARK_ORDER_FAILED)
    async def mark_order_failed(req: MarkOrderFailedRequest) -> None:
        gate(req)
        # preserving_proto_field_name keeps snake_case keys matching the service's
        # Pydantic model; proto3 omits empty fields, which map to its defaults.
        payload = MessageToDict(req, preserving_proto_field_name=True)
        payload.pop("order_id", None)
        payload.pop("contract_version", None)
        await client.mark_order_failed(req.order_id, payload)

    @activity.defn(name=ActivityName.FINALIZE_ORDER)
    async def finalize_order(req: FinalizeOrderRequest) -> None:
        gate(req)
        await client.finalize_order(req.order_id)

    return [
        create_order_record,
        persist_inventory_reservation,
        persist_shipment,
        persist_payment_capture,
        mark_order_failed,
        finalize_order,
    ]
