using Temporalio.Common;
using Temporalio.Workflows;

namespace {{Domain}}Demo;

public record HelloInput(string Name);

[Workflow(TemporalIds.WorkflowHello, VersioningBehavior = VersioningBehavior.Pinned)]
public class HelloWorkflow
{
    [WorkflowRun]
    public async Task<string> RunAsync(HelloInput input)
    {
        return await Workflow.ExecuteActivityAsync(
            (IHelloActivities act) => act.SayHelloAsync(input.Name),
            new()
            {
                TaskQueue = TemporalIds.ActivityTaskQueue,
                StartToCloseTimeout = TimeSpan.FromSeconds(30),
            });
    }
}
