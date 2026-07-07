package io.temporal.demo.appkit;

import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import io.temporal.spring.boot.TemporalOptionsCustomizer;
import java.util.Optional;
import javax.annotation.Nonnull;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.core.env.Environment;

/**
 * Connection profile customizer — Cloud API-key and OSS mTLS (ADR-0005).
 *
 * <p>Maps the same env vars as Python appkit: {@code TEMPORAL_TLS}, {@code TEMPORAL_API_KEY},
 * {@code TEMPORAL_TLS_CLIENT_CERT_PATH}, {@code TEMPORAL_TLS_CLIENT_KEY_PATH}, {@code
 * TEMPORAL_TLS_SERVER_CA_CERT_PATH}.
 */
@AutoConfiguration
public class AppkitConnectionAutoConfiguration {

  @Bean
  public TemporalOptionsCustomizer<WorkflowServiceStubsOptions.Builder>
      appkitWorkflowServiceStubsCustomizer(Environment env) {
    return new TemporalOptionsCustomizer<>() {
      @Nonnull
      @Override
      public WorkflowServiceStubsOptions.Builder customize(
          @Nonnull WorkflowServiceStubsOptions.Builder builder) {
        if (!isTrue(env, "TEMPORAL_TLS")) {
          return builder;
        }
        builder.setEnableHttps(true);

        String apiKey = env.getProperty("TEMPORAL_API_KEY");
        if (apiKey != null && !apiKey.isBlank()) {
          builder.addApiKey(() -> apiKey);
        }

        String certPath = env.getProperty("TEMPORAL_TLS_CLIENT_CERT_PATH");
        String keyPath = env.getProperty("TEMPORAL_TLS_CLIENT_KEY_PATH");
        String caPath = env.getProperty("TEMPORAL_TLS_SERVER_CA_CERT_PATH");
        if (certPath != null && keyPath != null && !certPath.isBlank() && !keyPath.isBlank()) {
          try {
            builder.setSslContext(AppkitSslContexts.clientContext(certPath, keyPath, caPath));
          } catch (Exception e) {
            throw new IllegalStateException("Failed to load Temporal mTLS client credentials", e);
          }
        }
        return builder;
      }
    };
  }

  private static boolean isTrue(Environment env, String key) {
    return Optional.ofNullable(env.getProperty(key))
        .map(v -> v.equalsIgnoreCase("true") || v.equals("1"))
        .orElse(false);
  }
}
