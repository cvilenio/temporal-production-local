package main

import (
	"log"

	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/worker"
	"go.temporal.io/sdk/workflow"

	"temporal.io/demo/workers/{{DOMAIN}}/workflow/internal/config"
	"temporal.io/demo/workers/{{DOMAIN}}/workflow/internal/temporalids"
	"temporal.io/demo/workers/{{DOMAIN}}/workflow/internal/workflows"
)

func main() {
	cfg, err := config.LoadFromEnv(temporalids.WorkflowTaskQueue)
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	tlsCfg, err := cfg.ClientTLS()
	if err != nil {
		log.Fatalf("tls: %v", err)
	}

	clientOpts := client.Options{
		HostPort:  cfg.Address,
		Namespace: cfg.Namespace,
	}
	if tlsCfg != nil {
		clientOpts.ConnectionOptions.TLS = tlsCfg
	}

	c, err := client.Dial(clientOpts)
	if err != nil {
		log.Fatalf("client dial: %v", err)
	}
	defer c.Close()

	w := worker.New(c, cfg.TaskQueue, worker.Options{
		DeploymentOptions: worker.DeploymentOptions{
			UseVersioning: true,
			Version: worker.WorkerDeploymentVersion{
				DeploymentName: cfg.Deployment,
				BuildID:        cfg.BuildID,
			},
		},
	})

	w.RegisterWorkflowWithOptions(workflows.HelloWorkflow, workflow.RegisterOptions{
		Name:               temporalids.WorkflowHello,
		VersioningBehavior: workflow.VersioningBehaviorPinned,
	})

	log.Printf("starting Go workflow worker on queue %s namespace %s", cfg.TaskQueue, cfg.Namespace)
	if err := w.Run(worker.InterruptCh()); err != nil {
		log.Fatalf("worker run: %v", err)
	}
}
