import datetime
import time

from temporalio import activity
from temporalio.exceptions import ApplicationError

from orders.clients.mock_api import MockApiClient
from orders.shared.activity_io import (
    CancelShipmentRequest,
    CapturePaymentRequest,
    CreateShipmentRequest,
    RefundPaymentRequest,
    ReleaseInventoryRequest,
    ReserveInventoryRequest,
    ShipmentCreatedResult,
    VerifyShipmentRequest,
)
from orders.shared.errors import ErrorType
from orders.shared.metrics import business_meter
from orders.shared.temporal_ids import ActivityName


def make_external_activities(mock_api: MockApiClient) -> list:
    # ── Business metrics (OTLP push pipeline) ───────────────────────────────
    # These record application-level facts that matter to the business.
    # business_meter() returns a global OTel Meter; it's safe to call here
    # because activities run outside the workflow sandbox.
    _meter = business_meter()
    # Counter name has no _total suffix — the OTel Collector's Prometheus
    # exporter appends it, yielding `orders_payments_captured_total`.
    _payments_captured = _meter.create_counter(
        "orders.payments_captured",
        description="Number of payment captures that succeeded",
    )
    _payment_amount = _meter.create_histogram(
        "orders.payment_amount",
        description="Amount charged per captured payment",
        unit="usd_cents",
    )

    @activity.defn(name=ActivityName.RESERVE_INVENTORY)
    async def reserve_inventory(req: ReserveInventoryRequest) -> None:
        activity.logger.info(
            "calling inventory API",
            extra={"item_id": req.item_id, "quantity": req.quantity},
        )
        result = await mock_api.reserve_inventory(
            item_id=req.item_id,
            quantity=req.quantity,
            idem_key=req.idem_key,
        )
        if not result["success"]:
            raise ApplicationError(
                f"Inventory reservation failed: {result['reason']}",
                type=ErrorType.UNRECOGNIZED_ACTIVITY_FAILURE,
            )

    @activity.defn(name=ActivityName.CREATE_SHIPMENT)
    async def create_shipment(req: CreateShipmentRequest) -> ShipmentCreatedResult:
        tracking_id = await mock_api.create_shipment(
            address=req.address,
            order_id=req.order_id,
            idem_key=req.idem_key,
        )
        return ShipmentCreatedResult(tracking_id=tracking_id)

    @activity.defn(name=ActivityName.CAPTURE_PAYMENT)
    async def capture_payment(req: CapturePaymentRequest) -> None:
        activity.logger.info(
            "capturing payment",
            extra={"amount": float(req.amount)},
        )
        t0 = time.monotonic()
        result = await mock_api.charge_payment(
            token=req.auth_token,
            amount=req.amount,
            idem_key=req.idem_key,
        )
        elapsed = datetime.timedelta(seconds=time.monotonic() - t0)

        if not result["success"]:
            raise ApplicationError(
                f"Payment capture failed: {result['reason']}",
                type=ErrorType.UNRECOGNIZED_ACTIVITY_FAILURE,
            )

        # Operational metric (SDK pull pipeline) — activity.metric_meter() MUST
        # be called inside the activity body, not at factory/setup time.
        activity.metric_meter().create_histogram_timedelta(
            "orders_payment_capture_duration",
            description="Wall-clock time for the payment capture API call",
            unit="duration",
        ).record(elapsed)

        # Business metrics (OTLP push pipeline via business_meter) — safe in
        # activities since they run outside the workflow sandbox.
        _payments_captured.add(1)
        _payment_amount.record(int(req.amount * 100))
        activity.logger.info("payment captured", extra={"amount": float(req.amount)})

    @activity.defn(name=ActivityName.VERIFY_SHIPMENT_STATUS)
    async def verify_shipment_status(
        req: VerifyShipmentRequest,
    ) -> ShipmentCreatedResult:
        result = await mock_api.verify_shipment_status(
            idem_key=req.idem_key,
        )
        if not result["confirmed"]:
            raise ApplicationError(
                "Shipment not verified after write",
                type=ErrorType.SHIPMENT_NOT_VERIFIED,
                non_retryable=True,
            )
        return ShipmentCreatedResult(tracking_id=result["tracking_id"])

    @activity.defn(name=ActivityName.RELEASE_INVENTORY)
    async def release_inventory(req: ReleaseInventoryRequest) -> None:
        await mock_api.release_inventory(
            reservation_id=req.reservation_id,
            item_id=req.item_id,
            quantity=req.quantity,
            idem_key=req.idem_key,
        )

    @activity.defn(name=ActivityName.CANCEL_SHIPMENT)
    async def cancel_shipment(req: CancelShipmentRequest) -> None:
        await mock_api.cancel_shipment(
            tracking_id=req.tracking_id,
            idem_key=req.idem_key,
        )

    @activity.defn(name=ActivityName.REFUND_PAYMENT)
    async def refund_payment(req: RefundPaymentRequest) -> None:
        await mock_api.refund_payment(
            capture_id=req.capture_id,
            amount=req.amount,
            idem_key=req.idem_key,
        )

    return [
        reserve_inventory,
        create_shipment,
        capture_payment,
        verify_shipment_status,
        release_inventory,
        cancel_shipment,
        refund_payment,
    ]
