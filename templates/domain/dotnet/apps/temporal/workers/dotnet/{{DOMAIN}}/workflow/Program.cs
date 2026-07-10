using Temporalio.Client;
using Temporalio.Common;
using Temporalio.Worker;

namespace {{Domain}}Demo;

public class Program
{
    public static async Task Main(string[] args)
    {
        var cfg = TemporalConfig.Load(TemporalIds.WorkflowTaskQueue);
        var client = await TemporalConfig.ConnectAsync(cfg);

        using var worker = new TemporalWorker(
            client,
            new TemporalWorkerOptions(cfg.TaskQueue)
            {
                DeploymentOptions = new WorkerDeploymentOptions
                {
                    Version = new WorkerDeploymentVersion(cfg.DeploymentName, cfg.BuildId),
                    UseWorkerVersioning = true,
                },
            }
            .AddWorkflow<HelloWorkflow>());

        await worker.ExecuteAsync(CancellationToken.None);
    }
}
