package workflows

import (
	"time"

	"go.temporal.io/sdk/workflow"

	"temporal.io/demo/workers/{{DOMAIN}}/workflow/internal/temporalids"
)

type HelloInput struct {
	Name string `json:"name"`
}

type HelloResult struct {
	Message string `json:"message"`
}

// HelloWorkflow routes activities to the activity worker queue (production split).
func HelloWorkflow(ctx workflow.Context, input HelloInput) (HelloResult, error) {
	ao := workflow.ActivityOptions{
		TaskQueue:           temporalids.ActivityTaskQueue,
		StartToCloseTimeout: 30 * time.Second,
	}
	ctx1 := workflow.WithActivityOptions(ctx, ao)
	var greeting string
	err := workflow.ExecuteActivity(ctx1, temporalids.ActivitySayHello, input.Name).Get(ctx1, &greeting)
	if err != nil {
		return HelloResult{}, err
	}
	return HelloResult{Message: greeting}, nil
}
