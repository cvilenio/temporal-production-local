package io.temporal.demo.appkit;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class AppkitDataConverterRegistryTest {

  @Test
  void defaultAndPydanticRefsResolve() {
    assertNotNull(AppkitDataConverterRegistry.resolve("default"));
    assertNotNull(AppkitDataConverterRegistry.resolve("pydantic"));
    assertNotNull(AppkitDataConverterRegistry.resolve("json"));
  }

  @Test
  void unknownRefFailsLoud() {
    assertThrows(IllegalArgumentException.class, () -> AppkitDataConverterRegistry.resolve("nope"));
  }
}
