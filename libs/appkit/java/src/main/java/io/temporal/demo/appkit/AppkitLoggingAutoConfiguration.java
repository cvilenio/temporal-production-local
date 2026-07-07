package io.temporal.demo.appkit;

import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

/** Registers the activity MDC {@link WorkerInterceptor} bean (auto-picked up by the starter). */
@AutoConfiguration
@EnableConfigurationProperties(AppkitTelemetryProperties.class)
public class AppkitLoggingAutoConfiguration {

  @Bean
  AppkitActivityLoggingWorkerInterceptor appkitActivityLoggingWorkerInterceptor() {
    return new AppkitActivityLoggingWorkerInterceptor();
  }
}
