package io.temporal.demo.appkit;

import io.temporal.common.WorkerDeploymentVersion;
import io.temporal.spring.boot.WorkerOptionsCustomizer;
import io.temporal.worker.WorkerDeploymentOptions;
import io.temporal.worker.WorkerOptions;
import javax.annotation.Nonnull;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.core.env.Environment;

/** Worker Deployment versioning from controller-injected env (ADR-0004). */
@AutoConfiguration
public class AppkitWorkerVersioningAutoConfiguration {

  @Bean
  public WorkerOptionsCustomizer appkitWorkerVersioningCustomizer(Environment env) {
    return new WorkerOptionsCustomizer() {
      @Nonnull
      @Override
      public WorkerOptions.Builder customize(
          @Nonnull WorkerOptions.Builder optionsBuilder,
          @Nonnull String workerName,
          @Nonnull String taskQueue) {
        String deploymentName = env.getProperty("TEMPORAL_DEPLOYMENT_NAME");
        String buildId = env.getProperty("TEMPORAL_WORKER_BUILD_ID");
        if (deploymentName == null
            || deploymentName.isBlank()
            || buildId == null
            || buildId.isBlank()) {
          return optionsBuilder;
        }
        WorkerDeploymentVersion version =
            new WorkerDeploymentVersion(deploymentName, buildId);
        return optionsBuilder.setDeploymentOptions(
            WorkerDeploymentOptions.newBuilder()
                .setUseVersioning(true)
                .setVersion(version)
                .build());
      }
    };
  }
}
