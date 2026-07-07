from __future__ import annotations

import functools
from datetime import timedelta

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from {{DOMAIN}}.shared.temporal_ids import ActivityName, TaskQueue
    from {{DOMAIN}}.shared.workflow_io import HelloInput, HelloResult

# Route every activity call to the activity worker queue (production split).
run_activity = functools.partial(
    workflow.execute_activity,
    task_queue=TaskQueue.ACTIVITY,
)


@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class HelloWorkflow:
    @workflow.run
    async def run(self, input: HelloInput) -> HelloResult:
        greeting = await run_activity(
            ActivityName.SAY_HELLO,
            input.name,
            start_to_close_timeout=timedelta(seconds=30),
        )
        return HelloResult(message=greeting)
