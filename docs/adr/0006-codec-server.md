# ADR-0006: Standalone codec server + data-converter encryption

- **Status:** Accepted (scaffold)
- **Date:** 2026-06-23

## Context

Production Temporal deployments encrypt Payloads (PII, order details) so they are opaque in
Event History and in transit to Temporal Cloud. Operators still need to read histories,
which requires a **remote codec server** the Temporal UI/CLI call to decode payloads. The
demo previously excluded this entirely.

## Decision

Add a standalone, "temporal-adjacent" app `apps/codec-server/` exposing the remote-codec
HTTP contract (`POST /encode`, `POST /decode`). It ships as a **scaffold** with a reversible
placeholder codec so the round-trip is demonstrable end-to-end. The matching `PayloadCodec`
is intended to be installed in the workers' data converter so payloads are encrypted at the
source.

## Consequences

- The codec server is deployable on compose (port 8085) and as a Helm chart on kind.
- **Before any real use:** replace the placeholder with an AEAD codec (e.g. AES-256-GCM with
  a per-namespace key from a secret/KMS), wire the same codec into the worker data converter
  (alongside `pydantic_data_converter`), and lock CORS to the Temporal UI origin.
- Pairs with ADR-0005: encryption is independent of transport TLS.
