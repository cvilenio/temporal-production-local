package scaling

import (
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
			TolerancePercent:      10,
			ScaleDownStabilization: 0,
			PanicThresholdPercent: 0,
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
