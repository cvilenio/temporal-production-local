import fs from "node:fs";
import {
  NativeConnection,
  type NativeConnectionOptions,
} from "@temporalio/worker";

export async function connectTemporal(
  address: string,
): Promise<NativeConnection> {
  const opts: NativeConnectionOptions = { address };
  if (process.env.TEMPORAL_TLS === "true") {
    const certPath = process.env.TEMPORAL_TLS_CLIENT_CERT_PATH;
    const keyPath = process.env.TEMPORAL_TLS_CLIENT_KEY_PATH;
    const caPath = process.env.TEMPORAL_TLS_SERVER_CA_CERT_PATH;
    if (!certPath || !keyPath) {
      throw new Error(
        "TEMPORAL_TLS_CLIENT_CERT_PATH and TEMPORAL_TLS_CLIENT_KEY_PATH are required when TEMPORAL_TLS=true",
      );
    }
    opts.tls = {
      clientCertPair: {
        crt: fs.readFileSync(certPath),
        key: fs.readFileSync(keyPath),
      },
    };
    if (caPath) {
      opts.tls.serverRootCACertificate = fs.readFileSync(caPath);
    }
  }
  return NativeConnection.connect(opts);
}
