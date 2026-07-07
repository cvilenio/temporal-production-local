package io.temporal.demo.appkit;

import io.temporal.common.converter.DataConverter;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;

@AutoConfiguration
@EnableConfigurationProperties(AppkitTemporalProperties.class)
public class AppkitDataConverterAutoConfiguration {

  /**
   * Primary Temporal {@link DataConverter} wired into temporal-spring-boot-starter.
   *
   * <p>Bean name {@code mainDataConverter} is required by the starter. Ref resolved from
   * {@code temporal.data-converter} / {@code TEMPORAL_DATA_CONVERTER}. Domains with a custom
   * converter register their own {@code @Primary @Bean(name = "mainDataConverter")} (see
   * samples-java {@code payloadconverter/*}).
   */
  @Bean(name = "mainDataConverter")
  @Primary
  @ConditionalOnMissingBean(name = "mainDataConverter")
  public DataConverter mainDataConverter(AppkitTemporalProperties properties) {
    return AppkitDataConverterRegistry.resolve(properties.getDataConverter());
  }
}
