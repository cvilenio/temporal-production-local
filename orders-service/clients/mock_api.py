import httpx


class MockApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def _post(self, path: str, payload: dict, idem_key: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers={"Idempotency-Key": idem_key},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}{path}",
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def charge_payment(self, token: str, amount: float, idem_key: str) -> dict:
        res = await self._post(
            "/payment/charge", {"token": token, "amount": amount}, idem_key
        )
        if res["status"] == "success":
            return {"success": True, "reason": None}
        return {"success": False, "reason": res.get("reason", "unknown")}

    async def reserve_inventory(
        self, item_id: str, quantity: int, idem_key: str
    ) -> dict:
        res = await self._post(
            "/inventory/reserve", {"item_id": item_id, "quantity": quantity}, idem_key
        )
        if res["status"] == "success":
            return {"success": True, "reason": None}
        return {"success": False, "reason": res.get("reason", "unknown")}

    async def create_shipment(self, address: str, order_id: str, idem_key: str) -> str:
        res = await self._post(
            "/shipping/request", {"address": address, "order_id": order_id}, idem_key
        )
        return res["tracking_id"]

    async def verify_shipment_status(self, idem_key: str) -> dict:
        res = await self._get(f"/shipping/status/{idem_key}")
        if res.get("status") == "confirmed":
            return {"confirmed": True, "tracking_id": res["tracking_id"]}
        return {"confirmed": False, "tracking_id": None}

    async def release_inventory(
        self, reservation_id: str, item_id: str, quantity: int, idem_key: str
    ) -> None:
        await self._post(
            "/inventory/release",
            {
                "reservation_id": reservation_id,
                "item_id": item_id,
                "quantity": quantity,
            },
            idem_key,
        )

    async def cancel_shipment(self, tracking_id: str, idem_key: str) -> None:
        await self._post(
            "/shipping/cancel",
            {"tracking_id": tracking_id},
            idem_key,
        )

    async def refund_payment(self, capture_id: str, amount: float, idem_key: str) -> None:
        await self._post(
            "/payment/refund",
            {"capture_id": capture_id, "amount": amount},
            idem_key,
        )
