from google.protobuf.json_format import MessageToDict
from temporalio import activity

from orders.activities.contract_gate import gate
from orders.clients.orders_service import OrdersServiceClient
from orders.shared.activity_io import UpdateCustomerStatusRequest
from orders.shared.temporal_ids import ActivityName


def make_customer_message_activities(client: OrdersServiceClient) -> list:
    @activity.defn(name=ActivityName.UPDATE_CUSTOMER_STATUS)
    async def update_customer_status(req: UpdateCustomerStatusRequest) -> None:
        """Updates the customer-facing status and message on the order."""
        gate(req)
        payload = MessageToDict(req, preserving_proto_field_name=True)
        payload.pop("order_id", None)
        payload.pop("contract_version", None)
        await client.update_customer_status(req.order_id, payload)

    return [update_customer_status]
