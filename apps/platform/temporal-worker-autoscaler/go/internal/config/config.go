// Package config is the typed settings layer (env -> Config), mirroring the
// repo's settings.py role for Python apps (ADR-0022). The Temporal connection is
// controller-global (one namespace per controller); per-worker knobs live on the
// WorkerAutoscaler CRD.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Config is the controller's process-level configuration.
type Config struct {
	// Temporal connection (shared by the central poller). On Cloud this is an
	// API key; on the self-hosted OSS backend it is an mTLS client cert + the
	// self-signed server CA (exactly one credential type is populated).
	TemporalHostPort  string // regional (Cloud) or in-cluster (OSS) endpoint
	TemporalNamespace string // <ns>.<account> (Cloud) or the bare domain (OSS)
	TemporalAPIKey    string // Bearer token (Cloud; from the mounted Secret)
	TemporalTLS       bool
	// OSS mTLS material (file paths from the mounted cert-manager Secret). Empty on Cloud.
	TemporalTLSClientCertPath   string
	TemporalTLSClientKeyPath    string
	TemporalTLSServerCACertPath string

	// PollInterval is the central Cloud poll cadence. Fast because it is ONE
	// caller; keep it rate-safe with jitter (see poller).
	PollInterval time.Duration

	// Kubernetes controller-runtime manager options.
	MetricsAddr          string
	HealthProbeAddr      string
	EnableLeaderElection bool
	LeaderElectionID     string

	// PrometheusURL is the in-cluster Prometheus HTTP API base (slot hints).
	PrometheusURL string
}

// Load reads Config from the environment, applying defaults.
func Load() (*Config, error) {
	c := &Config{
		TemporalHostPort:            getenv("TEMPORAL_HOSTPORT", ""),
		TemporalNamespace:           getenv("TEMPORAL_NAMESPACE", ""),
		TemporalAPIKey:              readSecretOrEnv("TEMPORAL_API_KEY_FILE", "TEMPORAL_API_KEY"),
		TemporalTLS:                 getbool("TEMPORAL_TLS", true),
		TemporalTLSClientCertPath:   getenv("TEMPORAL_TLS_CLIENT_CERT_PATH", ""),
		TemporalTLSClientKeyPath:    getenv("TEMPORAL_TLS_CLIENT_KEY_PATH", ""),
		TemporalTLSServerCACertPath: getenv("TEMPORAL_TLS_SERVER_CA_CERT_PATH", ""),
		// 15s default: the Cloud Worker-Deployment-Read API is aggressively
		// rate-limited, so this is the safe backlog-freshness floor for one central
		// caller polling per version. Actuation stays instant once the signal lands.
		PollInterval:         getdur("POLL_INTERVAL", 15*time.Second),
		MetricsAddr:          getenv("METRICS_ADDR", ":8080"),
		HealthProbeAddr:      getenv("HEALTH_PROBE_ADDR", ":8081"),
		EnableLeaderElection: getbool("ENABLE_LEADER_ELECTION", true),
		LeaderElectionID:     getenv("LEADER_ELECTION_ID", "temporal-worker-autoscaler.autoscaling.ziggymart.io"),
		PrometheusURL:        getenv("PROMETHEUS_URL", "http://prometheus-server.observability.svc.cluster.local:80"),
	}
	if c.TemporalHostPort == "" || c.TemporalNamespace == "" {
		return nil, fmt.Errorf("TEMPORAL_HOSTPORT and TEMPORAL_NAMESPACE are required")
	}
	return c, nil
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// readSecretOrEnv prefers a file path (k8s Secret mount) over an inline env var,
// mirroring how the Prometheus scrape reads the Cloud API key from a file.
func readSecretOrEnv(fileKey, envKey string) string {
	if p := os.Getenv(fileKey); p != "" {
		if b, err := os.ReadFile(p); err == nil {
			return string(trimNewline(b))
		}
	}
	return os.Getenv(envKey)
}

func trimNewline(b []byte) []byte {
	for len(b) > 0 && (b[len(b)-1] == '\n' || b[len(b)-1] == '\r') {
		b = b[:len(b)-1]
	}
	return b
}

func getbool(k string, def bool) bool {
	if v := os.Getenv(k); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}

func getdur(k string, def time.Duration) time.Duration {
	if v := os.Getenv(k); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}
