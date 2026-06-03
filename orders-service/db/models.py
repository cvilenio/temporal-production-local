from sqlalchemy import (
    Integer,
    Numeric,
    Text,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # State
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    payment_authorization_id: Mapped[str] = mapped_column(Text, nullable=True)
    payment_last_four: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_id: Mapped[str] = mapped_column(Text, nullable=True)
    tracking_id: Mapped[str] = mapped_column(Text, nullable=True)
    capture_id: Mapped[str] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=True)
    last_reached_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Customer Tracking fields
    customer_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_message_level: Mapped[str] = mapped_column(
        Text, nullable=False, default="info"
    )  # info, success, warn, error
    store_credit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Workflow Linkage
    workflow_id: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, index=True
    )

    # Timestamps
    created_at = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


Index("idx_orders_updated_at_desc", Order.updated_at.desc())
Index("idx_orders_created_at_desc", Order.created_at.desc())
