# ADR-0005: Temporal connection profiles (local ↔ Cloud)

- **Status:** Accepted
- **Date:** 2026-06-23

## Context

The same application must connect to a self-hosted server (no TLS, no auth) or to Temporal
Cloud (TLS + API key or mTLS), selected without code changes. The codebase previously had
no TLS/auth support at all.

## Decision

Extend the single `Settings` object (one-stop config) with
`temporal_tls`, `temporal_api_key`, and `temporal_tls_client_cert_path` /
`_key_path`. `TemporalService.connect()` builds `tls` (bool or `TLSConfig`) and `api_key`
from these and passes them to `Client.connect`. Local defaults keep TLS off; Cloud is opt-in
via env. Profile bundles live in `config/`; credentials are git-ignored.

*(Post-[ADR-0022](0022-domain-core-vs-application-composition-boundary.md): these fields are
now the reusable `appkit.TemporalConnectionSettings` field-group each app composes, and the
client is built by `appkit.build_temporal_client` — which also bakes in the data-converter
contract; `TemporalService` is now a thin domain wrapper over an already-connected client.)*

## Consequences

- Backend switch is an env/profile change; no code edits, no rebuild.
- mTLS and API-key auth are both supported (API key is the simpler Cloud path).
- The active credential file (`guts.<account-id>.txt`) and any `*.key`/`*.pem` are excluded from
  git.
