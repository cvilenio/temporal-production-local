package io.temporal.demo.appkit;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Observability settings — mirrors Python {@code TelemetrySettings} env mapping (ADR-0018).
 *
 * <p>Each worker process overrides {@code otelServiceName} at deploy time. On Kubernetes the
 * Grafana Alloy DaemonSet tails pod stdout, so {@code logOtlpPush=false} (no direct app→backend
 * log push).
 */
@ConfigurationProperties(prefix = "appkit.telemetry")
public class AppkitTelemetryProperties {

  private String otelExporterOtlpEndpoint = "http://localhost:4317";
  private String otelExporterOtlpMetricsEndpoint;
  private String otelServiceName = "app";
  private int sdkMetricsPort = 9000;
  private String logLevel = "INFO";
  private String logFormat = "json";
  private boolean logOtlpPush = true;
  private String serviceNamespace;
  private String serviceInstanceId;
  private String workerBuildId;

  public String getOtelExporterOtlpEndpoint() {
    return otelExporterOtlpEndpoint;
  }

  public void setOtelExporterOtlpEndpoint(String otelExporterOtlpEndpoint) {
    this.otelExporterOtlpEndpoint = otelExporterOtlpEndpoint;
  }

  public String getOtelExporterOtlpMetricsEndpoint() {
    return otelExporterOtlpMetricsEndpoint;
  }

  public void setOtelExporterOtlpMetricsEndpoint(String otelExporterOtlpMetricsEndpoint) {
    this.otelExporterOtlpMetricsEndpoint = otelExporterOtlpMetricsEndpoint;
  }

  /** Resolved metrics endpoint — falls back to the trace endpoint when unset. */
  public String resolvedMetricsEndpoint() {
    if (otelExporterOtlpMetricsEndpoint != null && !otelExporterOtlpMetricsEndpoint.isBlank()) {
      return otelExporterOtlpMetricsEndpoint;
    }
    return otelExporterOtlpEndpoint;
  }

  public String getOtelServiceName() {
    return otelServiceName;
  }

  public void setOtelServiceName(String otelServiceName) {
    this.otelServiceName = otelServiceName;
  }

  public int getSdkMetricsPort() {
    return sdkMetricsPort;
  }

  public void setSdkMetricsPort(int sdkMetricsPort) {
    this.sdkMetricsPort = sdkMetricsPort;
  }

  public String getLogLevel() {
    return logLevel;
  }

  public void setLogLevel(String logLevel) {
    this.logLevel = logLevel;
  }

  public String getLogFormat() {
    return logFormat;
  }

  public void setLogFormat(String logFormat) {
    this.logFormat = logFormat;
  }

  public boolean isLogOtlpPush() {
    return logOtlpPush;
  }

  public void setLogOtlpPush(boolean logOtlpPush) {
    this.logOtlpPush = logOtlpPush;
  }

  public String getServiceNamespace() {
    return serviceNamespace;
  }

  public void setServiceNamespace(String serviceNamespace) {
    this.serviceNamespace = serviceNamespace;
  }

  public String getServiceInstanceId() {
    return serviceInstanceId;
  }

  public void setServiceInstanceId(String serviceInstanceId) {
    this.serviceInstanceId = serviceInstanceId;
  }

  public String getWorkerBuildId() {
    return workerBuildId;
  }

  public void setWorkerBuildId(String workerBuildId) {
    this.workerBuildId = workerBuildId;
  }
}
