using System.Text.Json;
using Temporalio.Converters;

namespace {{Domain}}Demo;

/// <summary>
/// Cross-SDK payload interop: other SDK templates and the console sample_inputs catalog
/// emit camelCase JSON ({"name":...}). System.Text.Json defaults are PascalCase and
/// case-sensitive — use this converter when interoperating with Python/Go/TS/Java/Ruby starters.
/// </summary>
public class CamelCasePayloadConverter : DefaultPayloadConverter
{
    public CamelCasePayloadConverter()
        : base(new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = true,
        })
    {
    }
}
