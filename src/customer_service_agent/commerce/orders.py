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
from pydantic import BaseModel, ConfigDict, Field

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
    cancellation_refund_status: Literal["pending", "not_required"] | None = None


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


class ProductSnapshot(_OrderModel):
    product_id: str
    product_name: str
    unit_price: Decimal
    currency: str
    active: bool


class CreateOrderItemRequest(_OrderModel):
    product_id: str
    quantity: int = Field(gt=0)


class CreateOrderRequest(_OrderModel):
    items: tuple[CreateOrderItemRequest, ...] = Field(min_length=1)
    contact_name: str = Field(min_length=1)
    contact_phone: str = Field(min_length=1)
    shipping_address: str = Field(min_length=1)


class UpdateOrderContactRequest(_OrderModel):
    order_id: str
    contact_name: str | None = None
    contact_phone: str | None = None
    shipping_address: str | None = None


class CancelOrderRequest(_OrderModel):
    order_id: str
    reason_code: str
    reason_text: str | None = None


class RequestReturnRequest(_OrderModel):
    order_id: str
    reason_code: str
    reason_text: str


class OrderStateInvalid(Exception):
    code = "ORDER_STATE_INVALID"


class OrderVersionConflict(Exception):
    code = "ORDER_VERSION_CONFLICT"


class ProductCatalog(Protocol):
    async def get(self, product_id: str) -> ProductSnapshot | None: ...


class OrderCommandRepository(Protocol):
    async def get(self, order_id: str, customer_id: str) -> Order | None: ...

    async def create(self, order: Order) -> None: ...

    async def save(self, order: Order, *, expected_version: int) -> None: ...

    async def create_return(self, request: ReturnRequest) -> None: ...


class OrderCommandService:
    def __init__(
        self,
        *,
        repo: OrderCommandRepository,
        catalog: ProductCatalog,
        operations: "OperationService",
        clock: Callable[[], datetime],
        order_id_generator: Callable[[], str],
        return_id_generator: Callable[[], str],
    ) -> None:
        self._repo = repo
        self._catalog = catalog
        self._operations = operations
        self._clock = clock
        self._order_id_generator = order_id_generator
        self._return_id_generator = return_id_generator

    async def preview_create(
        self,
        context: RuntimeContext,
        *,
        interrupt_id: str,
        request: CreateOrderRequest,
    ) -> "OperationPreview":
        snapshots: list[dict[str, object]] = []
        total = Decimal("0")
        currency: str | None = None
        for item in request.items:
            product = await self._catalog.get(item.product_id)
            if product is None or not product.active:
                raise BusinessRuleRejected("product is unavailable")
            if currency is not None and product.currency != currency:
                raise BusinessRuleRejected("mixed currencies are not allowed")
            currency = product.currency
            total += product.unit_price * item.quantity
            snapshots.append(
                {
                    "product_id": product.product_id,
                    "product_name": product.product_name,
                    "unit_price": str(product.unit_price),
                    "quantity": item.quantity,
                }
            )
        return await self._operations.preview_operation(
            context,
            interrupt_id=interrupt_id,
            tool_name="create_order",
            normalized_args={
                "order_id": self._order_id_generator(),
                "items": snapshots,
                "contact_name": request.contact_name.strip(),
                "contact_phone": request.contact_phone.strip(),
                "shipping_address": request.shipping_address.strip(),
                "currency": currency or "",
                "total_amount": str(total),
            },
            expected_version=None,
        )

    async def preview_update_contact(
        self,
        context: RuntimeContext,
        *,
        interrupt_id: str,
        request: UpdateOrderContactRequest,
    ) -> "OperationPreview":
        stored = await self._require_order(context, request.order_id)
        if stored.status not in {
            OrderStatus.PENDING_PAYMENT,
            OrderStatus.PAID,
            OrderStatus.PROCESSING,
        }:
            raise OrderStateInvalid("order contact cannot be changed")
        changes = {
            name: value.strip()
            for name, value in {
                "contact_name": request.contact_name,
                "contact_phone": request.contact_phone,
                "shipping_address": request.shipping_address,
            }.items()
            if value is not None and value.strip()
        }
        if not changes:
            raise BusinessRuleRejected("at least one contact field is required")
        maskers = {
            "contact_name": _mask_name,
            "contact_phone": _mask_phone,
            "shipping_address": _mask_address,
        }
        preview_changes = {
            name: {
                "old": maskers[name](str(getattr(stored, name))),
                "new": maskers[name](str(value)),
            }
            for name, value in changes.items()
        }
        return await self._operations.preview_operation(
            context,
            interrupt_id=interrupt_id,
            tool_name="update_order_contact",
            normalized_args={
                "order_id": stored.order_id,
                **changes,
                "preview": {
                    "version": stored.version,
                    "changes": preview_changes,
                },
            },
            expected_version=stored.version,
        )

    async def preview_cancel(
        self,
        context: RuntimeContext,
        *,
        interrupt_id: str,
        request: CancelOrderRequest,
    ) -> "OperationPreview":
        stored = await self._require_order(context, request.order_id)
        if stored.status not in {
            OrderStatus.PENDING_PAYMENT,
            OrderStatus.PAID,
            OrderStatus.PROCESSING,
            OrderStatus.CANCELLED,
        }:
            raise OrderStateInvalid("order cannot be cancelled")
        refund_status = stored.cancellation_refund_status or (
            "pending" if stored.status is OrderStatus.PAID else "not_required"
        )
        return await self._operations.preview_operation(
            context,
            interrupt_id=interrupt_id,
            tool_name="cancel_order",
            normalized_args={
                "order_id": stored.order_id,
                "reason_code": request.reason_code,
                "reason_text": request.reason_text,
                "refund_status": refund_status,
            },
            expected_version=stored.version,
        )

    async def preview_return(
        self,
        context: RuntimeContext,
        *,
        interrupt_id: str,
        request: RequestReturnRequest,
    ) -> "OperationPreview":
        stored = await self._require_order(context, request.order_id)
        if stored.status is not OrderStatus.DELIVERED:
            raise OrderStateInvalid("order is not eligible for return")
        return await self._operations.preview_operation(
            context,
            interrupt_id=interrupt_id,
            tool_name="request_return",
            normalized_args={
                "order_id": stored.order_id,
                "return_id": self._return_id_generator(),
                "reason_code": request.reason_code,
                "reason_text": request.reason_text,
            },
            expected_version=stored.version,
        )

    async def execute(
        self,
        operation: "OperationPreview",
        repo: OrderCommandRepository,
    ) -> dict[str, object]:
        handlers = {
            "create_order": self._execute_create,
            "update_order_contact": self._execute_update_contact,
            "cancel_order": self._execute_cancel,
            "request_return": self._execute_return,
        }
        handler = handlers.get(operation.tool_name)
        if handler is None:
            raise BusinessRuleRejected("unsupported order operation")
        return await handler(operation, repo)

    async def _require_order(
        self,
        context: RuntimeContext,
        order_id: str,
    ) -> Order:
        stored = await self._repo.get(order_id, context.customer_id)
        if stored is None:
            raise OrderNotFound("order not found")
        return stored

    async def _load_for_execution(
        self,
        operation: "OperationPreview",
        repo: OrderCommandRepository,
    ) -> Order:
        order_id = str(operation.normalized_args["order_id"])
        stored = await repo.get(order_id, operation.customer_id)
        if stored is None:
            raise OrderNotFound("order not found")
        if operation.expected_version != stored.version:
            raise OrderVersionConflict("order version changed")
        return stored

    async def _execute_create(
        self,
        operation: "OperationPreview",
        repo: OrderCommandRepository,
    ) -> dict[str, object]:
        args = operation.normalized_args
        raw_items = args["items"]
        if not isinstance(raw_items, list) or any(
            not isinstance(item, dict) for item in raw_items
        ):
            raise BusinessRuleRejected("invalid order items")
        now = self._clock()
        created = Order(
            order_id=str(args["order_id"]),
            customer_id=operation.customer_id,
            status=OrderStatus.PENDING_PAYMENT,
            contact_name=str(args["contact_name"]),
            contact_phone=str(args["contact_phone"]),
            shipping_address=str(args["shipping_address"]),
            currency=str(args["currency"]),
            total_amount=Decimal(str(args["total_amount"])),
            items=tuple(
                OrderItem(
                    product_id=str(item["product_id"]),
                    product_name_snapshot=str(item["product_name"]),
                    unit_price=Decimal(str(item["unit_price"])),
                    quantity=int(item["quantity"]),
                )
                for item in raw_items
            ),
            created_at=now,
            updated_at=now,
            version=1,
        )
        await repo.create(created)
        return {
            "order_id": created.order_id,
            "status": created.status.value,
            "total_amount": str(created.total_amount),
            "currency": created.currency,
        }

    async def _execute_update_contact(
        self,
        operation: "OperationPreview",
        repo: OrderCommandRepository,
    ) -> dict[str, object]:
        stored = await self._load_for_execution(operation, repo)
        if stored.status not in {
            OrderStatus.PENDING_PAYMENT,
            OrderStatus.PAID,
            OrderStatus.PROCESSING,
        }:
            raise OrderStateInvalid("order contact cannot be changed")
        updates = {
            key: operation.normalized_args[key]
            for key in ("contact_name", "contact_phone", "shipping_address")
            if key in operation.normalized_args
        }
        changed = stored.model_copy(
            update={
                **updates,
                "updated_at": self._clock(),
                "version": stored.version + 1,
            }
        )
        await repo.save(changed, expected_version=stored.version)
        return {
            "order_id": changed.order_id,
            "status": changed.status.value,
            "version": changed.version,
        }

    async def _execute_cancel(
        self,
        operation: "OperationPreview",
        repo: OrderCommandRepository,
    ) -> dict[str, object]:
        stored = await self._load_for_execution(operation, repo)
        refund_status = str(operation.normalized_args["refund_status"])
        if stored.status is not OrderStatus.CANCELLED:
            try:
                changed = transition(stored, OrderStatus.CANCELLED).model_copy(
                    update={
                        "updated_at": self._clock(),
                        "cancellation_refund_status": refund_status,
                    }
                )
            except BusinessRuleRejected as exc:
                raise OrderStateInvalid("order cannot be cancelled") from exc
            await repo.save(changed, expected_version=stored.version)
        return {
            "order_id": stored.order_id,
            "status": OrderStatus.CANCELLED.value,
            "refund_status": refund_status,
        }

    async def _execute_return(
        self,
        operation: "OperationPreview",
        repo: OrderCommandRepository,
    ) -> dict[str, object]:
        stored = await self._load_for_execution(operation, repo)
        if stored.status is not OrderStatus.DELIVERED:
            raise OrderStateInvalid("order is not eligible for return")
        now = self._clock()
        changed = transition(stored, OrderStatus.RETURN_REQUESTED).model_copy(
            update={"updated_at": now}
        )
        request = ReturnRequest(
            return_id=str(operation.normalized_args["return_id"]),
            order_id=stored.order_id,
            customer_id=operation.customer_id,
            reason_code=str(operation.normalized_args["reason_code"]),
            reason_text=str(operation.normalized_args["reason_text"]),
            status="requested",
            refund_status="pending",
            created_at=now,
            updated_at=now,
        )
        await repo.save(changed, expected_version=stored.version)
        await repo.create_return(request)
        return {
            "order_id": stored.order_id,
            "return_id": request.return_id,
            "status": changed.status.value,
            "refund_status": request.refund_status,
            "message": "退货退款申请已提交",
        }


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
    request_id: str
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
    async def save_operation(self, operation: OperationPreview) -> None: ...

    async def get_operation(
        self,
        operation_id: str,
        customer_id: str,
        thread_id: str,
    ) -> OperationPreview | None: ...

    async def update_operation(self, operation: OperationPreview) -> None: ...

    async def execute_atomic(
        self,
        operation: OperationPreview,
        executor: Callable[
            [OperationPreview, OrderCommandRepository],
            Awaitable[dict[str, object]],
        ],
    ) -> dict[str, object]: ...


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
            request_id=context.request_id,
            interrupt_id=interrupt_id,
            tool_name=tool_name,
            normalized_args=canonical_args,
            args_hash=_operation_hash(
                customer_id=context.customer_id,
                thread_id=context.thread_id,
                request_id=context.request_id,
                interrupt_id=interrupt_id,
                tool_name=tool_name,
                normalized_args=canonical_args,
                expected_version=expected_version,
            ),
            expected_version=expected_version,
        )
        await self._store.save_operation(operation)
        return operation

    async def execute_approved(
        self,
        context: RuntimeContext,
        *,
        operation_id: str,
        decision: Literal["approve", "reject"],
        executor: Callable[
            [OperationPreview, OrderCommandRepository],
            Awaitable[dict[str, object]],
        ],
    ) -> dict[str, object] | None:
        operation = await self._store.get_operation(
            operation_id,
            context.customer_id,
            context.thread_id,
        )
        if operation is None:
            raise OperationUnavailable("operation unavailable")

        current_hash = _operation_hash(
            customer_id=operation.customer_id,
            thread_id=operation.thread_id,
            request_id=operation.request_id,
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
            await self._store.update_operation(
                operation.model_copy(update={"status": OperationStatus.REJECTED})
            )
            return None
        if decision != "approve":
            raise ValueError("decision must be approve or reject")

        result = await self._store.execute_atomic(operation, executor)
        return dict(_canonical_json_object(result))


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
    request_id: str,
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
        "request_id": request_id,
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


def create_order_preview_tools(
    commands: OrderCommandService,
    *,
    approval_id_generator: Callable[[], str],
):
    def approval_id() -> str:
        value = approval_id_generator().strip()
        if not value:
            raise ValueError("approval_id_generator returned blank value")
        return value

    @tool("create_order", response_format="content_and_artifact")
    async def create_order(
        items: list[CreateOrderItemRequest],
        contact_name: str,
        contact_phone: str,
        shipping_address: str,
        runtime: ToolRuntime[RuntimeContext],
    ) -> tuple[str, dict[str, object]]:
        """生成创建订单预览，等待用户审批后才会创建订单。"""
        preview = await commands.preview_create(
            runtime.context,
            interrupt_id=approval_id(),
            request=CreateOrderRequest(
                items=tuple(items),
                contact_name=contact_name,
                contact_phone=contact_phone,
                shipping_address=shipping_address,
            ),
        )
        return _preview_tool_output(preview)

    @tool("update_order_contact", response_format="content_and_artifact")
    async def update_order_contact(
        order_id: str,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        shipping_address: str | None = None,
        runtime: ToolRuntime[RuntimeContext] = None,
    ) -> tuple[str, dict[str, object]]:
        """生成订单联系方式修改预览，等待用户审批后才会修改。"""
        preview = await commands.preview_update_contact(
            runtime.context,
            interrupt_id=approval_id(),
            request=UpdateOrderContactRequest(
                order_id=order_id,
                contact_name=contact_name,
                contact_phone=contact_phone,
                shipping_address=shipping_address,
            ),
        )
        return _preview_tool_output(preview)

    @tool("cancel_order", response_format="content_and_artifact")
    async def cancel_order(
        order_id: str,
        reason_code: str,
        reason_text: str | None = None,
        runtime: ToolRuntime[RuntimeContext] = None,
    ) -> tuple[str, dict[str, object]]:
        """生成取消订单预览，等待用户审批后才会取消。"""
        preview = await commands.preview_cancel(
            runtime.context,
            interrupt_id=approval_id(),
            request=CancelOrderRequest(
                order_id=order_id,
                reason_code=reason_code,
                reason_text=reason_text,
            ),
        )
        return _preview_tool_output(preview)

    @tool("request_return", response_format="content_and_artifact")
    async def request_return(
        order_id: str,
        reason_code: str,
        reason_text: str,
        runtime: ToolRuntime[RuntimeContext] = None,
    ) -> tuple[str, dict[str, object]]:
        """生成退货申请预览，等待用户审批后才会提交申请。"""
        preview = await commands.preview_return(
            runtime.context,
            interrupt_id=approval_id(),
            request=RequestReturnRequest(
                order_id=order_id,
                reason_code=reason_code,
                reason_text=reason_text,
            ),
        )
        return _preview_tool_output(preview)

    return create_order, update_order_contact, cancel_order, request_return


def _preview_tool_output(preview: OperationPreview) -> tuple[str, dict[str, object]]:
    return (
        json.dumps(
            {
                "operation_id": preview.operation_id,
                "tool_name": preview.tool_name,
                "status": "approval_required",
            },
            ensure_ascii=False,
        ),
        {
            "approval_id": preview.interrupt_id,
            "operation": preview.model_dump(mode="json"),
        },
    )


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
