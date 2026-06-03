import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Set
from .config import settings
from . import db

logger = logging.getLogger(__name__)


class Broker:
    def __init__(self):
        self.connections: Set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.connections.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.connections:
            self.connections.remove(q)

    async def broadcast(self, message: Dict[str, Any]):
        if not self.connections:
            return

        for q in self.connections:
            try:
                q.put_nowait(message)
            except Exception as e:
                logger.error(f"Error broadcasting to a connection: {e}")


broker = Broker()


async def poll_order_updates():
    """
    Background task that polls the database for updated orders
    and broadcasts them to all connected SSE clients.
    """
    logger.info("Starting order updates poller")

    # Initialize high-water mark
    last_seen_ts = None
    try:
        last_seen_ts = await db.get_max_updated_at()
    except Exception as e:
        logger.error(f"Failed to get max updated_at: {e}")

    if not last_seen_ts:
        # Fallback to now if DB is empty
        last_seen_ts = datetime.now(timezone.utc)

    logger.info(f"Initialized order poller at {last_seen_ts}")

    while True:
        await asyncio.sleep(settings.order_poll_interval_seconds)
        try:
            orders = await db.fetch_orders_updated_after(last_seen_ts)

            if orders:
                for row in orders:
                    await broker.broadcast(row)

                # Parse the ISO format string back to datetime to use as arg
                last_seen_ts = datetime.fromisoformat(orders[-1]["updated_at"])

        except Exception as e:
            logger.error(f"Error polling orders: {e}")
