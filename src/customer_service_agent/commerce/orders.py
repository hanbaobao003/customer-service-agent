"""Order domain models and lifecycle rules."""

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict

from customer_service_agent.shared.models import RuntimeContext


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


class OrderNotFound(Exception):
    code = "ORDER_NOT_FOUND"


class OrderRepository(Protocol):
    async def get(self, order_id: str, customer_id: str) -> Order | None: ...


class OrderReadResult(_OrderModel):
    content: dict[str, object]
    artifact: dict[str, object]

    def to_tool_output(self) -> tuple[str, dict[str, object]]:
        return json.dumps(self.content, ensure_ascii=False), self.artifact


class OrderService:
    def __init__(
        self,
        *,
        repo: OrderRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repo = repo
        self._clock = clock

    async def get_order(
        self,
        context: RuntimeContext,
        order_id: str,
    ) -> OrderReadResult:
        order = await self._repo.get(order_id, context.customer_id)
        if order is None:
            raise OrderNotFound("order not found")

        queried_at = self._clock().isoformat().replace("+00:00", "Z")
        content: dict[str, object] = {
            "order_id": order.order_id,
            "status": order.status.value,
            "version": order.version,
            "queried_at": queried_at,
            "currency": order.currency,
            "total_amount": str(order.total_amount),
            "item_count": len(order.items),
        }
        artifact: dict[str, object] = {
            **content,
            "contact_name": _mask_name(order.contact_name),
            "contact_phone": _mask_phone(order.contact_phone),
            "shipping_address": _mask_address(order.shipping_address),
            "items": [item.model_dump(mode="json") for item in order.items],
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
        }
        return OrderReadResult(content=content, artifact=artifact)


def create_get_order_tool(service: OrderService):
    @tool("get_order", response_format="content_and_artifact")
    async def get_order(
        order_id: str,
        runtime: ToolRuntime[RuntimeContext],
    ) -> tuple[str, dict[str, object]]:
        """查询当前客户拥有的订单。"""
        result = await service.get_order(runtime.context, order_id)
        return result.to_tool_output()

    return get_order


def _mask_name(value: str) -> str:
    return f"{value[:1]}**" if value else "***"


def _mask_phone(value: str) -> str:
    visible = value[-4:]
    return f"{'*' * max(len(value) - len(visible), 0)}{visible}"


def _mask_address(value: str) -> str:
    return f"{value[:3]}{'*' * 12}" if value else "************"


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
