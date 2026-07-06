"""Temporal client builder (ADR-0022, class 3a) — bakes in the data-converter contract.

`build_temporal_client(...)` is the *one* place a `Client` is constructed. It is generic
(names no workflow or task queue) but it is NOT a free wiring choice: it bakes in the
**data converter** — a cross-app contract (ADR-0021). If the API wired one converter and
the workers another, proto payloads would stop deserializing across the
client → workflow → activity boundary. Every app builds its client here so the converter
is, by construction, identical everywhere. Apps choose the provider *lifetime* around this
factory; they never re-decide the converter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import TLSConfig

if TYPE_CHECKING:
    from temporalio.runtime import Runtime


async def build_temporal_client(
    *,
    address: str,
    namespace: str,
    runtime: Runtime | None = None,
    interceptors: list | None = None,
    tls: bool = False,
    api_key: str | None = None,
    tls_client_cert_path: str | None = None,
    tls_client_key_path: str | None = None,
    tls_server_ca_cert_path: str | None = None,
) -> Client:
    """Connect a Temporal `Client`, baking in the shared data-converter contract.

    Connection profile (driven by Settings / env):
      Local Compose quick-start: tls=False, no auth.
      Temporal Cloud: tls=True + API key, or mTLS client cert/key (public CA).
      Self-hosted OSS on kind: tls=True + mTLS client cert/key + the server CA
        (tls_server_ca_cert_path), so the self-signed frontend cert is trusted.

    The TracingInterceptor (passed in via `interceptors`) propagates OTel span context
    across the client → workflow → activity boundary; `pydantic_data_converter` handles
    typed payload serialisation. Both are independent of the transport.
    """
    tls_config: bool | TLSConfig = tls
    if tls_client_cert_path and tls_client_key_path:
        with open(tls_client_cert_path, "rb") as cert_file:
            client_cert = cert_file.read()
        with open(tls_client_key_path, "rb") as key_file:
            client_key = key_file.read()
        server_root_ca_cert: bytes | None = None
        if tls_server_ca_cert_path:
            with open(tls_server_ca_cert_path, "rb") as ca_file:
                server_root_ca_cert = ca_file.read()
        tls_config = TLSConfig(
            client_cert=client_cert,
            client_private_key=client_key,
            server_root_ca_cert=server_root_ca_cert,
        )

    return await Client.connect(
        address,
        namespace=namespace,
        data_converter=pydantic_data_converter,
        interceptors=interceptors or [],
        runtime=runtime,
        tls=tls_config,
        api_key=api_key or None,
    )
