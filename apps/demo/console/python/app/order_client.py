from typing import Any

import httpx


async def submit_order(
    client: httpx.AsyncClient, base_url: str, payload: dict[str, Any], idem_key: str
) -> dict[str, Any]:
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/submit-order",
            json=payload,
            headers={"X-Idempotency-Key": idem_key},
        )
        response.raise_for_status()
        data = response.json()
        return {
            "ok": True,
            "status_code": response.status_code,
            "order_id": data.get("order_id"),
            "workflow_id": data.get("workflow_id"),
        }
    except httpx.HTTPStatusError as e:
        return {"ok": False, "status_code": e.response.status_code, "error": str(e)}
    except Exception as e:
        return {"ok": False, "status_code": None, "error": str(e)}


async def fetch_order(
    client: httpx.AsyncClient, base_url: str, order_id: str
) -> dict[str, Any] | None:
    try:
        response = await client.get(f"{base_url.rstrip('/')}/orders/{order_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None
