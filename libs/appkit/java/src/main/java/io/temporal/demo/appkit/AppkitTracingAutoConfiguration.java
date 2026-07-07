package io.temporal.demo.appkit;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.exporter.otlp.trace.OtlpGrpcSpanExporter;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.BatchSpanProcessor;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

/**
 * OTLP trace export with W3C propagation so the Temporal starter's OpenTracing shim links Java
 * activity spans into Python workflow traces.
 */
@AutoConfiguration(after = AppkitOtelResourceAutoConfiguration.class)
@EnableConfigurationProperties(AppkitTelemetryProperties.class)
public class AppkitTracingAutoConfiguration {

  @Bean(destroyMethod = "close")
  @ConditionalOnBean(Resource.class)
  @ConditionalOnMissingBean(name = "appkitTracerProvider")
  SdkTracerProvider appkitTracerProvider(
      Resource appkitOtelResource, AppkitTelemetryProperties telemetry) {
    return SdkTracerProvider.builder()
        .setResource(appkitOtelResource)
        .addSpanProcessor(
            BatchSpanProcessor.builder(
                    OtlpGrpcSpanExporter.builder()
                        .setEndpoint(telemetry.getOtelExporterOtlpEndpoint())
                        .build())
                .build())
        .build();
  }

  @Bean(destroyMethod = "close")
  @ConditionalOnBean(SdkTracerProvider.class)
  @ConditionalOnMissingBean(OpenTelemetry.class)
  OpenTelemetry openTelemetry(SdkTracerProvider appkitTracerProvider) {
    return OpenTelemetrySdk.builder()
        .setTracerProvider(appkitTracerProvider)
        .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
        .buildAndRegisterGlobal();
  }
}
