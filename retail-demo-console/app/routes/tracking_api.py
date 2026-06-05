import asyncio
import json

import httpx
from app import db
from app.config import settings
from app.order_client import fetch_order as http_fetch_order
from app.sse import broker
from app.submission_log import submission_log
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/tracking")


@router.get("/orders")
async def get_orders(limit: int = Query(100)):
    rows = await db.fetch_recent_orders(limit)
    return rows


@router.get("/batches")
async def get_batches():
    entries = await submission_log.get_all()
    batches = []
    for entry in entries:
        results = entry.get("results", [])
        order_ids = [
            res["order_id"] for res in results if res.get("ok") and res.get("order_id")
        ]
        order_labels = {
            res["order_id"]: res["label"]
            for res in results
            if res.get("ok") and res.get("order_id")
        }

        batches.append(
            {
                "batch_id": entry.get("batch_id"),
                "timestamp_utc": entry.get("timestamp_utc"),
                "summary": entry.get("summary"),
                "triggered": entry.get("triggered"),
                "failed": entry.get("failed"),
                "order_ids": order_ids,
                "order_labels": order_labels,
            }
        )
    return batches


@router.get("/orders/{order_id}")
async def get_single_order(order_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        row = await http_fetch_order(client, settings.orders_service_url, order_id)

    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


@router.get("/stream")
async def stream_order_updates(request: Request):
    async def event_generator():
        q = await broker.subscribe()
        try:
            while True:
                # Wait for new data or heartbeat interval
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except TimeoutError:
                    # Keep connection alive
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broker.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
