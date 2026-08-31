"""Order domain models and lifecycle rules."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class BusinessRuleRejected(Exception):
    code = "BUSINESS_RULE_REJECTED"


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    RETURN_REQUESTED = "return_requested"
    RETURNED = "returned"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class _OrderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderItem(_OrderModel):
    product_id: str
    product_name_snapshot: str
    unit_price: Decimal
    quantity: int


class Order(_OrderModel):
    order_id: str
    customer_id: str
    status: OrderStatus
    contact_name: str
    contact_phone: str
    shipping_address: str
    currency: str
    total_amount: Decimal
    items: tuple[OrderItem, ...]
    created_at: datetime
    updated_at: datetime
    version: int


class ReturnRequest(_OrderModel):
    return_id: str
    order_id: str
    customer_id: str
    reason_code: str
    reason_text: str
    status: str
    refund_status: str
    created_at: datetime
    updated_at: datetime


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_PAYMENT: frozenset(
        {OrderStatus.PAID, OrderStatus.CANCELLED}
    ),
    OrderStatus.PAID: frozenset(
        {OrderStatus.PROCESSING, OrderStatus.CANCELLED}
    ),
    OrderStatus.PROCESSING: frozenset(
        {OrderStatus.SHIPPED, OrderStatus.CANCELLED}
    ),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.RETURN_REQUESTED}),
}


def transition(order: Order, target: OrderStatus) -> Order:
    if target not in ALLOWED_TRANSITIONS.get(order.status, frozenset()):
        raise BusinessRuleRejected(
            f"transition from {order.status} to {target} is not allowed"
        )
    return order.model_copy(
        update={"status": target, "version": order.version + 1}
    )
