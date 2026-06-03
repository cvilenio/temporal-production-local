import httpx
from temporalio.exceptions import ApplicationError

class OrdersServiceClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def _request(self, method: str, path: str, json: dict = None) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                timeout=10.0,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if 400 <= e.response.status_code < 500:
                    raise ApplicationError(
                        f"Client error from orders service: {e.response.text}",
                        type="HTTPClientError",
                        non_retryable=True,
                    ) from e
                raise
            return response.json() if response.content else {"ok": True}

    async def ensure_order(self, order_data: dict) -> None:
        await self._request("POST", "/internal/orders/ensure", json=order_data)

    async def update_customer_status(self, order_id: str, payload: dict) -> None:
        await self._request("PATCH", f"/orders/{order_id}/customer-status", json=payload)

    async def persist_inventory_reservation(self, order_id: str, reservation_id: str) -> None:
        await self._request("PATCH", f"/internal/orders/{order_id}/inventory-reservation", json={"reservation_id": reservation_id})

    async def persist_shipment(self, order_id: str, tracking_id: str) -> None:
        await self._request("PATCH", f"/internal/orders/{order_id}/shipment", json={"tracking_id": tracking_id})

    async def persist_payment_capture(self, order_id: str, capture_id: str) -> None:
        await self._request("PATCH", f"/internal/orders/{order_id}/payment-capture", json={"capture_id": capture_id})

    async def mark_order_failed(self, order_id: str, payload: dict) -> None:
        await self._request("POST", f"/orders/{order_id}/fail", json=payload)

    async def finalize_order(self, order_id: str) -> None:
        await self._request("POST", f"/internal/orders/{order_id}/finalize")
