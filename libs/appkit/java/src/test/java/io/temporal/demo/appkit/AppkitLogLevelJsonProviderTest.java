package io.temporal.demo.appkit;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class AppkitLogLevelJsonProviderTest {

  @ParameterizedTest
  @CsvSource({
    "TRACE, debug",
    "DEBUG, debug",
    "INFO, info",
    "WARN, warning",
    "ERROR, error",
    "FATAL, critical"
  })
  void mapsLevelsToLogSchema(String input, String expected) {
    assertEquals(expected, AppkitLogLevelJsonProvider.mapLevel(input));
  }
}
