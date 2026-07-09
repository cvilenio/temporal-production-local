"""Temporal Remote Codec Server (scaffold).

A standalone HTTP service that the Temporal Web UI and CLI call to decode
(and encode) Payloads so operators can read otherwise-encrypted Event History.
It exposes the two endpoints the remote-codec protocol requires:

    POST /encode   { "payloads": [ <Payload>, ... ] }  ->  { "payloads": [...] }
    POST /decode   { "payloads": [ <Payload>, ... ] }  ->  { "payloads": [...] }

A <Payload> is the JSON form of a temporalio Payload: base64-encoded `metadata`
values and base64-encoded `data`.

SCAFFOLD STATUS
---------------
The codec here is a reversible XOR-with-static-key placeholder so the round-trip
is demonstrable end-to-end. Before any real use, replace `DemoCodec` with a
proper AEAD codec (e.g. AES-256-GCM with a per-namespace key from a KMS/secret)
and lock CORS down to the Temporal UI origin. The same PayloadCodec must also be
installed in the workers' data converter so payloads are encrypted at the source
(see ADR-0006 / docs/ARCHITECTURE.md). When wiring a real codec, resolve the
(when deployed, via TEMPORAL_DATA_CONVERTER env from the descriptor at deploy time;
Phase B console may use appkit.data_converter_for_domain when descriptors are mounted).
"""

from __future__ import annotations

import base64
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from obslog import get_logger, init_logging
from pydantic import BaseModel

log = get_logger("codec-server")

# Origin allowed to call the codec from a browser (the Temporal UI). Lock this
# down per environment; "*" here only because this is a local demo scaffold.
UI_ORIGIN = os.getenv("CODEC_UI_ORIGIN", "*")
# Placeholder key. Replace the whole codec with AEAD before real use.
_DEMO_KEY = os.getenv("CODEC_DEMO_KEY", "local-demo-key").encode()


def _xor(data: bytes) -> bytes:
    return bytes(b ^ _DEMO_KEY[i % len(_DEMO_KEY)] for i, b in enumerate(data))


class Payload(BaseModel):
    metadata: dict[str, str] = {}  # base64-encoded values
    data: str = ""  # base64-encoded bytes


class Payloads(BaseModel):
    payloads: list[Payload] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Host-plane scaffold: structured JSON to stdout (Docker Desktop).
    init_logging(
        os.getenv("OTEL_SERVICE_NAME", "codec-server"),
        level=os.getenv("LOG_LEVEL", "INFO"),
        fmt=os.getenv("LOG_FORMAT", "json"),
        instance_id=os.getenv("HOSTNAME") or socket.gethostname(),
    )
    log.info("codec server up", ui_origin=UI_ORIGIN)
    yield


app = FastAPI(title="Temporal Codec Server (scaffold)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[UI_ORIGIN],
    allow_methods=["POST"],
    allow_headers=["content-type", "x-namespace"],
)


def _transform(payloads: list[Payload]) -> list[Payload]:
    out: list[Payload] = []
    for p in payloads:
        raw = base64.b64decode(p.data) if p.data else b""
        transformed = base64.b64encode(_xor(raw)).decode()
        out.append(Payload(metadata=p.metadata, data=transformed))
    return out


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/encode")
async def encode(body: Payloads) -> Payloads:
    return Payloads(payloads=_transform(body.payloads))


@app.post("/decode")
async def decode(body: Payloads) -> Payloads:
    # XOR is its own inverse, so decode == encode for this placeholder codec.
    return Payloads(payloads=_transform(body.payloads))
