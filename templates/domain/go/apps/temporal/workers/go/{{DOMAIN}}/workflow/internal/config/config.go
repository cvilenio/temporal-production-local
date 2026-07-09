package config

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
)

type Temporal struct {
	Address    string
	Namespace  string
	TaskQueue  string
	TLSEnabled bool
	CertPath   string
	KeyPath    string
	CAPath     string
	BuildID    string
	Deployment string
}

func LoadFromEnv(taskQueue string) (Temporal, error) {
	cfg := Temporal{
		Address:    envOr("TEMPORAL_ADDRESS", "localhost:7233"),
		Namespace:  envOr("TEMPORAL_NAMESPACE", "{{DOMAIN}}"),
		TaskQueue:  taskQueue,
		TLSEnabled: os.Getenv("TEMPORAL_TLS") == "true",
		CertPath:   os.Getenv("TEMPORAL_TLS_CLIENT_CERT_PATH"),
		KeyPath:    os.Getenv("TEMPORAL_TLS_CLIENT_KEY_PATH"),
		CAPath:     os.Getenv("TEMPORAL_TLS_SERVER_CA_CERT_PATH"),
		BuildID:    os.Getenv("TEMPORAL_WORKER_BUILD_ID"),
		Deployment: os.Getenv("TEMPORAL_DEPLOYMENT_NAME"),
	}
	if cfg.Namespace == "" {
		return cfg, fmt.Errorf("TEMPORAL_NAMESPACE is required")
	}
	if cfg.BuildID == "" {
		return cfg, fmt.Errorf("TEMPORAL_WORKER_BUILD_ID is required")
	}
	if cfg.Deployment == "" {
		return cfg, fmt.Errorf("TEMPORAL_DEPLOYMENT_NAME is required")
	}
	return cfg, nil
}

func (c Temporal) ClientTLS() (*tls.Config, error) {
	if !c.TLSEnabled {
		return nil, nil
	}
	cert, err := tls.LoadX509KeyPair(c.CertPath, c.KeyPath)
	if err != nil {
		return nil, fmt.Errorf("load client cert: %w", err)
	}
	cfg := &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12}
	if c.CAPath != "" {
		caPEM, err := os.ReadFile(c.CAPath)
		if err != nil {
			return nil, fmt.Errorf("read CA: %w", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caPEM) {
			return nil, fmt.Errorf("parse CA PEM")
		}
		cfg.RootCAs = pool
	}
	return cfg, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
