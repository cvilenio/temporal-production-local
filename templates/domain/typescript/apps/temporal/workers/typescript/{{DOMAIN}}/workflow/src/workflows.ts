import { proxyActivities, setWorkflowOptions } from "@temporalio/workflow";
import {
  ActivityName,
  TaskQueue,
} from "@temporal-demo/{{DOMAIN}}-lib/temporal-ids";

export interface HelloInput {
  name: string;
}

type HelloActivities = {
  [ActivityName.SAY_HELLO](name: string): Promise<string>;
};

const { [ActivityName.SAY_HELLO]: sayHello } =
  proxyActivities<HelloActivities>({
    taskQueue: TaskQueue.ACTIVITY,
    startToCloseTimeout: "30 seconds",
  });

export async function HelloWorkflow(input: HelloInput): Promise<string> {
  return sayHello(input.name);
}

setWorkflowOptions({ versioningBehavior: "PINNED" }, HelloWorkflow);
