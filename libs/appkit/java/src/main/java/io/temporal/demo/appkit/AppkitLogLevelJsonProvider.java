package io.temporal.demo.appkit;

import ch.qos.logback.classic.spi.ILoggingEvent;
import com.fasterxml.jackson.core.JsonGenerator;
import java.io.IOException;
import java.util.Locale;
import net.logstash.logback.composite.AbstractFieldJsonProvider;
import net.logstash.logback.composite.JsonWritingUtils;

/**
 * Emits {@code level} per {@code log-schema.json}: lowercase enum
 * debug|info|warning|error|critical.
 */
public class AppkitLogLevelJsonProvider extends AbstractFieldJsonProvider<ILoggingEvent> {

  public AppkitLogLevelJsonProvider() {
    setFieldName("level");
  }

  @Override
  public void writeTo(JsonGenerator generator, ILoggingEvent event) throws IOException {
    JsonWritingUtils.writeStringField(generator, getFieldName(), mapLevel(event.getLevel().toString()));
  }

  static String mapLevel(String level) {
    return switch (level.toUpperCase(Locale.ROOT)) {
      case "TRACE", "DEBUG" -> "debug";
      case "INFO" -> "info";
      case "WARN" -> "warning";
      case "ERROR" -> "error";
      case "FATAL" -> "critical";
      default -> level.toLowerCase(Locale.ROOT);
    };
  }
}
