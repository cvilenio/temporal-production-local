# frozen_string_literal: true

require 'temporalio/activity'

module {{Domain}}Lib
  module Activities
    class SayHelloActivity < Temporalio::Activity::Definition
      def execute(name)
        "Hello, #{name}!"
      end
    end
  end
end
