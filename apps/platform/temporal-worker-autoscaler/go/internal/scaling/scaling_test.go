package scaling

import (
	"math"
	"testing"
	"time"
)

func base(cur int32, backlog int64) Input {
	return Input{
		Current:          cur,
		Backlog:          backlog,
		TargetPerReplica: 5,
		Min:              1,
		Max:              10,
		Now:              time.Unix(1_000_000, 0),
		Behavior: Behavior{
			TolerancePercent:       10,
			ScaleDownStabilization: 0,
			PanicThresholdPercent:  0,
		},
	}
}

func TestScaleUpCeil(t *testing.T) {
	s := NewHPAScaler()
	// 12 backlog / 5 target = ceil(2.4) = 3.
	d := s.Decide("k", base(1, 12))
	if d.Desired != 3 || !d.Changed {
		t.Fatalf("want desired=3 changed; got %+v", d)
	}
}

func TestDeadbandHolds(t *testing.T) {
	s := NewHPAScaler()
	// current 2, backlog 10 -> perPod 5, ratio 1.0 -> within tolerance, hold.
	d := s.Decide("k", base(2, 10))
	if d.Desired != 2 || d.Changed {
		t.Fatalf("want hold at 2; got %+v (%s)", d, d.Reason)
	}
}

func TestScaleFromZero(t *testing.T) {
	s := NewHPAScaler()
	in := base(0, 1)
	in.Min = 0
	d := s.Decide("k", in)
	if d.Desired != 1 {
		t.Fatalf("scale-from-zero: want 1; got %+v", d)
	}
}

func TestScaleToZero(t *testing.T) {
	s := NewHPAScaler()
	in := base(3, 0)
	in.Min = 0
	d := s.Decide("k", in)
	if d.Desired != 0 {
		t.Fatalf("scale-to-zero: want 0; got %+v (%s)", d, d.Reason)
	}
}

func TestClampToMax(t *testing.T) {
	s := NewHPAScaler()
	d := s.Decide("k", base(1, 1000)) // ceil(200) but max=10
	if d.Desired != 10 {
		t.Fatalf("want clamp to max 10; got %+v", d)
	}
}

func TestDownscaleStabilizationHoldsMax(t *testing.T) {
	s := NewHPAScaler()
	in := base(8, 40) // perPod 5, ratio 1.0 within tolerance -> hold 8, records 8
	in.Behavior.ScaleDownStabilization = 120 * time.Second
	if d := s.Decide("k", in); d.Desired != 8 {
		t.Fatalf("setup: want hold 8; got %+v (%s)", d, d.Reason)
	}
	// Backlog collapses to 0 shortly after; raw would be min(1), but the recent
	// max recommendation (8) inside the down window must hold us up.
	in2 := base(8, 0)
	in2.Behavior.ScaleDownStabilization = 120 * time.Second
	in2.Now = in.Now.Add(10 * time.Second)
	d := s.Decide("k", in2)
	if d.Desired != 8 {
		t.Fatalf("down-stabilization should hold at 8; got %+v (%s)", d, d.Reason)
	}
	// Long after the window, it may finally scale down.
	in3 := base(8, 0)
	in3.Behavior.ScaleDownStabilization = 120 * time.Second
	in3.Now = in.Now.Add(200 * time.Second)
	d = s.Decide("k", in3)
	if d.Desired != 1 {
		t.Fatalf("after window, want scale down to min 1; got %+v (%s)", d, d.Reason)
	}
}

func TestPanicBypassesUpStabilization(t *testing.T) {
	s := NewHPAScaler()
	in := base(2, 40) // perPod 20, ratio 4.0 -> panic if threshold 200%
	in.Behavior.ScaleUpStabilization = 300 * time.Second
	in.Behavior.PanicThresholdPercent = 200
	d := s.Decide("k", in)
	if !d.Panic {
		t.Fatalf("want panic; got %+v (%s)", d, d.Reason)
	}
	if d.Desired != 8 { // ceil(40/5)=8, up-stabilization bypassed
		t.Fatalf("panic should scale to 8; got %+v (%s)", d, d.Reason)
	}
}

func TestMaxScaleUpStep(t *testing.T) {
	s := NewHPAScaler()
	in := base(2, 45) // ceil(9)=9, but step caps +2 -> 4
	in.Behavior.MaxScaleUpStep = 2
	d := s.Decide("k", in)
	if d.Desired != 4 {
		t.Fatalf("up-step cap: want 4; got %+v (%s)", d, d.Reason)
	}
}

func TestMaxScaleDownStep(t *testing.T) {
	s := NewHPAScaler()
	in := base(9, 5) // ceil(1)=1, but step caps -1 -> 8
	in.Behavior.MaxScaleDownStep = 1
	d := s.Decide("k", in)
	if d.Desired != 8 {
		t.Fatalf("down-step cap: want 8; got %+v (%s)", d, d.Reason)
	}
}

func slotBase(cur int32, backlog int64) Input {
	in := base(cur, backlog)
	in.SlotUpOn = true
	in.SlotDownGateOn = true
	in.TargetSlotUtil = 0.75
	in.IdleSlotUtil = 0.25
	return in
}

func TestQuietBacklogSaturatedSlotsScalesUp(t *testing.T) {
	s := NewHPAScaler()
	// Backlog within deadband (2 pods, 10 backlog, target 5 → ratio 1.0) but slots at 90%.
	in := slotBase(2, 10)
	in.SlotUpHint = 0.90
	in.SlotIdleHint = 0.90
	d := s.Decide("k", in)
	// ceil(2 * 0.9 / 0.75) = 3
	if d.Desired != 3 || !d.Changed || !d.SlotDrivenUp {
		t.Fatalf("want slot-driven scale to 3; got %+v (%s)", d, d.Reason)
	}
}

func TestLowBacklogBusySlotsVetoesDown(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(4, 0)
	in.SlotUpOn = false
	in.SlotIdleHint = 0.50 // above idle gate 0.25
	d := s.Decide("k", in)
	if d.Desired != 4 || d.Changed || !d.SlotDownVeto {
		t.Fatalf("want hold at 4 with veto; got %+v (%s)", d, d.Reason)
	}
}

func TestLowBacklogIdleSlotsScalesDown(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(4, 0)
	in.SlotUpHint = 0.10
	in.SlotIdleHint = 0.10 // below idle gate
	d := s.Decide("k", in)
	if d.Desired != 1 {
		t.Fatalf("want scale down to min 1; got %+v (%s)", d, d.Reason)
	}
}

func TestSlotFlagsOffMatchesBacklogOnly(t *testing.T) {
	s := NewHPAScaler()
	// Deadband hold without slot flags.
	d1 := s.Decide("k1", base(2, 10))
	// Same with NaN slot hints and flags off.
	in := base(2, 10)
	in.SlotUpHint = math.NaN()
	in.SlotIdleHint = math.NaN()
	d2 := s.Decide("k2", in)
	if d1.Desired != d2.Desired || d1.Changed != d2.Changed {
		t.Fatalf("flags off should match backlog-only: %+v vs %+v", d1, d2)
	}
}

func TestNaNUpHintBacklogOnlyUp(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(2, 12) // backlog wants 3
	in.SlotUpHint = math.NaN()
	d := s.Decide("k", in)
	if d.Desired != 3 {
		t.Fatalf("want backlog-driven 3; got %+v (%s)", d, d.Reason)
	}
}

func TestNaNIdleHintDownNotVetoed(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(4, 0)
	in.SlotIdleHint = math.NaN()
	d := s.Decide("k", in)
	if d.Desired != 1 || d.SlotDownVeto {
		t.Fatalf("want down without veto; got %+v (%s)", d, d.Reason)
	}
}

func TestSlotDrivenUpRespectsStepClamp(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(2, 10) // deadband hold on backlog alone
	in.SlotUpHint = 0.95
	in.Behavior.MaxScaleUpStep = 1
	d := s.Decide("k", in)
	if d.Desired != 3 {
		t.Fatalf("slot-up step capped +1: want 3; got %+v (%s)", d, d.Reason)
	}
}

func TestScaleToZeroWithIdleSlots(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(1, 0)
	in.Min = 0
	in.SlotUpOn = false
	in.SlotUpHint = math.NaN()
	in.SlotIdleHint = 0.05
	d := s.Decide("k", in)
	if d.Desired != 0 {
		t.Fatalf("min=0 idle slots: want 0; got %+v (%s)", d, d.Reason)
	}
}

func TestScaleToZeroBusySlotsHolds(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(1, 0)
	in.Min = 0
	in.SlotIdleHint = 0.60
	d := s.Decide("k", in)
	if d.Desired != 1 || !d.SlotDownVeto {
		t.Fatalf("min=0 busy slots: want hold 1; got %+v (%s)", d, d.Reason)
	}
}

func TestUpOnlyFlag(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(2, 0)
	in.SlotDownGateOn = false
	in.SlotUpHint = 0.90
	in.SlotIdleHint = 0.90
	d := s.Decide("k", in)
	if d.Desired != 3 {
		t.Fatalf("up-only: want 3; got %+v (%s)", d, d.Reason)
	}
}

func TestDownOnlyFlag(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(4, 0)
	in.SlotUpOn = false
	in.SlotIdleHint = 0.50
	d := s.Decide("k", in)
	if d.Desired != 4 || !d.SlotDownVeto {
		t.Fatalf("down-only veto: want hold 4; got %+v (%s)", d, d.Reason)
	}
}

func TestLowSlotUtilDoesNotFloorReplicas(t *testing.T) {
	s := NewHPAScaler()
	// BUG 1 regression: slotDrivenDesired returns 2 when util is below target, but
	// that must not raise raw above backlog-only min when slotWantsUp is false.
	in := slotBase(3, 0)
	in.SlotUpHint = 0.50
	in.SlotIdleHint = 0.10 // below idle gate so AND-down does not veto the shrink
	d := s.Decide("k", in)
	if d.Desired != 1 || !d.Changed {
		t.Fatalf("low slot util must not floor replicas at 2; want scale down to min 1; got %+v (%s)", d, d.Reason)
	}
	if d.SlotDrivenUp {
		t.Fatalf("must not mark slot-driven up when util below target; got %+v", d)
	}
}

func TestQuietBacklogHighSlotUtilScalesUpFromThree(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(3, 0)
	in.SlotUpHint = 0.90
	in.SlotIdleHint = 0.90
	d := s.Decide("k", in)
	// ceil(3 * 0.9 / 0.75) = 4
	if d.Desired != 4 || !d.Changed || !d.SlotDrivenUp {
		t.Fatalf("genuine OR-up: want slot-driven scale to 4; got %+v (%s)", d, d.Reason)
	}
}

func TestExplicitZeroIdleGateVetoesAnyPositiveIdle(t *testing.T) {
	s := NewHPAScaler()
	in := slotBase(4, 0)
	in.SlotUpOn = false
	in.IdleSlotUtil = 0.0 // strictest explicit gate; must not be coerced to 0.25
	in.SlotIdleHint = 0.01
	d := s.Decide("k", in)
	if d.Desired != 4 || !d.SlotDownVeto {
		t.Fatalf("explicit 0%% idle gate must veto any positive idle hint; got %+v (%s)", d, d.Reason)
	}
}
