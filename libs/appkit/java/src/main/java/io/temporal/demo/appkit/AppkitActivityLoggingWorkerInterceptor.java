package io.temporal.demo.appkit;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import io.temporal.activity.ActivityExecutionContext;
import io.temporal.activity.ActivityInfo;
import io.temporal.common.interceptors.ActivityInboundCallsInterceptor;
import io.temporal.common.interceptors.ActivityInboundCallsInterceptorBase;
import io.temporal.common.interceptors.WorkerInterceptorBase;
import java.util.Map;
import org.slf4j.MDC;

/**
 * Injects Temporal activity context and the active trace id into SLF4J MDC so the JSON log encoder
 * emits the same correlation fields as Python structlog (ADR-0018).
 */
public class AppkitActivityLoggingWorkerInterceptor extends WorkerInterceptorBase {

  @Override
  public ActivityInboundCallsInterceptor interceptActivity(
      ActivityInboundCallsInterceptor next) {
    return new ActivityLoggingInboundInterceptor(next);
  }

  private static final class ActivityLoggingInboundInterceptor
      extends ActivityInboundCallsInterceptorBase {

    private ActivityExecutionContext activityExecutionContext;

    ActivityLoggingInboundInterceptor(ActivityInboundCallsInterceptor next) {
      super(next);
    }

    @Override
    public void init(ActivityExecutionContext context) {
      this.activityExecutionContext = context;
      super.init(context);
    }

    @Override
    public ActivityOutput execute(ActivityInput input) {
      Map<String, String> previousMdc = MDC.getCopyOfContextMap();
      ActivityInfo info = activityExecutionContext.getInfo();
      putMdc("workflow_id", info.getWorkflowId());
      putMdc("run_id", info.getRunId());
      putMdc("workflow_type", info.getWorkflowType());
      putMdc("activity_id", info.getActivityId());
      putMdc("activity_type", info.getActivityType());
      putMdc("attempt", String.valueOf(info.getAttempt()));
      putMdc("task_queue", info.getActivityTaskQueue());
      putTraceId();
      try {
        return super.execute(input);
      } finally {
        restoreMdc(previousMdc);
      }
    }
  }

  private static void putTraceId() {
    SpanContext ctx = Span.current().getSpanContext();
    if (ctx.isValid()) {
      putMdc("trace_id", ctx.getTraceId());
    }
  }

  private static void putMdc(String key, String value) {
    if (value != null && !value.isBlank()) {
      MDC.put(key, value);
    }
  }

  private static void restoreMdc(Map<String, String> previousMdc) {
    if (previousMdc == null) {
      MDC.clear();
    } else {
      MDC.setContextMap(previousMdc);
    }
  }
}
