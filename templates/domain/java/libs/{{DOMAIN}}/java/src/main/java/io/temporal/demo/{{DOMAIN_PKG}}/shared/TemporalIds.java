package io.temporal.demo.{{DOMAIN_PKG}}.shared;

/** Within-domain Temporal identifiers — must match config/domains/{{DOMAIN}}.yaml. */
public final class TemporalIds {

  public static final String WORKFLOW_TASK_QUEUE = "{{DOMAIN}}-workflow-task-queue";
  public static final String ACTIVITY_TASK_QUEUE = "{{DOMAIN}}-activity-task-queue";

  public static final String SAY_HELLO_ACTIVITY = "say_hello";

  private TemporalIds() {}
}
