from datetime import UTC, datetime
from decimal import Decimal

import pytest

from customer_service_agent.commerce.orders import (
    CancelOrderRequest,
    CreateOrderItemRequest,
    CreateOrderRequest,
    OperationPreview,
    OperationService,
    Order,
    OrderCommandService,
    OrderStateInvalid,
    OrderStatus,
    OrderVersionConflict,
    ProductSnapshot,
    RequestReturnRequest,
    ReturnRequest,
    UpdateOrderContactRequest,
)
from customer_service_agent.shared.models import RuntimeContext


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def context() -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id="customer-1",
        thread_id="thread-1",
        request_id="request-1",
    )


def order(status: OrderStatus, *, version: int = 3) -> Order:
    return Order(
        order_id="order-1",
        customer_id="customer-1",
        status=status,
        contact_name="王小明",
        contact_phone="13812345678",
        shipping_address="上海市示例路 1 号",
        currency="CNY",
        total_amount=Decimal("20.00"),
        items=(),
        created_at=NOW,
        updated_at=NOW,
        version=version,
    )


class OperationStore:
    def __init__(self, repo: "Repository") -> None:
        self.operations: dict[str, OperationPreview] = {}
        self.repo = repo

    async def save_operation(self, operation: OperationPreview) -> None:
        self.operations[operation.operation_id] = operation

    async def get_operation(
        self,
        operation_id: str,
        customer_id: str,
        thread_id: str,
    ):
        operation = self.operations.get(operation_id)
        if operation is None:
            return None
        if operation.customer_id != customer_id or operation.thread_id != thread_id:
            return None
        return operation

    async def update_operation(self, operation: OperationPreview) -> None:
        self.operations[operation.operation_id] = operation

    async def execute_atomic(self, operation, executor):
        result = await executor(operation, self.repo)
        self.operations[operation.operation_id] = operation.model_copy(
            update={
                "status": "executed",
                "result_artifact": result,
            }
        )
        return result


class Repository:
    def __init__(self, stored: Order | None = None) -> None:
        self.orders: dict[str, Order] = {}
        if stored is not None:
            self.orders[stored.order_id] = stored
        self.returns: list[ReturnRequest] = []
        self.write_count = 0

    async def get(self, order_id: str, customer_id: str) -> Order | None:
        stored = self.orders.get(order_id)
        return stored if stored is not None and stored.customer_id == customer_id else None

    async def create(self, order: Order) -> None:
        self.write_count += 1
        self.orders[order.order_id] = order

    async def save(self, order: Order, *, expected_version: int) -> None:
        current = self.orders[order.order_id]
        assert current.version == expected_version
        self.write_count += 1
        self.orders[order.order_id] = order

    async def create_return(self, request: ReturnRequest) -> None:
        self.write_count += 1
        self.returns.append(request)


class Catalog:
    async def get(self, product_id: str) -> ProductSnapshot | None:
        if product_id != "product-1":
            return None
        return ProductSnapshot(
            product_id=product_id,
            product_name="蓝色水杯",
            unit_price=Decimal("10.00"),
            currency="CNY",
            active=True,
        )


def services(repo: Repository):
    operation_store = OperationStore(repo)
    operations = OperationService(
        store=operation_store,
        id_generator=lambda: "operation-1",
    )
    commands = OrderCommandService(
        repo=repo,
        catalog=Catalog(),
        operations=operations,
        clock=lambda: NOW,
        order_id_generator=lambda: "order-new",
        return_id_generator=lambda: "return-1",
    )
    return commands, operations, operation_store


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_preview_uses_catalog_price_without_inserting_order() -> None:
    repo = Repository()
    commands, _, _ = services(repo)

    preview = await commands.preview_create(
        context(),
        interrupt_id="interrupt-1",
        request=CreateOrderRequest(
            items=(CreateOrderItemRequest(product_id="product-1", quantity=2),),
            contact_name="王小明",
            contact_phone="13812345678",
            shipping_address="上海市示例路 1 号",
        ),
    )

    assert preview.tool_name == "create_order"
    assert preview.normalized_args["total_amount"] == "20.00"
    assert repo.orders == {}
    assert repo.write_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approved_create_inserts_catalog_snapshot_once() -> None:
    repo = Repository()
    commands, operations, _ = services(repo)
    preview = await commands.preview_create(
        context(),
        interrupt_id="interrupt-1",
        request=CreateOrderRequest(
            items=(CreateOrderItemRequest(product_id="product-1", quantity=2),),
            contact_name="王小明",
            contact_phone="13812345678",
            shipping_address="上海市示例路 1 号",
        ),
    )

    result = await operations.execute_approved(
        context(),
        operation_id=preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )

    created = repo.orders["order-new"]
    assert created.status is OrderStatus.PENDING_PAYMENT
    assert created.total_amount == Decimal("20.00")
    assert created.items[0].product_name_snapshot == "蓝色水杯"
    assert result["order_id"] == "order-new"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shipped_order_is_rejected_before_update_preview() -> None:
    repo = Repository(order(OrderStatus.SHIPPED))
    commands, _, operation_store = services(repo)

    with pytest.raises(OrderStateInvalid) as exc:
        await commands.preview_update_contact(
            context(),
            interrupt_id="interrupt-1",
            request=UpdateOrderContactRequest(
                order_id="order-1",
                shipping_address="北京市新地址 2 号",
            ),
        )

    assert exc.value.code == "ORDER_STATE_INVALID"
    assert operation_store.operations == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approved_contact_update_increments_version() -> None:
    repo = Repository(order(OrderStatus.PAID))
    commands, operations, _ = services(repo)
    preview = await commands.preview_update_contact(
        context(),
        interrupt_id="interrupt-1",
        request=UpdateOrderContactRequest(
            order_id="order-1",
            shipping_address="北京市新地址 2 号",
        ),
    )

    result = await operations.execute_approved(
        context(),
        operation_id=preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )

    assert repo.orders["order-1"].shipping_address == "北京市新地址 2 号"
    assert repo.orders["order-1"].version == 4
    assert result["version"] == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contact_update_preview_contains_masked_old_and_new_values() -> None:
    repo = Repository(order(OrderStatus.PAID))
    commands, _, _ = services(repo)

    preview = await commands.preview_update_contact(
        context(),
        interrupt_id="interrupt-1",
        request=UpdateOrderContactRequest(
            order_id="order-1",
            contact_phone="13987654321",
        ),
    )

    assert preview.normalized_args["preview"] == {
        "version": 3,
        "changes": {
            "contact_phone": {
                "old": "*******5678",
                "new": "*******4321",
            }
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approval_rejects_order_changed_after_preview() -> None:
    repo = Repository(order(OrderStatus.PAID))
    commands, operations, _ = services(repo)
    preview = await commands.preview_update_contact(
        context(),
        interrupt_id="interrupt-1",
        request=UpdateOrderContactRequest(
            order_id="order-1",
            shipping_address="北京市新地址 2 号",
        ),
    )
    repo.orders["order-1"] = repo.orders["order-1"].model_copy(
        update={"version": 4}
    )

    with pytest.raises(OrderVersionConflict) as exc:
        await operations.execute_approved(
            context(),
            operation_id=preview.operation_id,
            decision="approve",
            executor=commands.execute,
        )

    assert exc.value.code == "ORDER_VERSION_CONFLICT"
    assert repo.orders["order-1"].shipping_address == "上海市示例路 1 号"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_paid_cancel_marks_refund_pending_without_claiming_completion() -> None:
    repo = Repository(order(OrderStatus.PAID))
    commands, operations, _ = services(repo)
    preview = await commands.preview_cancel(
        context(),
        interrupt_id="interrupt-1",
        request=CancelOrderRequest(order_id="order-1", reason_code="changed_mind"),
    )

    result = await operations.execute_approved(
        context(),
        operation_id=preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )

    assert repo.orders["order-1"].status is OrderStatus.CANCELLED
    assert result == {
        "order_id": "order-1",
        "status": "cancelled",
        "refund_status": "pending",
    }
    assert "completed" not in str(result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_cancel_preserves_first_refund_result_without_second_write() -> None:
    repo = Repository(order(OrderStatus.PAID))
    commands, operations, operation_store = services(repo)
    first_preview = await commands.preview_cancel(
        context(),
        interrupt_id="interrupt-1",
        request=CancelOrderRequest(order_id="order-1", reason_code="changed_mind"),
    )
    first = await operations.execute_approved(
        context(),
        operation_id=first_preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )
    first_write_count = repo.write_count
    operation_store.operations.clear()

    second_preview = await commands.preview_cancel(
        context(),
        interrupt_id="interrupt-2",
        request=CancelOrderRequest(order_id="order-1", reason_code="retry"),
    )
    second = await operations.execute_approved(
        context(),
        operation_id=second_preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )

    assert first == second == {
        "order_id": "order-1",
        "status": "cancelled",
        "refund_status": "pending",
    }
    assert repo.write_count == first_write_count


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delivered_return_creates_request_and_only_reports_submitted() -> None:
    repo = Repository(order(OrderStatus.DELIVERED))
    commands, operations, _ = services(repo)
    preview = await commands.preview_return(
        context(),
        interrupt_id="interrupt-1",
        request=RequestReturnRequest(
            order_id="order-1",
            reason_code="damaged",
            reason_text="杯身破损",
        ),
    )

    result = await operations.execute_approved(
        context(),
        operation_id=preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )

    assert repo.orders["order-1"].status is OrderStatus.RETURN_REQUESTED
    assert len(repo.returns) == 1
    assert repo.returns[0].refund_status == "pending"
    assert result["message"] == "退货退款申请已提交"
    assert "通过" not in str(result)
    assert "到账" not in str(result)
