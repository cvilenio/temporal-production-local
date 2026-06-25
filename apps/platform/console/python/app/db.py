import asyncio
import logging
from typing import Any

import asyncpg
from asyncpg.pool import Pool

from .config import settings

logger = logging.getLogger(__name__)

# Strip asyncpg driver part for raw asyncpg
_raw_db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
pool: Pool | None = None

# The console must boot and keep serving even when the database is unreachable.
# On the kind path orders-db lives in-cluster — outside the host plane — and the
# console is expected to be running BEFORE the cluster exists (an agent brings it
# up first to watch the bring-up). So DB connectivity is best-effort: never fatal,
# always self-healing. Read paths degrade to "no orders yet" (pool is None) rather
# than erroring, and a background maintainer (re)establishes the pool when the DB
# appears or returns. See ADR-0015 (substrate-aware console) and ai_checkpoints.
_DB_TARGET = _raw_db_url.split("@")[-1]


async def _try_connect() -> bool:
    global pool
    try:
        pool = await asyncpg.create_pool(_raw_db_url, min_size=1, max_size=5)
        logger.info(f"Connected to database {_DB_TARGET}")
        return True
    except Exception as e:
        pool = None
        logger.warning(
            f"Database {_DB_TARGET} unreachable ({e!r}); retrying in background"
        )
        return False


async def init_db():
    # Non-fatal: a failed first connect must not abort console startup.
    await _try_connect()


async def maintain_pool(interval_seconds: int = 5):
    # Keep (re)establishing the pool until it exists, then idle-check. Lets the
    # console self-heal when orders-db comes up (or comes back after a teardown).
    while True:
        if pool is None:
            await _try_connect()
        await asyncio.sleep(interval_seconds)


async def close_db():
    global pool
    if pool:
        await pool.close()
        pool = None


def _drop_pool_on_conn_error() -> None:
    # A query hit a dead/severed pool (DB went away). Drop it so the maintainer
    # reconnects; read paths return empty until then.
    global pool
    pool = None


def _map_row(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "customer_message": row["customer_message"],
        "customer_message_level": row["customer_message_level"],
        "address": row["address"],
        "amount": float(row["amount"]),
        "payment_last_four": row["payment_last_four"],
        "tracking_id": row["tracking_id"],
        "store_credit_cents": row["store_credit_cents"],
        "last_reached_status": row["last_reached_status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def fetch_recent_orders(limit: int = 100) -> list[dict[str, Any]]:
    if not pool:
        return []
    query = """
        SELECT id, status, customer_message, customer_message_level, address, amount, payment_last_four, tracking_id, store_credit_cents, last_reached_status, created_at, updated_at
        FROM orders
        ORDER BY created_at DESC
        LIMIT $1
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, limit)
            return [_map_row(r) for r in rows]
    except Exception as e:
        logger.warning(f"fetch_recent_orders failed against {_DB_TARGET}: {e!r}")
        _drop_pool_on_conn_error()
        return []


async def fetch_orders_updated_after(last_ts, limit: int = 500) -> list[dict[str, Any]]:
    if not pool:
        return []
    query = """
        SELECT id, status, customer_message, customer_message_level, address, amount, payment_last_four, tracking_id, store_credit_cents, last_reached_status, created_at, updated_at
        FROM orders
        WHERE updated_at > $1
        ORDER BY updated_at ASC
        LIMIT $2
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, last_ts, limit)
            return [_map_row(r) for r in rows]
    except Exception as e:
        logger.warning(f"fetch_orders_updated_after failed against {_DB_TARGET}: {e!r}")
        _drop_pool_on_conn_error()
        return []


async def get_max_updated_at():
    if not pool:
        return None
    query = "SELECT MAX(updated_at) FROM orders"
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(query)
    except Exception as e:
        logger.warning(f"get_max_updated_at failed against {_DB_TARGET}: {e!r}")
        _drop_pool_on_conn_error()
        return None
