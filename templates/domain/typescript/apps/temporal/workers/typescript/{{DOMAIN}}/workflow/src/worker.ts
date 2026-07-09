import { Worker } from "@temporalio/worker";
import { connectTemporal } from "@temporal-demo/{{DOMAIN}}-lib/temporal-connection";
import { TaskQueue } from "@temporal-demo/{{DOMAIN}}-lib/temporal-ids";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

async function run(): Promise<void> {
  const address = process.env.TEMPORAL_ADDRESS ?? "localhost:7233";
  const namespace = process.env.TEMPORAL_NAMESPACE ?? "{{DOMAIN}}";
  const buildId = requireEnv("TEMPORAL_WORKER_BUILD_ID");
  const deploymentName = requireEnv("TEMPORAL_DEPLOYMENT_NAME");
  const connection = await connectTemporal(address);
  const worker = await Worker.create({
    connection,
    namespace,
    taskQueue: TaskQueue.WORKFLOW,
    workflowsPath: new URL("./workflows.js", import.meta.url).pathname,
    workerDeploymentOptions: {
      useWorkerVersioning: true,
      version: { deploymentName, buildId },
      defaultVersioningBehavior: "PINNED",
    },
  });
  await worker.run();
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
