import asyncio
import hashlib
import json
import random
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Body

from ..config import settings
from ..order_client import submit_order
from ..scenarios import SCENARIOS
from ..submission_log import submission_log

router = APIRouter(prefix="/api")


@router.post("/submit-batch")
async def submit_batch(payload: dict[str, dict[str, int]] = Body(...)):
    counts = payload.get("counts", {})
    batch_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()

    tasks = []
    scenarios_run = []

    async with httpx.AsyncClient(
        timeout=settings.orders_service_timeout_seconds
    ) as client:
        for scenario_key, count in counts.items():
            if count <= 0 or scenario_key not in SCENARIOS:
                continue

            scenario_def = SCENARIOS[scenario_key]

            for _ in range(count):
                task_payload = scenario_def["payload"].copy()
                task_payload["user_id"] = str(uuid.uuid4())
                trace_id = uuid.uuid4().hex
                task_payload["trace_id"] = trace_id
                task_payload["payment_authorization_id"] = f"PAUTH-{uuid.uuid4().hex}"

                # Randomize order amount to vary between orders
                random_dollars = random.randint(10, 150)
                random_cents = random.choice([0.99, 0.49, 0.00, 0.50])
                task_payload["amount"] = round(random_dollars + random_cents, 2)

                # Compute cart_version
                # Note: must exclude cart_version itself.
                canonical_body = json.dumps(
                    task_payload, sort_keys=True, separators=(",", ":")
                )
                cart_version = hashlib.sha256(
                    canonical_body.encode("utf-8")
                ).hexdigest()
                task_payload["cart_version"] = cart_version

                idem_key = str(uuid.uuid4())

                async def run_scenario(sk, sd, tp, ikey):
                    res = await submit_order(
                        client, settings.orders_service_url, tp, ikey
                    )
                    return {"scenario": sk, "label": sd["label"], "result": res}

                tasks.append(
                    run_scenario(scenario_key, scenario_def, task_payload, idem_key)
                )
                scenarios_run.append(
                    {
                        "scenario": scenario_key,
                        "payload": task_payload,
                        "idem_key": idem_key,
                    }
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

    triggered = 0
    failed = 0
    formatted_results = []

    for r in results:
        if isinstance(r, BaseException):
            failed += 1
            formatted_results.append({"label": "Unknown", "ok": False, "error": str(r)})
        else:
            if r["result"].get("ok"):
                triggered += 1
            else:
                failed += 1
            formatted_results.append(
                {
                    "label": r["label"],
                    "ok": r["result"].get("ok"),
                    "order_id": r["result"].get("order_id"),
                    "error": r["result"].get("error"),
                }
            )

    # Summary
    counts_summary = ", ".join(
        f"{c}× {SCENARIOS[k]['label']}"
        for k, c in counts.items()
        if c > 0 and k in SCENARIOS
    )

    # Store the actual tasks so they can be replayed
    replays = [
        {"scenario": t["scenario"], "payload": t["payload"], "idem_key": t["idem_key"]}
        for t in scenarios_run
    ]

    entry = {
        "batch_id": batch_id,
        "timestamp_utc": timestamp,
        "summary": counts_summary,
        "triggered": triggered,
        "failed": failed,
        "results": formatted_results,
        "replays": replays,
    }

    await submission_log.append_batch(entry)

    return entry


@router.post("/replay-batch")
async def replay_batch(payload: dict[str, Any] = Body(...)):
    replays = payload.get("replays", [])
    batch_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()

    tasks = []

    async with httpx.AsyncClient(
        timeout=settings.orders_service_timeout_seconds
    ) as client:
        for r in replays:
            sk = r["scenario"]
            sd = SCENARIOS.get(sk, {"label": "Unknown"})
            tp = r["payload"]
            ikey = r["idem_key"]

            async def run_scenario(sk, sd, tp, ikey):
                res = await submit_order(client, settings.orders_service_url, tp, ikey)
                return {"scenario": sk, "label": sd["label"], "result": res}

            tasks.append(run_scenario(sk, sd, tp, ikey))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    triggered = 0
    failed = 0
    formatted_results = []

    for r in results:
        if isinstance(r, BaseException):
            failed += 1
            formatted_results.append({"label": "Unknown", "ok": False, "error": str(r)})
        else:
            if r["result"].get("ok"):
                triggered += 1
            else:
                failed += 1
            formatted_results.append(
                {
                    "label": r["label"],
                    "ok": r["result"].get("ok"),
                    "order_id": r["result"].get("order_id"),
                    "error": r["result"].get("error"),
                }
            )

    entry = {
        "batch_id": batch_id,
        "timestamp_utc": timestamp,
        "summary": f"Replay ({len(replays)} orders)",
        "triggered": triggered,
        "failed": failed,
        "results": formatted_results,
        "replays": replays,
    }

    await submission_log.append_batch(entry)

    return entry


@router.post("/cancel-batch")
async def cancel_batch(payload: dict[str, Any] = Body(...)):
    order_ids = payload.get("order_ids", [])
    if not order_ids:
        return {"requested": 0, "skipped": 0}

    async with httpx.AsyncClient(
        timeout=settings.orders_service_timeout_seconds
    ) as client:
        response = await client.post(
            f"{settings.orders_service_url.rstrip('/')}/orders/cancel-batch",
            json={"order_ids": order_ids},
        )
        response.raise_for_status()
        return response.json()


@router.get("/submission-log")
async def get_submission_log():
    return await submission_log.get_all()
