from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

from shared.errors import ErrorType
from shared.models import OrderStatus
from shared.temporal_ids import TaskQueue, ActivityName, SignalName, SearchAttribute
from shared.workflow_io import OrderWorkflowInput, OrderWorkflowResult
from shared.activity_io import (
    CreateOrderRecordRequest,
    ReserveInventoryRequest,
    PersistInventoryReservationRequest,
    CreateShipmentRequest,
    ShipmentCreatedResult,
    VerifyShipmentRequest,
    PersistShipmentRequest,
    CapturePaymentRequest,
    PersistPaymentCaptureRequest,
    FinalizeOrderRequest,
    UpdateCustomerStatusRequest,
    MarkOrderFailedRequest,
    ReleaseInventoryRequest,
    CancelShipmentRequest,
    RefundPaymentRequest,
)

from temporalio.contrib.opentelemetry.workflow import completed_span as otel_span

from workflows._helpers import retry_policies as retry
from workflows._helpers.errors import unwrap_activity_error
from workflows.order.context import OrderRunContext
from workflows.order.exceptions import OrderCancelled
from workflows.order.terminal import TERMINAL_CONFIG, TerminalReason
from workflows.order import retry_policies as order_retry


@workflow.defn
class OrderWorkflow:
    def __init__(self) -> None:
        self._status = OrderStatus.PENDING
        self._cancel_requested = False
        self._cancelled_from_status: OrderStatus | None = None
        self._compensations: list[tuple[ActivityName, Any]] = []
        self._ctx: OrderRunContext | None = None

        # Custom workflow metrics — ride the Temporal SDK pull pipeline so they
        # are replay-safe (suppressed during history replay automatically).
        # Tag with bounded, low-cardinality labels only (not order_id/trace_id).
        _meter = workflow.metric_meter()
        self._step_counter = _meter.create_counter(
            "order_workflow_steps_completed",
            description="Steps completed within the order workflow",
        )
        self._compensation_counter = _meter.create_counter(
            "order_workflow_compensations_run",
            description="Saga compensation activities executed",
        )

    # ----- signals -----

    @workflow.signal(name=SignalName.CANCEL_ORDER)
    def cancel_order(self) -> None:
        extra = self._log_ctx(self._ctx) if self._ctx is not None else {}
        workflow.logger.warning("cancellation signal received", extra=extra)
        self._cancel_requested = True

    # ----- entrypoint -----

    @workflow.run
    async def run(self, order_input: OrderWorkflowInput) -> OrderWorkflowResult:
        ctx = OrderRunContext(order_input, workflow.info().workflow_id)
        self._ctx = ctx

        # Defensive trace ID propagation — client also sets this, but the workflow
        # sets it too so it is correct even if started without a trace-aware client.
        if ctx.trace_id:
            workflow.upsert_search_attributes({SearchAttribute.TRACE_ID: [ctx.trace_id]})

        workflow.logger.info("order workflow started", extra=self._log_ctx(ctx))

        try:
            await self._step_create_order_record(ctx)

            self._raise_if_cancelled()

            await self._step_reserve_inventory(ctx)

            self._raise_if_cancelled()

            tracking_id = await self._step_create_shipment(ctx)

            self._raise_if_cancelled()

            await self._step_capture_payment(ctx)

            self._raise_if_cancelled()

            await self._step_finalize(ctx, tracking_id)

            workflow.logger.info("order workflow completed", extra=self._log_ctx(ctx))
            return self._make_result(ctx, "Success", tracking_id=tracking_id)

        except OrderCancelled:
            return await self._finalize_terminal(ctx, TerminalReason.CANCELLED_BY_USER)

        except (ActivityError, ApplicationError) as e:
            cause = unwrap_activity_error(e)

            # Unrecoverable shipping failure — compensate and surface clean terminal state
            if isinstance(cause, ApplicationError) and cause.type == ErrorType.SHIPMENT_NOT_VERIFIED:
                return await self._finalize_terminal(ctx, TerminalReason.SHIPPING_UNRECOVERABLE)

            # Unexpected activity or application failure — compensate and re-raise for Temporal retry
            workflow.logger.error("unexpected workflow failure", exc_info=True, extra=self._log_ctx(ctx))
            failed_comps = await self._run_compensations(ctx)
            await self._record_terminal_state(
                ctx,
                status=OrderStatus.CANCELLED_WITH_ISSUES if failed_comps else OrderStatus.FAILED,
                message="Something went wrong with your order. Our team is investigating.",
                failure_reason=str(e),
            )
            raise ApplicationError(
                f"Order workflow failed: {e}",
                type=ErrorType.UNEXPECTED_ORDER_FAILURE,
            ) from e

    # ----- step: create order record -----

    async def _step_create_order_record(self, ctx: OrderRunContext) -> None:
        workflow.logger.info("step starting", extra=self._log_ctx(ctx, step="create_order_record"))

        await workflow.execute_activity(
            ActivityName.CREATE_ORDER_RECORD,
            CreateOrderRecordRequest(
                order_id=ctx.order_id,
                workflow_id=ctx.workflow_id,
                item_id=ctx.item_id,
                quantity=ctx.quantity,
                user_id=ctx.user_id,
                address=ctx.address,
                payment_authorization_id=ctx.payment_authorization_id,
                amount=ctx.amount,
                trace_id=ctx.trace_id,
            ),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.PERSISTENCE,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        await self._notify(ctx, OrderStatus.PENDING, "Order received, getting ready to process.")
        # otel_span creates a sandbox-safe OTel span via TracingInterceptor's context.
        # Import is from temporalio.contrib (fully pass-through in the sandbox).
        otel_span("order.create_order_record")
        self._step_counter.add(1, {"step": "create_order_record"})
        workflow.logger.info("step completed", extra=self._log_ctx(ctx, step="create_order_record"))

    # ----- step: reserve inventory -----

    async def _step_reserve_inventory(self, ctx: OrderRunContext) -> None:
        workflow.logger.info("step starting", extra=self._log_ctx(ctx, step="reserve_inventory"))
        self._set_status(OrderStatus.RESERVING_INVENTORY)

        await workflow.execute_activity(
            ActivityName.RESERVE_INVENTORY,
            ReserveInventoryRequest(
                item_id=ctx.item_id,
                quantity=ctx.quantity,
                idem_key=ctx.idem_key("reserve_inventory"),
            ),
            start_to_close_timeout=timedelta(seconds=12),
            retry_policy=retry.EXTERNAL_CALL,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        # Register compensation before persisting — ensures rollback is possible
        # even if the persistence step fails.
        reservation_id = ctx.generate_reservation_id()
        self._compensations.append((
            ActivityName.RELEASE_INVENTORY,
            ReleaseInventoryRequest(
                reservation_id=reservation_id,
                item_id=ctx.item_id,
                quantity=ctx.quantity,
                idem_key=ctx.idem_key("release_inventory"),
            ),
        ))

        await workflow.execute_activity(
            ActivityName.PERSIST_INVENTORY_RESERVATION,
            PersistInventoryReservationRequest(
                order_id=ctx.order_id,
                reservation_id=reservation_id,
            ),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.PERSISTENCE,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        self._set_status(OrderStatus.INVENTORY_RESERVED)
        await self._notify(ctx, OrderStatus.INVENTORY_RESERVED, "Items reserved in our warehouse.")
        otel_span("order.reserve_inventory")
        self._step_counter.add(1, {"step": "reserve_inventory"})
        workflow.logger.info("step completed", extra=self._log_ctx(ctx, step="reserve_inventory"))

    # ----- step: create shipment -----

    async def _step_create_shipment(self, ctx: OrderRunContext) -> str:
        workflow.logger.info("step starting", extra=self._log_ctx(ctx, step="create_shipment"))
        self._set_status(OrderStatus.CREATING_SHIPMENT)

        shipping_idem_key = ctx.idem_key("create_shipment")
        tracking_id: str | None = None

        # Two-cycle create-then-verify loop: if create times out or fails, we verify
        # whether the shipment actually landed before retrying. This prevents duplicate
        # shipments while still recovering from transient courier API failures.
        for cycle in [1, 2]:
            try:
                result = await workflow.execute_activity(
                    ActivityName.CREATE_SHIPMENT,
                    CreateShipmentRequest(
                        address=ctx.address,
                        order_id=ctx.order_id,
                        idem_key=shipping_idem_key,
                    ),
                    result_type=ShipmentCreatedResult,
                    start_to_close_timeout=timedelta(seconds=5),
                    retry_policy=order_retry.SHIPPING,
                    task_queue=TaskQueue.ORDERS_ACTIVITY,
                )
                tracking_id = result.tracking_id
                break

            except ActivityError:
                workflow.logger.warning(
                    "create_shipment failed; verifying status",
                    extra={**self._log_ctx(ctx, step="create_shipment"), "cycle": cycle},
                )
                try:
                    result = await workflow.execute_activity(
                        ActivityName.VERIFY_SHIPMENT_STATUS,
                        VerifyShipmentRequest(idem_key=shipping_idem_key),
                        result_type=ShipmentCreatedResult,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=order_retry.VERIFY_SHIPMENT,
                        task_queue=TaskQueue.ORDERS_ACTIVITY,
                    )
                    tracking_id = result.tracking_id
                    break

                except ActivityError as ve:
                    cause = unwrap_activity_error(ve)
                    if isinstance(cause, ApplicationError) and cause.type == ErrorType.SHIPMENT_NOT_VERIFIED:
                        if cycle == 1:
                            continue  # Cycle 1: retry create on verify failure
                        else:
                            raise   # Cycle 2: surface terminal.
                    else:
                        raise 

        # Register compensation only after we have a confirmed tracking ID
        self._compensations.append((
            ActivityName.CANCEL_SHIPMENT,
            CancelShipmentRequest(
                tracking_id=tracking_id,
                idem_key=ctx.idem_key("cancel_shipment"),
            ),
        ))

        await workflow.execute_activity(
            ActivityName.PERSIST_SHIPMENT,
            PersistShipmentRequest(order_id=ctx.order_id, tracking_id=tracking_id),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.PERSISTENCE,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        self._set_status(OrderStatus.SHIPMENT_CREATED)
        await self._notify(ctx, OrderStatus.SHIPMENT_CREATED, f"Shipment created. Tracking: {tracking_id}")
        otel_span("order.create_shipment")
        self._step_counter.add(1, {"step": "create_shipment"})
        workflow.logger.info("step completed", extra=self._log_ctx(ctx, step="create_shipment"))
        return tracking_id

    # ----- step: capture payment -----

    async def _step_capture_payment(self, ctx: OrderRunContext) -> None:
        workflow.logger.info("step starting", extra=self._log_ctx(ctx, step="capture_payment"))
        self._set_status(OrderStatus.CAPTURING_PAYMENT)

        await workflow.execute_activity(
            ActivityName.CAPTURE_PAYMENT,
            CapturePaymentRequest(
                auth_token=ctx.payment_authorization_id,
                amount=ctx.amount,
                idem_key=ctx.idem_key("payment_capture"),
            ),
            start_to_close_timeout=timedelta(seconds=12),
            retry_policy=retry.EXTERNAL_CALL,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        capture_id = f"CAP-{workflow.uuid4()}"
        self._compensations.append((
            ActivityName.REFUND_PAYMENT,
            RefundPaymentRequest(
                capture_id=capture_id,
                amount=ctx.amount,
                idem_key=ctx.idem_key("refund_payment"),
            ),
        ))

        await workflow.execute_activity(
            ActivityName.PERSIST_PAYMENT_CAPTURE,
            PersistPaymentCaptureRequest(order_id=ctx.order_id, capture_id=capture_id),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.PERSISTENCE,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        self._set_status(OrderStatus.PAYMENT_CAPTURED)
        await self._notify(ctx, OrderStatus.PAYMENT_CAPTURED, "Payment captured successfully.")
        otel_span("order.capture_payment")
        self._step_counter.add(1, {"step": "capture_payment"})
        workflow.logger.info("step completed", extra=self._log_ctx(ctx, step="capture_payment"))

    # ----- step: finalize -----

    async def _step_finalize(self, ctx: OrderRunContext, tracking_id: str) -> None:
        workflow.logger.info("step starting", extra=self._log_ctx(ctx, step="finalize"))
        self._set_status(OrderStatus.FINALIZING)

        await workflow.execute_activity(
            ActivityName.FINALIZE_ORDER,
            FinalizeOrderRequest(order_id=ctx.order_id),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.PERSISTENCE,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

        self._set_status(OrderStatus.COMPLETED)
        await self._notify(
            ctx,
            OrderStatus.COMPLETED,
            f"Your order is finalized. Your tracking number is {tracking_id}.",
            level="success",
        )
        otel_span("order.finalize")
        self._step_counter.add(1, {"step": "finalize"})
        workflow.logger.info("step completed", extra=self._log_ctx(ctx, step="finalize"))

    # ----- internal helpers -----

    def _log_ctx(self, ctx: OrderRunContext, step: str | None = None) -> dict:
        """Build a structured logging extra dict for every log call."""
        data: dict[str, Any] = {
            "order_id": ctx.order_id,
            "workflow_id": ctx.workflow_id,
            "trace_id": ctx.trace_id,
        }
        if step is not None:
            data["step"] = step
        return data

    def _make_result(
        self,
        ctx: OrderRunContext,
        status: str,
        tracking_id: str | None = None,
    ) -> OrderWorkflowResult:
        """Build the typed workflow result."""
        return OrderWorkflowResult(
            status=status,
            order_id=ctx.order_id,
            tracking_id=tracking_id,
            trace_id=ctx.trace_id,
        )

    def _set_status(self, status: OrderStatus) -> None:
        self._status = status
        workflow.upsert_search_attributes({SearchAttribute.ORDER_STATUS: [status.value]})

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            self._cancelled_from_status = self._status
            raise OrderCancelled()

    async def _notify(
        self, ctx: OrderRunContext, status: OrderStatus, message: str, level: str = "info"
    ) -> None:
        await workflow.execute_activity(
            ActivityName.UPDATE_CUSTOMER_STATUS,
            UpdateCustomerStatusRequest(
                order_id=ctx.order_id,
                status=status,
                message=message,
                level=level,
            ),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.NOTIFY,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )

    async def _record_terminal_state(
        self,
        ctx: OrderRunContext,
        status: OrderStatus,
        message: str,
        failure_reason: str | None = None,
        level: str = "error",
        last_reached_status: OrderStatus | None = None,
    ) -> None:
        await workflow.execute_activity(
            ActivityName.MARK_ORDER_FAILED,
            MarkOrderFailedRequest(
                order_id=ctx.order_id,
                status=status,
                failure_reason=failure_reason,
                customer_message=message,
                customer_message_level=level,
                last_reached_status=last_reached_status or self._status,
            ),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=retry.PERSISTENCE,
            task_queue=TaskQueue.ORDERS_ACTIVITY,
        )
        self._set_status(status)

    async def _run_compensations(self, ctx: OrderRunContext) -> list[str]:
        """Run registered compensations in reverse order. Returns names of any that failed."""
        failed: list[str] = []
        # Snapshot before clearing so signals cannot append during iteration
        comps = list(self._compensations)
        self._compensations.clear()

        for activity_name, req in reversed(comps):
            self._compensation_counter.add(1, {"activity": str(activity_name)})
            try:
                await workflow.execute_activity(
                    activity_name,
                    req,
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=retry.COMPENSATION,
                    task_queue=TaskQueue.ORDERS_ACTIVITY,
                )
            except ActivityError as e:
                workflow.logger.error(
                    "compensation failed",
                    exc_info=True,
                    extra={**self._log_ctx(ctx), "compensation": str(activity_name)},
                )
                failed.append(str(activity_name))
                await self._notify(
                    ctx,
                    self._status,
                    f"Compensation {activity_name} failed: {e}. Operator follow-up required.",
                    level="error",
                )

        return failed

    async def _finalize_terminal(
        self, ctx: OrderRunContext, reason: TerminalReason
    ) -> OrderWorkflowResult:
        # Check before running compensations — _run_compensations clears the list
        payment_was_captured = any(
            name == ActivityName.REFUND_PAYMENT for name, _ in self._compensations
        )

        failed_comps = await self._run_compensations(ctx)
        config = TERMINAL_CONFIG[reason]

        # Refine cancellation message based on whether payment was already taken
        message = config.message
        if reason == TerminalReason.CANCELLED_BY_USER and payment_was_captured:
            message = "Your order has been cancelled. Any charges will be refunded."

        status = OrderStatus.CANCELLED_WITH_ISSUES if failed_comps else config.clean_status

        if failed_comps:
            workflow.logger.error(
                "terminal finalization with failed compensations",
                extra={**self._log_ctx(ctx), "reason": reason, "failed_compensations": failed_comps},
            )
        else:
            workflow.logger.warning(
                "terminal finalization",
                extra={**self._log_ctx(ctx), "reason": reason},
            )

        await self._record_terminal_state(
            ctx,
            status=status,
            message=message,
            failure_reason=f"Reason: {reason}; Failed compensations: {failed_comps}"
            if failed_comps
            else f"Reason: {reason}",
            level=config.level if not failed_comps else "error",
            last_reached_status=self._cancelled_from_status or self._status,
        )

        if failed_comps:
            raise ApplicationError(
                f"{reason} with failed compensations: {failed_comps}",
                type=ErrorType.COMPENSATION_FAILED,
                non_retryable=True,
            )

        return self._make_result(ctx, config.return_string)
