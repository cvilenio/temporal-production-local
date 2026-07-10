# frozen_string_literal: true

require 'temporalio/workflow'
require 'temporalio/workflow/definition'
require '{{DOMAIN_PKG}}_lib'

module {{Domain}}Workflow
  class HelloWorkflow < Temporalio::Workflow::Definition
    workflow_name {{Domain}}Lib::TemporalIds::WORKFLOW_HELLO
    workflow_versioning_behavior Temporalio::VersioningBehavior::PINNED
    workflow_arg_hint {{Domain}}Lib::HelloInput

    def execute(input)
      hello_input =
        if input.is_a?(Hash)
          {{Domain}}Lib::HelloInput.new(name: input.fetch('name'))
        else
          input
        end
      Temporalio::Workflow.execute_activity(
        {{Domain}}Lib::Activities::SayHelloActivity,
        hello_input.name,
        task_queue: {{Domain}}Lib::TemporalIds::ACTIVITY_TASK_QUEUE,
        start_to_close_timeout: 30
      )
    end
  end
end
