package controller

import (
	"testing"

	autoscalingv1alpha1 "github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/api/v1alpha1"
)

func TestSlotInputFromSpecHonorsExplicitZeroIdleGate(t *testing.T) {
	got := slotInputFromSpec(&autoscalingv1alpha1.WorkerAutoscalerSpec{
		ScaleDownSlotUtilizationPercent: 0,
	})
	if got.IdleSlotUtil != 0.0 {
		t.Fatalf("explicit 0%% idle gate must not coerce to default 25%%; got IdleSlotUtil=%v", got.IdleSlotUtil)
	}
}

func TestSlotInputFromSpecPassesThroughNonZeroIdleGate(t *testing.T) {
	got := slotInputFromSpec(&autoscalingv1alpha1.WorkerAutoscalerSpec{
		ScaleDownSlotUtilizationPercent: 25,
	})
	if got.IdleSlotUtil != 0.25 {
		t.Fatalf("want IdleSlotUtil=0.25; got %v", got.IdleSlotUtil)
	}
}
