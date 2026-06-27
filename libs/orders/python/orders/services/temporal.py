from __future__ import annotations

import asyncio
import logging

from orders.shared.temporal_ids import SearchAttribute, SignalName, TaskQueue
from orders.shared.workflow_io import (
    ORDER_WORKFLOW_EXECUTION_TIMEOUT,
    OrderWorkflowInput,
)
from orders.workflows.order_workflow import OrderWorkflow
from temporalio.api.common.v1 import WorkflowExecution as WorkflowExecutionMsg
from temporalio.api.workflowservice.v1 import DeleteWorkflowExecutionRequest
from temporalio.client import Client
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)
from temporalio.service import RPCError, RPCStatusCode

logger = logging.getLogger(__name__)

# Bound concurrent terminate/delete RPCs during a reset so a large namespace
# doesn't flood the frontend with simultaneous calls.
_RESET_CONCURRENCY = 20


class TemporalService:
    """Domain operations over a connected Temporal client (ADR-0022).

    Lifecycle-agnostic: it receives an already-connected `Client` (built by the
    app's composition root via `appkit.build_temporal_client`, which bakes in the
    data-converter contract) and owns none of the connection policy — just the
    orders-domain start / cancel / reset behaviour.
    """

    def __init__(self, client: Client):
        self.client = client

    async def start_order_workflow(
        self,
        workflow_id: str,
        order_id: str,
        trace_id: str | None,
        order_input: OrderWorkflowInput,
    ):
        pairs = [
            SearchAttributePair(SearchAttribute.ORDER_ID, order_id),
            SearchAttributePair(SearchAttribute.ORDER_STATUS, "pending"),
            # Pre-set the contract-version list the workflow will also upsert, so
            # the attribute is queryable from the first task (ADR-0021).
            SearchAttributePair(SearchAttribute.CONTRACT_VERSIONS, ["1"]),
        ]
        if trace_id:
            pairs.append(SearchAttributePair(SearchAttribute.TRACE_ID, trace_id))

        # USE_EXISTING makes a retried start idempotent: if a workflow with this
        # id is already running (e.g. the caller retried after the DB commit but
        # before the idempotency record landed), attach to it instead of starting
        # a duplicate. Relies on workflow_id being derived from the idempotency key.
        handle = await self.client.start_workflow(
            OrderWorkflow.run,
            order_input,
            id=workflow_id,
            task_queue=TaskQueue.ORDERS_WORKFLOW,
            execution_timeout=ORDER_WORKFLOW_EXECUTION_TIMEOUT,
            search_attributes=TypedSearchAttributes(pairs),
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return handle.id

    async def reset_workflows(
        self,
        *,
        delete_closed: bool = True,
        terminate_reason: str = "demo reset",
    ) -> dict:
        """Reset the namespace to a clean slate for a fresh demo run.

        Two best-effort passes, each with bounded concurrency:
          1. Terminate every still-open workflow (hard stop — does not wait for
             a graceful cancel signal to drain).
          2. Optionally delete every workflow from visibility + history so the
             Temporal UI list starts empty.

        Per-workflow failures are tolerated and counted rather than raised, so a
        single bad execution can't abort the whole reset. Note that visibility is
        eventually consistent: workflows terminated in pass 1 may not yet show as
        closed when pass 2 lists them, so a few may survive deletion until the
        next reset. Counts in the return value reflect what actually happened.
        """
        client = self.client
        sem = asyncio.Semaphore(_RESET_CONCURRENCY)

        async def _terminate(wf_id: str, run_id: str) -> str:
            async with sem:
                try:
                    handle = client.get_workflow_handle(wf_id, run_id=run_id)
                    await handle.terminate(terminate_reason)
                    return "ok"
                except RPCError as e:
                    if e.status == RPCStatusCode.NOT_FOUND:
                        return "skip"  # already closed/gone between list and call
                    logger.warning("Reset: terminate %s failed: %s", wf_id, e)
                    return "err"
                except Exception as e:
                    logger.warning("Reset: terminate %s failed: %s", wf_id, e)
                    return "err"

        term_tasks = [
            _terminate(wf.id, wf.run_id)
            async for wf in client.list_workflows(query='ExecutionStatus="Running"')
        ]
        term_results = await asyncio.gather(*term_tasks)
        terminated = term_results.count("ok")
        terminate_errors = term_results.count("err")

        deleted = 0
        delete_errors = 0
        if delete_closed:

            async def _delete(wf_id: str, run_id: str) -> str:
                async with sem:
                    try:
                        await client.workflow_service.delete_workflow_execution(
                            DeleteWorkflowExecutionRequest(
                                namespace=client.namespace,
                                workflow_execution=WorkflowExecutionMsg(
                                    workflow_id=wf_id, run_id=run_id
                                ),
                            )
                        )
                        return "ok"
                    except Exception as e:
                        logger.warning("Reset: delete %s failed: %s", wf_id, e)
                        return "err"

            del_tasks = [
                _delete(wf.id, wf.run_id) async for wf in client.list_workflows()
            ]
            del_results = await asyncio.gather(*del_tasks)
            deleted = del_results.count("ok")
            delete_errors = del_results.count("err")

        return {
            "terminated": terminated,
            "terminate_errors": terminate_errors,
            "deleted": deleted,
            "delete_errors": delete_errors,
            "delete_closed": delete_closed,
        }

    async def cancel_order(self, workflow_id: str) -> dict:
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
