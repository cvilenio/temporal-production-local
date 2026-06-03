from temporalio import activity
from temporalio.exceptions import ApplicationError
from clients.mock_api import MockApiClient
from shared.errors import ErrorType
from shared.temporal_ids import ActivityName
from shared.activity_io import (
    ReserveInventoryRequest,
    CreateShipmentRequest,
    ShipmentCreatedResult,
    CapturePaymentRequest,
    VerifyShipmentRequest,
    ReleaseInventoryRequest,
    CancelShipmentRequest,
    RefundPaymentRequest,
)

def make_external_activities(mock_api: MockApiClient) -> list:
    @activity.defn(name=ActivityName.RESERVE_INVENTORY)
    async def reserve_inventory(req: ReserveInventoryRequest) -> None:
        """Reserves inventory."""
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
        result = await mock_api.charge_payment(
            token=req.auth_token,
            amount=req.amount,
            idem_key=req.idem_key,
        )
        if not result["success"]:
            raise ApplicationError(
                f"Payment capture failed: {result['reason']}",
                type=ErrorType.UNRECOGNIZED_ACTIVITY_FAILURE,
            )

    @activity.defn(name=ActivityName.VERIFY_SHIPMENT_STATUS)
    async def verify_shipment_status(req: VerifyShipmentRequest) -> ShipmentCreatedResult:
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
