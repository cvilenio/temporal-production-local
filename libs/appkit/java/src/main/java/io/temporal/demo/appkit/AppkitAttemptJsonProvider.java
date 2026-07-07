package io.temporal.demo.appkit;

import ch.qos.logback.classic.spi.ILoggingEvent;
import com.fasterxml.jackson.core.JsonGenerator;
import java.io.IOException;
import net.logstash.logback.composite.AbstractFieldJsonProvider;
import net.logstash.logback.composite.JsonWritingUtils;

/** Emits {@code attempt} as a JSON integer per {@code log-schema.json}. */
public class AppkitAttemptJsonProvider extends AbstractFieldJsonProvider<ILoggingEvent> {

  public AppkitAttemptJsonProvider() {
    setFieldName("attempt");
  }

  @Override
  public void writeTo(JsonGenerator generator, ILoggingEvent event) throws IOException {
    String attempt = event.getMDCPropertyMap().get("attempt");
    if (attempt == null || attempt.isBlank()) {
      return;
    }
    JsonWritingUtils.writeNumberField(generator, getFieldName(), Integer.parseInt(attempt));
  }
}
