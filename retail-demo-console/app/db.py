import logging
from typing import List, Dict, Any, Optional
import asyncpg
from asyncpg.pool import Pool

from .config import settings

logger = logging.getLogger(__name__)

# Strip asyncpg driver part for raw asyncpg
_raw_db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
pool: Optional[Pool] = None


async def init_db():
    global pool
    logger.info(f"Connecting to database {_raw_db_url.split('@')[-1]}")
    pool = await asyncpg.create_pool(_raw_db_url, min_size=1, max_size=5)


async def close_db():
    global pool
    if pool:
        await pool.close()


def _map_row(row: asyncpg.Record) -> Dict[str, Any]:
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


async def fetch_recent_orders(limit: int = 100) -> List[Dict[str, Any]]:
    if not pool:
        return []
    query = """
        SELECT id, status, customer_message, customer_message_level, address, amount, payment_last_four, tracking_id, store_credit_cents, last_reached_status, created_at, updated_at
        FROM orders
        ORDER BY created_at DESC
        LIMIT $1
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, limit)
        return [_map_row(r) for r in rows]


async def fetch_orders_updated_after(last_ts, limit: int = 500) -> List[Dict[str, Any]]:
    if not pool:
        return []
    query = """
        SELECT id, status, customer_message, customer_message_level, address, amount, payment_last_four, tracking_id, store_credit_cents, last_reached_status, created_at, updated_at
        FROM orders
        WHERE updated_at > $1
        ORDER BY updated_at ASC
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, last_ts, limit)
        return [_map_row(r) for r in rows]


async def get_max_updated_at():
    if not pool:
        return None
    query = "SELECT MAX(updated_at) FROM orders"
    async with pool.acquire() as conn:
        return await conn.fetchval(query)
