package io.temporal.demo.appkit;

import io.temporal.common.converter.DataConverter;
import io.temporal.common.converter.DefaultDataConverter;

/**
 * Resolves symbolic data-converter refs to Temporal {@link DataConverter} instances.
 *
 * <p>{@code default} uses {@link DefaultDataConverter#newDefaultInstance()} — Jackson {@code
 * json/plain} encoding, wire-compatible with Python's {@code pydantic_data_converter} for JSON
 * payloads (ADR-0021). Proto payloads use the built-in proto converters on both sides.
 */
public final class AppkitDataConverterRegistry {

  private AppkitDataConverterRegistry() {}

  public static DataConverter resolve(String ref) {
    String name = ref == null || ref.isBlank() ? "default" : ref.trim();
    return switch (name) {
      case "default", "pydantic", "json" -> DefaultDataConverter.newDefaultInstance();
      default ->
          throw new IllegalArgumentException(
              "unknown temporal.data-converter "
                  + ref
                  + " — add a resolver in AppkitDataConverterRegistry or set temporal.data-converter: default");
    };
  }
}
