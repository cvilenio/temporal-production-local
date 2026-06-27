"""Shared in-process services the mock routes depend on — the composition root (ADR-0022).

The mock has no external dependencies (no DB / Temporal / downstream); its "dependencies"
are the in-memory idempotency store and the latency simulator that every route uses, plus
the per-scenario attempt counters and the ghost-shipment cache. Kept here so the route
modules stay thin.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable

from obslog import get_logger

log = get_logger("mock-api")

# Idempotency store. Brief global guard for the cache dict + the per-key lock registry.
# NOTE: never hold cache_lock across the latency-simulating compute_fn — doing so
# serializes EVERY request behind one lock (each held for the full simulated delay),
# which under concurrent load cascades into activity timeouts.
idempotency_cache: dict[str, dict] = {}
cache_lock = asyncio.Lock()
# Per-idempotency-key locks: only requests sharing a key (genuine duplicates) serialize;
# distinct orders run concurrently.
_key_locks: dict[str, asyncio.Lock] = {}

# Ghost cache: shipping labels created but whose response was "lost".
shipping_ghost_cache: dict[str, str] = {}

# Per-key attempt counters for the flaky/ghost demo scenarios.
shipping_attempts: dict[str, int] = {}
inventory_attempts: dict[str, int] = {}


async def with_idempotency(
    idem_key: str | None, compute_fn: Callable[[], Awaitable[dict]]
) -> dict:
    if not idem_key:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    # Fast path + grab a per-key lock, all under a brief global guard.
    async with cache_lock:
        if idem_key in idempotency_cache:
            return idempotency_cache[idem_key]
        key_lock = _key_locks.setdefault(idem_key, asyncio.Lock())

    # Serialize only same-key (duplicate) requests; the slow compute_fn runs WITHOUT the
    # global lock so distinct orders proceed concurrently.
    async with key_lock:
        async with cache_lock:
            if idem_key in idempotency_cache:
                return idempotency_cache[idem_key]

        response = await compute_fn()

        async with cache_lock:
            idempotency_cache[idem_key] = response

    return response


async def simulate_latency(base_ms: int) -> None:
    if base_ms > 0:
        # +/- 20% jitter
        jitter = random.uniform(0.8, 1.2)
        actual_ms = int(base_ms * jitter)
        log.debug("simulating response latency", actual_ms=actual_ms, base_ms=base_ms)
        await asyncio.sleep(actual_ms / 1000.0)
