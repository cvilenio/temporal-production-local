package io.temporal.demo.appkit;

import static org.junit.jupiter.api.Assertions.assertEquals;

import ch.qos.logback.classic.spi.LoggingEvent;
import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.StringWriter;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AppkitAttemptJsonProviderTest {

  @Test
  void writesAttemptAsJsonInteger() throws Exception {
    LoggingEvent event = new LoggingEvent();
    event.setMDCPropertyMap(Map.of("attempt", "3"));

    StringWriter writer = new StringWriter();
    try (JsonGenerator generator = new JsonFactory().createGenerator(writer)) {
      generator.writeStartObject();
      new AppkitAttemptJsonProvider().writeTo(generator, event);
      generator.writeEndObject();
    }

    assertEquals(3, new ObjectMapper().readTree(writer.toString()).get("attempt").asInt());
  }
}
