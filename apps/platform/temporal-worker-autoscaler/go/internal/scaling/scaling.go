// Package scaling is the decision math: given the current replica count and the
// freshest backlog, compute the desired replica count. It mirrors Kubernetes'
// HPA algorithm (ratio + tolerance deadband + max-over-window downscale
// stabilization + step clamp) and adds Knative's panic/stable idea for bursts.
//
// Deliberately NOT importing k8s.io/kubernetes (un-vendorable) or Knative
// serving (not standalone) — the algorithm is small enough to own and test.
// The Algorithm interface follows AIBrix's swappable-scaler shape so alternative
// math can be dropped in.
package scaling

import (
	"fmt"
	"math"
	"sync"
	"time"
)

// Behavior tunes reaction speed and flap damping (from WorkerAutoscaler.spec).
type Behavior struct {
	ScaleUpStabilization   time.Duration
	ScaleDownStabilization time.Duration
	TolerancePercent       int32
	MaxScaleUpStep         int32 // 0 = unlimited
	MaxScaleDownStep       int32 // 0 = unlimited
	PanicThresholdPercent  int32 // 0 = panic disabled
}

// Input is one scaling decision's context for a single version.
type Input struct {
	Current          int32
	Backlog          int64
	TargetPerReplica int32
	Min              int32
	Max              int32
	Now              time.Time
	Behavior         Behavior
}

// Decision is the result: the replica count to set, plus why.
type Decision struct {
	Desired int32
	Reason  string
	Panic   bool
	Changed bool // Desired != Current
}

// Algorithm computes desired replicas. Keyed per version so stabilization
// history is tracked independently.
type Algorithm interface {
	Decide(key string, in Input) Decision
}

// HPAScaler is the default Algorithm.
type HPAScaler struct {
	mu   sync.Mutex
	hist map[string]*history
}

// NewHPAScaler returns a ready HPAScaler.
func NewHPAScaler() *HPAScaler { return &HPAScaler{hist: make(map[string]*history)} }

type sample struct {
	t time.Time
	v int32
}

type history struct{ recs []sample }

func (h *history) record(now time.Time, v int32, keep time.Duration) {
	h.recs = append(h.recs, sample{now, v})
	cut := now.Add(-keep)
	i := 0
	for ; i < len(h.recs); i++ {
		if !h.recs[i].t.Before(cut) {
			break
		}
	}
	if i > 0 {
		h.recs = h.recs[i:]
	}
}

func (h *history) maxOver(now time.Time, window time.Duration) int32 {
	cut := now.Add(-window)
	var m int32 = math.MinInt32
	for _, s := range h.recs {
		if !s.t.Before(cut) && s.v > m {
			m = s.v
		}
	}
	return m
}

func (h *history) minOver(now time.Time, window time.Duration) int32 {
	cut := now.Add(-window)
	var m int32 = math.MaxInt32
	for _, s := range h.recs {
		if !s.t.Before(cut) && s.v < m {
			m = s.v
		}
	}
	return m
}

// Decide implements Algorithm.
func (s *HPAScaler) Decide(key string, in Input) Decision {
	s.mu.Lock()
	defer s.mu.Unlock()

	target := in.TargetPerReplica
	if target < 1 {
		target = 1
	}

	// Raw recommendation from the metric: ceil(backlog / target).
	raw := int32(math.Ceil(float64(in.Backlog) / float64(target)))
	if in.Backlog > 0 && raw < 1 {
		raw = 1 // scale-from-zero needs at least one worker to make progress
	}
	raw = clamp(raw, in.Min, in.Max)

	// Tolerance deadband (only meaningful when we have running pods to measure).
	tol := float64(in.Behavior.TolerancePercent) / 100.0
	panic := false
	if in.Current > 0 {
		ratio := (float64(in.Backlog) / float64(in.Current)) / float64(target)
		if math.Abs(ratio-1.0) <= tol && raw >= in.Min && raw <= in.Max {
			// Within deadband: hold, but still record so stabilization sees it.
			s.histFor(key).record(in.Now, in.Current, s.keep(in.Behavior))
			return Decision{Desired: in.Current, Reason: fmt.Sprintf(
				"within tolerance (ratio=%.2f, backlog=%d, target=%d)", ratio, in.Backlog, target)}
		}
		if p := in.Behavior.PanicThresholdPercent; p > 0 && ratio >= float64(p)/100.0 {
			panic = true
		}
	}

	h := s.histFor(key)
	h.record(in.Now, raw, s.keep(in.Behavior))

	desired := raw
	reason := fmt.Sprintf("backlog=%d target=%d -> %d", in.Backlog, target, raw)

	// Downscale stabilization: don't drop below the MAX recommendation over the
	// window — the primary anti-flap lever.
	if desired < in.Current && in.Behavior.ScaleDownStabilization > 0 {
		if maxRec := h.maxOver(in.Now, in.Behavior.ScaleDownStabilization); maxRec > desired {
			desired = clamp(maxRec, in.Min, in.Max)
			reason = fmt.Sprintf("down-stabilized to %d (raw=%d)", desired, raw)
		}
	}

	// Upscale stabilization: don't exceed the MIN recommendation over the window,
	// unless panic. With window 0 this is a no-op (react immediately up).
	if desired > in.Current && !panic && in.Behavior.ScaleUpStabilization > 0 {
		if minRec := h.minOver(in.Now, in.Behavior.ScaleUpStabilization); minRec < desired {
			desired = clamp(minRec, in.Min, in.Max)
			reason = fmt.Sprintf("up-stabilized to %d (raw=%d)", desired, raw)
		}
	}
	if panic {
		reason = fmt.Sprintf("panic: scale up to %d (backlog=%d target=%d)", desired, in.Backlog, target)
	}

	// Step clamp: bound the delta per decision.
	if desired > in.Current && in.Behavior.MaxScaleUpStep > 0 {
		if cap := in.Current + in.Behavior.MaxScaleUpStep; desired > cap {
			desired = cap
			reason += fmt.Sprintf(" (up-step capped to +%d)", in.Behavior.MaxScaleUpStep)
		}
	}
	if desired < in.Current && in.Behavior.MaxScaleDownStep > 0 {
		if floor := in.Current - in.Behavior.MaxScaleDownStep; desired < floor {
			desired = floor
			reason += fmt.Sprintf(" (down-step capped to -%d)", in.Behavior.MaxScaleDownStep)
		}
	}

	desired = clamp(desired, in.Min, in.Max)
	return Decision{Desired: desired, Reason: reason, Panic: panic, Changed: desired != in.Current}
}

func (s *HPAScaler) histFor(key string) *history {
	h, ok := s.hist[key]
	if !ok {
		h = &history{}
		s.hist[key] = h
	}
	return h
}

// keep is how long to retain recommendations (the larger stabilization window).
func (s *HPAScaler) keep(b Behavior) time.Duration {
	k := b.ScaleDownStabilization
	if b.ScaleUpStabilization > k {
		k = b.ScaleUpStabilization
	}
	if k < time.Second {
		k = time.Second
	}
	return k
}

// Forget drops per-version history (call when a version is sunset).
func (s *HPAScaler) Forget(key string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.hist, key)
}

func clamp(v, lo, hi int32) int32 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
