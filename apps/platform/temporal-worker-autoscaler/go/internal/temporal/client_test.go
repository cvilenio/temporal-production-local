package temporal

import (
	"errors"
	"io"
	"testing"

	"go.temporal.io/api/serviceerror"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestIsTransient(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want bool
	}{
		// The exact error observed against Temporal Cloud's edge on connection
		// recycle. It surfaces as an untyped/Unknown-coded error wrapping the raw
		// transport string, so the string fallback must catch it.
		{"cloud recycle EOF", errors.New("error reading from server: EOF"), true},
		{"raw io.EOF", io.EOF, true},
		{"grpc unavailable", status.Error(codes.Unavailable, "transport is closing"), true},
		{"grpc canceled", status.Error(codes.Canceled, "context canceled"), true},
		{"grpc deadline", status.Error(codes.DeadlineExceeded, "deadline"), true},
		{"typed sdk unavailable", serviceerror.NewUnavailable("frontend down"), true},
		{"connection reset", errors.New("read tcp: connection reset by peer"), true},

		// Real failures must NOT be swallowed as transient.
		{"not found", serviceerror.NewNotFound("no such version"), false},
		{"invalid arg", status.Error(codes.InvalidArgument, "bad build id"), false},
		{"generic", errors.New("something is wrong"), false},
		{"nil", nil, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isTransient(tc.err); got != tc.want {
				t.Fatalf("isTransient(%v) = %v, want %v", tc.err, got, tc.want)
			}
		})
	}
}
