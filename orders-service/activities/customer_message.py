from clients.orders_service import OrdersServiceClient
from shared.activity_io import UpdateCustomerStatusRequest
from shared.temporal_ids import ActivityName
from temporalio import activity


def make_customer_message_activities(client: OrdersServiceClient) -> list:
    @activity.defn(name=ActivityName.UPDATE_CUSTOMER_STATUS)
    async def update_customer_status(req: UpdateCustomerStatusRequest) -> None:
        """Updates the customer-facing status and message on the order."""
        payload = req.model_dump(mode="json")
        order_id = payload.pop("order_id")
        await client.update_customer_status(order_id, payload)

    return [update_customer_status]
