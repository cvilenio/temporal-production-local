package io.temporal.demo.{{DOMAIN_PKG}}.workflow;

import io.temporal.demo.{{DOMAIN_PKG}}.shared.HelloInput;
import io.temporal.demo.{{DOMAIN_PKG}}.shared.HelloResult;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface HelloWorkflow {

  @WorkflowMethod
  HelloResult run(HelloInput input);
}
