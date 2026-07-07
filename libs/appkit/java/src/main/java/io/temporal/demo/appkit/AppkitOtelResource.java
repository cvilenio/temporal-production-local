package io.temporal.demo.appkit;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.sdk.resources.Resource;

/** Shared OTel resource attributes for tracing and business metrics (ADR-0018). */
final class AppkitOtelResource {

  private static final AttributeKey<String> SERVICE_NAME = AttributeKey.stringKey("service.name");
  private static final AttributeKey<String> SERVICE_NAMESPACE =
      AttributeKey.stringKey("service.namespace");
  private static final AttributeKey<String> SERVICE_INSTANCE_ID =
      AttributeKey.stringKey("service.instance.id");
  private static final AttributeKey<String> SERVICE_VERSION = AttributeKey.stringKey("service.version");

  private AppkitOtelResource() {}

  static Resource build(AppkitTelemetryProperties telemetry) {
    var attributes = Attributes.builder().put(SERVICE_NAME, telemetry.getOtelServiceName());
    if (telemetry.getServiceNamespace() != null && !telemetry.getServiceNamespace().isBlank()) {
      attributes.put(SERVICE_NAMESPACE, telemetry.getServiceNamespace());
    }
    if (telemetry.getServiceInstanceId() != null && !telemetry.getServiceInstanceId().isBlank()) {
      attributes.put(SERVICE_INSTANCE_ID, telemetry.getServiceInstanceId());
    }
    if (telemetry.getWorkerBuildId() != null && !telemetry.getWorkerBuildId().isBlank()) {
      attributes.put(SERVICE_VERSION, telemetry.getWorkerBuildId());
    }
    return Resource.getDefault().merge(Resource.create(attributes.build()));
  }
}
