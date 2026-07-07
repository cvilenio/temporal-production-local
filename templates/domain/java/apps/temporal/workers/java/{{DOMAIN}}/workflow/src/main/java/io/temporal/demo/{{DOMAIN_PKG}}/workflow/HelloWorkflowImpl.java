package io.temporal.demo.{{DOMAIN_PKG}}.workflow;

import io.temporal.activity.ActivityOptions;
import io.temporal.common.VersioningBehavior;
import io.temporal.demo.{{DOMAIN_PKG}}.activity.HelloActivity;
import io.temporal.demo.{{DOMAIN_PKG}}.shared.HelloInput;
import io.temporal.demo.{{DOMAIN_PKG}}.shared.HelloResult;
import io.temporal.demo.{{DOMAIN_PKG}}.shared.TemporalIds;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowVersioningBehavior;
import java.time.Duration;

@WorkflowImpl(taskQueues = TemporalIds.WORKFLOW_TASK_QUEUE)
public class HelloWorkflowImpl implements HelloWorkflow {

  private final HelloActivity activities =
      Workflow.newActivityStub(
          HelloActivity.class,
          ActivityOptions.newBuilder()
              .setTaskQueue(TemporalIds.ACTIVITY_TASK_QUEUE)
              .setStartToCloseTimeout(Duration.ofSeconds(30))
              .build());

  @Override
  @WorkflowVersioningBehavior(VersioningBehavior.PINNED)
  public HelloResult run(HelloInput input) {
    String greeting = activities.sayHello(input.name());
    return new HelloResult(greeting);
  }
}
