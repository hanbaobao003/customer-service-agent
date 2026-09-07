from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
import pytest

from customer_service_agent.commerce.orders import (
    CreateOrderItemRequest,
    CreateOrderRequest,
    OperationService,
    OperationStatus,
    Order,
    OrderCommandService,
    OrderStatus,
    ProductSnapshot,
    RequestReturnRequest,
    UpdateOrderContactRequest,
    CancelOrderRequest,
)
from customer_service_agent.commerce.postgres import PostgresCommerceStore
from customer_service_agent.shared.models import RuntimeContext


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


class Catalog:
    async def get(self, product_id: str) -> ProductSnapshot | None:
        return ProductSnapshot(
            product_id=product_id,
            product_name="蓝色水杯",
            unit_price=Decimal("10.00"),
            currency="CNY",
            active=True,
        )


class FailingAuditStore(PostgresCommerceStore):
    async def _write_audit(self, connection, operation, result) -> None:
        raise RuntimeError("injected audit failure")


def context() -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id="customer-1",
        thread_id="thread-1",
        request_id="request-1",
    )


def command_services(store: PostgresCommerceStore):
    operations = OperationService(store=store, id_generator=lambda: "operation-1")
    commands = OrderCommandService(
        repo=store,
        catalog=Catalog(),
        operations=operations,
        clock=lambda: NOW,
        order_id_generator=lambda: "order-1",
        return_id_generator=lambda: "return-1",
    )
    return commands, operations


async def preview_create(commands: OrderCommandService):
    return await commands.preview_create(
        context(),
        interrupt_id="interrupt-1",
        request=CreateOrderRequest(
            items=(CreateOrderItemRequest(product_id="product-1", quantity=2),),
            contact_name="王小明",
            contact_phone="13812345678",
            shipping_address="上海市示例路 1 号",
        ),
    )


def stored_order(status: OrderStatus) -> Order:
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
        version=3,
    )


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_approved_create_commits_order_operation_and_audit(
    postgres_dsn: str,
) -> None:
    store = PostgresCommerceStore(postgres_dsn)
    await store.setup()
    commands, operations = command_services(store)
    preview = await preview_create(commands)

    result = await operations.execute_approved(
        context(),
        operation_id=preview.operation_id,
        decision="approve",
        executor=commands.execute,
    )

    order = await store.get("order-1", "customer-1")
    executed = await store.get_operation(
        "operation-1", "customer-1", "thread-1"
    )
    assert order is not None and order.total_amount == Decimal("20.00")
    assert executed is not None and executed.status is OperationStatus.EXECUTED
    assert result == executed.result_artifact
    async with await psycopg.AsyncConnection.connect(
        postgres_dsn,
        row_factory=dict_row,
    ) as connection:
        audit = await connection.execute(
            "SELECT operation_id, request_id FROM audit_events"
        )
        assert await audit.fetchall() == [
            {"operation_id": "operation-1", "request_id": "request-1"}
        ]


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_audit_failure_rolls_back_order_and_operation_result(
    postgres_dsn: str,
) -> None:
    store = FailingAuditStore(postgres_dsn)
    await store.setup()
    commands, operations = command_services(store)
    preview = await preview_create(commands)

    with pytest.raises(RuntimeError, match="injected audit failure"):
        await operations.execute_approved(
            context(),
            operation_id=preview.operation_id,
            decision="approve",
            executor=commands.execute,
        )

    assert await store.get("order-1", "customer-1") is None
    pending = await store.get_operation(
        "operation-1", "customer-1", "thread-1"
    )
    assert pending is not None and pending.status is OperationStatus.PENDING
    assert pending.result_artifact is None


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_update_cancel_and_return_use_real_repository_sql(
    postgres_dsn: str,
) -> None:
    scenarios = (
        (OrderStatus.PAID, "update"),
        (OrderStatus.PAID, "cancel"),
        (OrderStatus.DELIVERED, "return"),
    )
    for index, (initial_status, action) in enumerate(scenarios, start=1):
        store = PostgresCommerceStore(postgres_dsn)
        await store.setup()
        seeded = stored_order(initial_status).model_copy(
            update={"order_id": f"order-{index}"}
        )
        await store.create(seeded)
        operations = OperationService(
            store=store,
            id_generator=lambda index=index: f"operation-{index}",
        )
        commands = OrderCommandService(
            repo=store,
            catalog=Catalog(),
            operations=operations,
            clock=lambda: NOW,
            order_id_generator=lambda: "unused",
            return_id_generator=lambda index=index: f"return-{index}",
        )
        if action == "update":
            preview = await commands.preview_update_contact(
                context(),
                interrupt_id=f"interrupt-{index}",
                request=UpdateOrderContactRequest(
                    order_id=seeded.order_id,
                    shipping_address="北京市新地址 2 号",
                ),
            )
        elif action == "cancel":
            preview = await commands.preview_cancel(
                context(),
                interrupt_id=f"interrupt-{index}",
                request=CancelOrderRequest(
                    order_id=seeded.order_id,
                    reason_code="changed_mind",
                ),
            )
        else:
            preview = await commands.preview_return(
                context(),
                interrupt_id=f"interrupt-{index}",
                request=RequestReturnRequest(
                    order_id=seeded.order_id,
                    reason_code="damaged",
                    reason_text="杯身破损",
                ),
            )
        first = await operations.execute_approved(
            context(),
            operation_id=preview.operation_id,
            decision="approve",
            executor=commands.execute,
        )
        repeated = await operations.execute_approved(
            context(),
            operation_id=preview.operation_id,
            decision="approve",
            executor=commands.execute,
        )
        assert repeated == first

    updated = await store.get("order-1", "customer-1")
    cancelled = await store.get("order-2", "customer-1")
    returned = await store.get("order-3", "customer-1")
    assert updated is not None and updated.version == 4
    assert cancelled is not None and cancelled.status is OrderStatus.CANCELLED
    assert returned is not None and returned.status is OrderStatus.RETURN_REQUESTED
