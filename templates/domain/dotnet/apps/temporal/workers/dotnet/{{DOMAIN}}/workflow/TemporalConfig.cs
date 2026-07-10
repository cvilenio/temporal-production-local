using System.Text.Json;
using Temporalio.Activities;
using Temporalio.Converters;

namespace {{Domain}}Demo;

public static class TemporalConfig
{
    public static TemporalSettings Load(string taskQueue)
    {
        var deploymentName = Environment.GetEnvironmentVariable("TEMPORAL_DEPLOYMENT_NAME");
        var buildId = Environment.GetEnvironmentVariable("TEMPORAL_WORKER_BUILD_ID");
        if (string.IsNullOrEmpty(deploymentName))
        {
            throw new InvalidOperationException("TEMPORAL_DEPLOYMENT_NAME is required");
        }

        if (string.IsNullOrEmpty(buildId))
        {
            throw new InvalidOperationException("TEMPORAL_WORKER_BUILD_ID is required");
        }

        return new TemporalSettings(
            Address: Environment.GetEnvironmentVariable("TEMPORAL_ADDRESS") ?? "localhost:7233",
            Namespace: Environment.GetEnvironmentVariable("TEMPORAL_NAMESPACE") ?? "{{DOMAIN}}",
            TaskQueue: taskQueue,
            DeploymentName: deploymentName,
            BuildId: buildId,
            TlsEnabled: Environment.GetEnvironmentVariable("TEMPORAL_TLS") == "true",
            ClientCertPath: Environment.GetEnvironmentVariable("TEMPORAL_TLS_CLIENT_CERT_PATH"),
            ClientKeyPath: Environment.GetEnvironmentVariable("TEMPORAL_TLS_CLIENT_KEY_PATH"),
            ServerCaCertPath: Environment.GetEnvironmentVariable("TEMPORAL_TLS_SERVER_CA_CERT_PATH"));
    }

    public static async Task<Temporalio.Client.ITemporalClient> ConnectAsync(TemporalSettings cfg)
    {
        var options = new Temporalio.Client.TemporalClientConnectOptions(cfg.Address)
        {
            Namespace = cfg.Namespace,
            DataConverter = DataConverter.Default with
            {
                PayloadConverter = new CamelCasePayloadConverter(),
            },
        };

        if (cfg.TlsEnabled)
        {
            var tls = new Temporalio.Client.TlsOptions();
            if (!string.IsNullOrEmpty(cfg.ClientCertPath) && !string.IsNullOrEmpty(cfg.ClientKeyPath))
            {
                tls.ClientCert = await File.ReadAllBytesAsync(cfg.ClientCertPath);
                tls.ClientPrivateKey = await File.ReadAllBytesAsync(cfg.ClientKeyPath);
            }

            if (!string.IsNullOrEmpty(cfg.ServerCaCertPath))
            {
                tls.ServerRootCACert = await File.ReadAllBytesAsync(cfg.ServerCaCertPath);
            }

            options.Tls = tls;
        }

        return await Temporalio.Client.TemporalClient.ConnectAsync(options);
    }
}

public sealed record TemporalSettings(
    string Address,
    string Namespace,
    string TaskQueue,
    string DeploymentName,
    string BuildId,
    bool TlsEnabled,
    string? ClientCertPath,
    string? ClientKeyPath,
    string? ServerCaCertPath);
