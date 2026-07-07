package io.temporal.demo.appkit;

import io.temporal.client.WorkflowClientOptions;
import io.temporal.spring.boot.TemporalOptionsCustomizer;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.Optional;
import javax.annotation.Nonnull;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.core.env.Environment;

/**
 * Worker identity from deployment name (ADR-0028).
 *
 * <p>Identity is {@code <deployment-name>@<host>}, using {@code TEMPORAL_DEPLOYMENT_NAME} when the
 * Worker Controller injects it, else {@code appkit.worker.default-deployment-name} for the
 * host/OSS path.
 */
@AutoConfiguration
public class AppkitWorkerIdentityAutoConfiguration {

  @Bean
  public TemporalOptionsCustomizer<WorkflowClientOptions.Builder>
      appkitWorkflowClientIdentityCustomizer(Environment env) {
    return new TemporalOptionsCustomizer<>() {
      @Nonnull
      @Override
      public WorkflowClientOptions.Builder customize(
          @Nonnull WorkflowClientOptions.Builder builder) {
        String deploymentName = env.getProperty("TEMPORAL_DEPLOYMENT_NAME");
        if (deploymentName == null || deploymentName.isBlank()) {
          deploymentName = env.getProperty("appkit.worker.default-deployment-name");
        }
        if (deploymentName == null || deploymentName.isBlank()) {
          return builder;
        }
        String host =
            Optional.ofNullable(env.getProperty("HOSTNAME"))
                .filter(h -> !h.isBlank())
                .orElseGet(AppkitWorkerIdentityAutoConfiguration::localHostName);
        return builder.setIdentity(deploymentName + "@" + host);
      }
    };
  }

  private static String localHostName() {
    try {
      return InetAddress.getLocalHost().getHostName();
    } catch (UnknownHostException e) {
      return "localhost";
    }
  }
}
