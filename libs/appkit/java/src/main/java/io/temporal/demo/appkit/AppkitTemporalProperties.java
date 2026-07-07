package io.temporal.demo.appkit;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Appkit Temporal settings — mirrors Python {@code TemporalConnectionSettings} env mapping.
 *
 * <p>The data-converter ref is injected at deploy time as {@code TEMPORAL_DATA_CONVERTER} (from
 * {@code config/domains/*.yaml}); it is resolved fail-loud at startup, never read from disk at
 * runtime (ADR-0026).
 */
@ConfigurationProperties(prefix = "temporal")
public class AppkitTemporalProperties {

  /** Symbolic converter ref: {@code default} | {@code pydantic} | {@code json} | custom (future). */
  private String dataConverter = "default";

  public String getDataConverter() {
    return dataConverter;
  }

  public void setDataConverter(String dataConverter) {
    this.dataConverter = dataConverter;
  }
}
