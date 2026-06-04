from __future__ import annotations

from typing import TYPE_CHECKING

from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.contrib.pydantic import pydantic_data_converter

from workflows.order_workflow import OrderWorkflow
from shared.temporal_ids import TaskQueue, SignalName, SearchAttribute
from shared.workflow_io import OrderWorkflowInput

if TYPE_CHECKING:
    from temporalio.runtime import Runtime


class TemporalService:
    def __init__(
        self,
        temporal_address: str,
        temporal_namespace: str,
        runtime: Runtime | None = None,
        interceptors: list | None = None,
    ):
        self.temporal_address = temporal_address
        self.temporal_namespace = temporal_namespace
        self.runtime = runtime
        self.interceptors = interceptors or []
        self.client: Client | None = None

    async def connect(self):
        # TracingInterceptor propagates OTel span context across the
        # client → workflow → activity boundary via Temporal headers.
        # pydantic_data_converter handles typed payload serialisation.
        # Both are independent: the interceptor uses headers, not payloads.
        self.client = await Client.connect(
            self.temporal_address,
            namespace=self.temporal_namespace,
            data_converter=pydantic_data_converter,
            interceptors=self.interceptors,
            runtime=self.runtime,
        )
        return self.client

    async def start_order_workflow(
        self,
        workflow_id: str,
        order_id: str,
        trace_id: str | None,
        order_input: OrderWorkflowInput,
    ):
        if not self.client:
            raise RuntimeError("Temporal client not connected")

        search_attributes = {
            SearchAttribute.ORDER_ID: [order_id],
            SearchAttribute.ORDER_STATUS: ["pending"],
        }
        if trace_id:
            search_attributes[SearchAttribute.TRACE_ID] = [trace_id]

        handle = await self.client.start_workflow(
            OrderWorkflow.run,
            order_input,
            id=workflow_id,
            task_queue=TaskQueue.ORDERS_WORKFLOW,
            search_attributes=search_attributes,
        )
        return handle.id

    async def cancel_order(self, workflow_id: str) -> dict:
        if not self.client:
            raise RuntimeError("Temporal client not connected")
        try:
            handle = self.client.get_workflow_handle(workflow_id)
            await handle.signal(SignalName.CANCEL_ORDER)
            return {"requested": True}
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                return {"requested": False, "reason": "workflow_not_found"}
            return {"requested": False, "reason": str(e)}
        except Exception as e:
            return {"requested": False, "reason": str(e)}
