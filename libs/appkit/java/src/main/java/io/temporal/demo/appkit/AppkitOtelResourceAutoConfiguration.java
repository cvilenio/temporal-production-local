package io.temporal.demo.appkit;

import io.opentelemetry.sdk.resources.Resource;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

@AutoConfiguration
@EnableConfigurationProperties(AppkitTelemetryProperties.class)
public class AppkitOtelResourceAutoConfiguration {

  @Bean
  @ConditionalOnMissingBean(name = "appkitOtelResource")
  Resource appkitOtelResource(AppkitTelemetryProperties telemetry) {
    return AppkitOtelResource.build(telemetry);
  }
}
