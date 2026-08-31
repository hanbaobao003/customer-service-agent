"""Order domain models and lifecycle rules."""

import json
from collections.abc import Awaitable, Callable
from hashlib import sha256
from hmac import compare_digest
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

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


class OperationStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class OperationPreview(_OrderModel):
    operation_id: str
    customer_id: str
    thread_id: str
    interrupt_id: str
    tool_name: str
    normalized_args: dict[str, object]
    args_hash: str
    expected_version: int | None
    status: OperationStatus = OperationStatus.PENDING
    result_artifact: dict[str, object] | None = None


class OperationHashMismatch(Exception):
    code = "ORDER_OPERATION_REJECTED"


class OperationRejected(Exception):
    code = "ORDER_OPERATION_REJECTED"


class OperationUnavailable(Exception):
    code = "ORDER_OPERATION_REJECTED"


class OperationExpired(Exception):
    code = "ORDER_OPERATION_EXPIRED"


class OperationStore(Protocol):
    async def save(self, operation: OperationPreview) -> None: ...

    async def get(
        self,
        operation_id: str,
        customer_id: str,
        thread_id: str,
    ) -> OperationPreview | None: ...

    async def update(self, operation: OperationPreview) -> None: ...


class OperationService:
    def __init__(
        self,
        *,
        store: OperationStore,
        id_generator: Callable[[], str],
    ) -> None:
        self._store = store
        self._id_generator = id_generator

    async def preview_operation(
        self,
        context: RuntimeContext,
        *,
        interrupt_id: str,
        tool_name: str,
        normalized_args: dict[str, object],
        expected_version: int | None,
    ) -> OperationPreview:
        if not interrupt_id.strip() or not tool_name.strip():
            raise ValueError("interrupt_id and tool_name must not be blank")
        operation_id = self._id_generator()
        if not operation_id.strip():
            raise ValueError("operation_id must not be blank")

        canonical_args = _canonical_json_object(normalized_args)
        operation = OperationPreview(
            operation_id=operation_id,
            customer_id=context.customer_id,
            thread_id=context.thread_id,
            interrupt_id=interrupt_id,
            tool_name=tool_name,
            normalized_args=canonical_args,
            args_hash=_operation_hash(
                customer_id=context.customer_id,
                thread_id=context.thread_id,
                interrupt_id=interrupt_id,
                tool_name=tool_name,
                normalized_args=canonical_args,
                expected_version=expected_version,
            ),
            expected_version=expected_version,
        )
        await self._store.save(operation)
        return operation

    async def execute_approved(
        self,
        context: RuntimeContext,
        *,
        operation_id: str,
        decision: Literal["approve", "reject"],
        executor: Callable[
            [OperationPreview], Awaitable[dict[str, object]]
        ],
    ) -> dict[str, object] | None:
        operation = await self._store.get(
            operation_id,
            context.customer_id,
            context.thread_id,
        )
        if operation is None:
            raise OperationUnavailable("operation unavailable")

        current_hash = _operation_hash(
            customer_id=operation.customer_id,
            thread_id=operation.thread_id,
            interrupt_id=operation.interrupt_id,
            tool_name=operation.tool_name,
            normalized_args=operation.normalized_args,
            expected_version=operation.expected_version,
        )
        if not compare_digest(current_hash, operation.args_hash):
            raise OperationHashMismatch("operation arguments changed")

        if operation.status is OperationStatus.REJECTED:
            raise OperationRejected("operation was rejected")
        if operation.status is OperationStatus.EXPIRED:
            raise OperationExpired("operation expired")
        if operation.status is OperationStatus.EXECUTED:
            if decision != "approve":
                raise OperationRejected("executed operation cannot be rejected")
            return dict(operation.result_artifact or {})
        if decision == "reject":
            await self._store.update(
                operation.model_copy(update={"status": OperationStatus.REJECTED})
            )
            return None
        if decision != "approve":
            raise ValueError("decision must be approve or reject")

        result = _canonical_json_object(await executor(operation))
        await self._store.update(
            operation.model_copy(
                update={
                    "status": OperationStatus.EXECUTED,
                    "result_artifact": result,
                }
            )
        )
        return dict(result)


def _canonical_json_object(value: dict[str, object]) -> dict[str, object]:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("normalized arguments must be a JSON object")
    return decoded


def _operation_hash(
    *,
    customer_id: str,
    thread_id: str,
    interrupt_id: str,
    tool_name: str,
    normalized_args: dict[str, object],
    expected_version: int | None,
) -> str:
    payload = {
        "customer_id": customer_id,
        "expected_version": expected_version,
        "interrupt_id": interrupt_id,
        "normalized_args": normalized_args,
        "thread_id": thread_id,
        "tool_name": tool_name,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()


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
