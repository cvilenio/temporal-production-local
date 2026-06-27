"""Mock payment endpoints — all under the /payment prefix."""

from dependencies import simulate_latency, with_idempotency
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from settings import settings

router = APIRouter(prefix="/payment")


class PaymentChargeRequest(BaseModel):
    token: str
    amount: float


class PaymentRefundRequest(BaseModel):
    capture_id: str
    amount: float


@router.post("/charge")
async def charge_payment(
    request: PaymentChargeRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header missing")

    async def compute() -> dict:
        await simulate_latency(settings.mock_payment_latency_ms)
        return {"status": "success"}

    return await with_idempotency(idempotency_key, compute)


@router.post("/refund")
async def refund_payment(
    request: PaymentRefundRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    async def compute() -> dict:
        await simulate_latency(settings.mock_payment_latency_ms)
        return {"status": "success"}

    return await with_idempotency(idempotency_key, compute)
