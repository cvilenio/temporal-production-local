from temporalio import activity

from orders.clients.orders_service import OrdersServiceClient
from orders.shared.activity_io import UpdateCustomerStatusRequest
from orders.shared.temporal_ids import ActivityName


def make_customer_message_activities(client: OrdersServiceClient) -> list:
    @activity.defn(name=ActivityName.UPDATE_CUSTOMER_STATUS)
    async def update_customer_status(req: UpdateCustomerStatusRequest) -> None:
        """Updates the customer-facing status and message on the order."""
        payload = req.model_dump(mode="json")
        order_id = payload.pop("order_id")
        await client.update_customer_status(order_id, payload)

    return [update_customer_status]
