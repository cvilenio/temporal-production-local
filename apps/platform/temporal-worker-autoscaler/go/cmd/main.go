// Command temporal-worker-autoscaler is a leader-elected controller that scales
// Temporal worker Deployments directly from live task-queue backlog, for
// seconds-level actuation (bypassing the HPA sync loop). See ADR-0023.
//
// Layout (first Go deployable in this repo; mirrors the settings/wiring/main
// split of the Python apps, ADR-0022):
//   - internal/config     — env -> typed Config (settings.py role)
//   - internal/temporal   — the one Temporal Cloud call (DescribeWorkerDeploymentVersion)
//   - internal/scaling    — decision algorithm (HPA ratio + stable/panic)
//   - internal/metrics    — Prometheus series for scaling decisions
//   - internal/controller — the reconciler (discover versions, decide, patch, record)
//   - cmd/main.go         — composition root + manager lifecycle
package main

import (
	"os"
	"time"

	"golang.org/x/time/rate"
	"k8s.io/apimachinery/pkg/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	autoscalingv1alpha1 "github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/api/v1alpha1"
	"github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/config"
	"github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/controller"
	"github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/promsource"
	"github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/scaling"
	temporalpkg "github.com/cvilenio/temporal-production-local/apps/platform/temporal-worker-autoscaler/go/internal/temporal"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

func init() {
	utilruntimeMust(clientgoscheme.AddToScheme(scheme))
	utilruntimeMust(autoscalingv1alpha1.AddToScheme(scheme))
}

func main() {
	ctrl.SetLogger(zap.New(zap.UseDevMode(false)))

	cfg, err := config.Load()
	if err != nil {
		setupLog.Error(err, "invalid configuration")
		os.Exit(1)
	}

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme:                 scheme,
		Metrics:                metricsserver.Options{BindAddress: cfg.MetricsAddr},
		HealthProbeBindAddress: cfg.HealthProbeAddr,
		LeaderElection:         cfg.EnableLeaderElection,
		LeaderElectionID:       cfg.LeaderElectionID,
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	tc, err := temporalpkg.Dial(cfg.TemporalHostPort, cfg.TemporalNamespace, cfg.TemporalAPIKey, cfg.TemporalTLS, temporalpkg.TLSPaths{
		ClientCertPath:   cfg.TemporalTLSClientCertPath,
		ClientKeyPath:    cfg.TemporalTLSClientKeyPath,
		ServerCACertPath: cfg.TemporalTLSServerCACertPath,
	})
	if err != nil {
		setupLog.Error(err, "unable to build Temporal client")
		os.Exit(1)
	}
	defer tc.Close()

	if err := (&controller.WorkerAutoscalerReconciler{
		Client:            mgr.GetClient(),
		Recorder:          mgr.GetEventRecorderFor("temporal-worker-autoscaler"),
		Temporal:          tc,
		Prom:              promsource.New(cfg.PrometheusURL),
		TemporalNamespace: cfg.TemporalNamespace,
		Algo:              scaling.NewHPAScaler(),
		// Gentle + spaced (burst 1): the Cloud Worker-Deployment-Read API trips at a
		// low RPS, so never burst describe calls. ~1.3/s max, one at a time.
		Limiter:         rate.NewLimiter(rate.Every(750*time.Millisecond), 1),
		RequeueInterval: cfg.PollInterval,
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "WorkerAutoscaler")
		os.Exit(1)
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting temporal-worker-autoscaler",
		"namespace", cfg.TemporalNamespace, "pollInterval", cfg.PollInterval)
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}

func utilruntimeMust(err error) {
	if err != nil {
		setupLog.Error(err, "scheme setup failed")
		os.Exit(1)
	}
}
