"""
The log codec — a structlog processor that coerces *any* value in the event
dict to a JSON-safe form, never raising.

Design mirrors Temporal's DataConverter / PayloadCodec: an ordered set of
encoding rules ending in a wire-safe representation, with a worst-case fallback
(``repr``) so a log call can never blow up the caller. This is the deliberate
leniency the schema asks for — accept more types at the logger interface, enrich
the record, and degrade gracefully rather than refuse to serialize.

Ordering of the coercion rules matters: the most specific types are handled
before the structural containers, and the ``repr`` fallback is last.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import enum
from collections.abc import Mapping, Sequence, Set
from decimal import Decimal
from typing import Any
from uuid import UUID

# Guard against pathological/cyclic structures: stop descending past this depth
# and fall back to repr. Deep enough for any realistic log payload.
_MAX_DEPTH = 6

# Scalars that json.dumps already handles natively — pass straight through.
_PASSTHROUGH = (str, bool, int, float, type(None))


def _coerce(value: Any, depth: int, seen: frozenset[int]) -> Any:
    """Return a JSON-safe rendering of ``value``. Never raises."""
    if isinstance(value, _PASSTHROUGH):
        return value

    # Cycle / depth guards — repr is the safe escape hatch.
    if depth > _MAX_DEPTH:
        return repr(value)
    obj_id = id(value)
    if obj_id in seen:
        return f"<cycle {type(value).__name__}>"

    try:
        # ── Scalars with an obvious canonical string form ──────────────────
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value.isoformat()
        if isinstance(value, datetime.timedelta):
            return value.total_seconds()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (bytes, bytearray)):
            return base64.b64encode(bytes(value)).decode("ascii")
        if isinstance(value, enum.Enum):
            return _coerce(value.value, depth + 1, seen)
        if isinstance(value, BaseException):
            return {"type": type(value).__name__, "message": str(value)}

        # ── Rich objects that know how to describe themselves ──────────────
        # pydantic BaseModel (duck-typed so obslog keeps zero hard deps).
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return _coerce(model_dump(mode="json"), depth + 1, seen)
            except TypeError:
                return _coerce(model_dump(), depth + 1, seen)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return _coerce(dataclasses.asdict(value), depth + 1, seen)

        # ── Structural containers ──────────────────────────────────────────
        nxt = seen | {obj_id}
        if isinstance(value, Mapping):
            return {str(k): _coerce(v, depth + 1, nxt) for k, v in value.items()}
        if isinstance(value, (Set, frozenset)):
            return [_coerce(v, depth + 1, nxt) for v in value]
        # str/bytes are Sequences too but were handled above.
        if isinstance(value, Sequence):
            return [_coerce(v, depth + 1, nxt) for v in value]
    except Exception:  # noqa: BLE001 — serialization must never raise.
        return repr(value)

    # ── Worst-case fallback ────────────────────────────────────────────────
    return repr(value)


def safe_serialize(_logger: Any, _method: str, event_dict: dict) -> dict:
    """structlog processor: coerce every value in the event dict to JSON-safe.

    Keys are stringified; values pass through :func:`_coerce`. Runs just before
    the JSON renderer so the renderer never meets a type it can't encode.
    """
    return {str(k): _coerce(v, 0, frozenset()) for k, v in event_dict.items()}


def json_fallback(value: Any) -> str:
    """Last-resort ``default=`` for json.dumps. Belt-and-suspenders to
    ``safe_serialize`` — should never actually fire, but guarantees the renderer
    cannot raise on an exotic type that slipped through."""
    return repr(value)
