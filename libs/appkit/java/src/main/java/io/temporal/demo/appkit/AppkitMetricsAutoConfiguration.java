package io.temporal.demo.appkit;

import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.exporter.otlp.metrics.OtlpGrpcMetricExporter;
import io.opentelemetry.sdk.metrics.InstrumentType;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.metrics.data.AggregationTemporality;
import io.opentelemetry.sdk.metrics.export.AggregationTemporalitySelector;
import io.opentelemetry.sdk.metrics.export.PeriodicMetricReader;
import io.opentelemetry.sdk.resources.Resource;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

/**
 * Business metrics (OTLP push, DELTA temporality) — separate from SDK Prometheus pull metrics.
 */
@AutoConfiguration(after = AppkitOtelResourceAutoConfiguration.class)
@EnableConfigurationProperties(AppkitTelemetryProperties.class)
public class AppkitMetricsAutoConfiguration {

  @Bean(destroyMethod = "close")
  @ConditionalOnBean(Resource.class)
  @ConditionalOnMissingBean(name = "businessMeterProvider")
  SdkMeterProvider businessMeterProvider(
      Resource appkitOtelResource, AppkitTelemetryProperties telemetry) {
    return SdkMeterProvider.builder()
        .setResource(appkitOtelResource)
        .registerMetricReader(
            PeriodicMetricReader.builder(
                    OtlpGrpcMetricExporter.builder()
                        .setEndpoint(telemetry.resolvedMetricsEndpoint())
                        .setAggregationTemporalitySelector(AppkitMetricsAutoConfiguration::deltaTemporality)
                        .build())
                .setInterval(java.time.Duration.ofSeconds(15))
                .build())
        .build();
  }

  @Bean
  @ConditionalOnBean(SdkMeterProvider.class)
  @ConditionalOnMissingBean(name = "businessMeter")
  Meter businessMeter(SdkMeterProvider businessMeterProvider) {
    return businessMeterProvider.get("ziggymart.business");
  }

  /** Mirrors Python appkit {@code _DELTA_TEMPORALITY} (ADR-0024). */
  static AggregationTemporality deltaTemporality(InstrumentType type) {
    if (type == InstrumentType.COUNTER
        || type == InstrumentType.HISTOGRAM
        || type == InstrumentType.OBSERVABLE_COUNTER) {
      return AggregationTemporalitySelector.deltaPreferred().getAggregationTemporality(type);
    }
    return AggregationTemporalitySelector.alwaysCumulative().getAggregationTemporality(type);
  }
}
