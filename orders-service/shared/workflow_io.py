from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal

class OrderWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    order_id: str
    item_id: str
    quantity: int
    user_id: str
    address: str
    payment_authorization_id: str
    amount: float
    trace_id: Optional[str] = None

class OrderWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    status: Literal["Success", "Cancelled", "Failed - Shipping"]
    order_id: str
    tracking_id: Optional[str] = None
    trace_id: Optional[str] = None
