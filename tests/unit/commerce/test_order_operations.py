import pytest

from customer_service_agent.commerce.orders import (
    OperationExpired,
    OperationHashMismatch,
    OperationPreview,
    OperationRejected,
    OperationService,
    OperationStatus,
    OperationUnavailable,
)
from customer_service_agent.shared.models import RuntimeContext


def context(
    *,
    customer_id: str = "customer-1",
    thread_id: str = "thread-1",
) -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id=customer_id,
        thread_id=thread_id,
        request_id="request-1",
    )


class Store:
    def __init__(self) -> None:
        self.operations: dict[str, OperationPreview] = {}

    async def save(self, operation: OperationPreview) -> None:
        self.operations[operation.operation_id] = operation

    async def get(
        self,
        operation_id: str,
        customer_id: str,
        thread_id: str,
    ) -> OperationPreview | None:
        operation = self.operations.get(operation_id)
        if operation is None:
            return None
        if operation.customer_id != customer_id or operation.thread_id != thread_id:
            return None
        return operation

    async def update(self, operation: OperationPreview) -> None:
        self.operations[operation.operation_id] = operation

    def tamper(self, operation_id: str, normalized_args: dict[str, object]) -> None:
        operation = self.operations[operation_id]
        self.operations[operation_id] = operation.model_copy(
            update={"normalized_args": normalized_args}
        )

    def replace(self, operation_id: str, **changes: object) -> None:
        operation = self.operations[operation_id]
        self.operations[operation_id] = operation.model_copy(update=changes)


class Executor:
    def __init__(self) -> None:
        self.write_count = 0
        self.audit_count = 0

    async def __call__(self, operation: OperationPreview) -> dict[str, object]:
        self.write_count += 1
        self.audit_count += 1
        return {"order_id": operation.normalized_args["order_id"], "ok": True}


def service(store: Store) -> OperationService:
    return OperationService(store=store, id_generator=lambda: "operation-1")


async def preview(store: Store) -> OperationPreview:
    return await service(store).preview_operation(
        context(),
        interrupt_id="interrupt-1",
        tool_name="cancel_order",
        normalized_args={"reason": "不需要", "order_id": "order-1"},
        expected_version=3,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preview_normalizes_arguments_and_binds_trusted_context() -> None:
    store = Store()

    operation = await preview(store)

    assert operation.operation_id == "operation-1"
    assert operation.customer_id == "customer-1"
    assert operation.thread_id == "thread-1"
    assert operation.interrupt_id == "interrupt-1"
    assert operation.normalized_args == {
        "order_id": "order-1",
        "reason": "不需要",
    }
    assert len(operation.args_hash) == 64
    assert operation.status is OperationStatus.PENDING


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approval_rejects_changed_normalized_arguments() -> None:
    store = Store()
    operation = await preview(store)
    store.tamper(operation.operation_id, {"order_id": "order-2"})
    executor = Executor()

    with pytest.raises(OperationHashMismatch):
        await service(store).execute_approved(
            context(),
            operation_id=operation.operation_id,
            decision="approve",
            executor=executor,
        )

    assert executor.write_count == 0
    assert executor.audit_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"tool_name": "update_order_contact"},
        {"interrupt_id": "interrupt-2"},
        {"expected_version": 4},
    ],
)
async def test_hash_binds_tool_interrupt_and_version(
    changes: dict[str, object],
) -> None:
    store = Store()
    operation = await preview(store)
    store.replace(operation.operation_id, **changes)

    with pytest.raises(OperationHashMismatch):
        await service(store).execute_approved(
            context(),
            operation_id=operation.operation_id,
            decision="approve",
            executor=Executor(),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hash_binds_customer_and_thread() -> None:
    common = {
        "interrupt_id": "interrupt-1",
        "tool_name": "cancel_order",
        "normalized_args": {"order_id": "order-1"},
        "expected_version": 3,
    }
    first = await service(Store()).preview_operation(context(), **common)
    second = await service(Store()).preview_operation(
        context(customer_id="customer-2", thread_id="thread-2"),
        **common,
    )

    assert first.args_hash != second.args_hash


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_approval_returns_first_result_without_second_write() -> None:
    store = Store()
    operation = await preview(store)
    executor = Executor()
    operation_service = service(store)

    first = await operation_service.execute_approved(
        context(),
        operation_id=operation.operation_id,
        decision="approve",
        executor=executor,
    )
    second = await operation_service.execute_approved(
        context(),
        operation_id=operation.operation_id,
        decision="approve",
        executor=executor,
    )

    assert first == second == {"order_id": "order-1", "ok": True}
    assert executor.write_count == 1
    assert executor.audit_count == 1
    assert store.operations[operation.operation_id].status is OperationStatus.EXECUTED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejected_operation_cannot_be_approved() -> None:
    store = Store()
    operation = await preview(store)
    executor = Executor()
    operation_service = service(store)

    result = await operation_service.execute_approved(
        context(),
        operation_id=operation.operation_id,
        decision="reject",
        executor=executor,
    )

    assert result is None
    assert store.operations[operation.operation_id].status is OperationStatus.REJECTED
    with pytest.raises(OperationRejected):
        await operation_service.execute_approved(
            context(),
            operation_id=operation.operation_id,
            decision="approve",
            executor=executor,
        )
    assert executor.write_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expired_operation_cannot_be_approved() -> None:
    store = Store()
    operation = await preview(store)
    store.replace(operation.operation_id, status=OperationStatus.EXPIRED)

    with pytest.raises(OperationExpired) as exc:
        await service(store).execute_approved(
            context(),
            operation_id=operation.operation_id,
            decision="approve",
            executor=Executor(),
        )

    assert exc.value.code == "ORDER_OPERATION_EXPIRED"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_context",
    [context(customer_id="customer-2"), context(thread_id="thread-2")],
)
async def test_operation_is_unavailable_outside_customer_and_thread(
    other_context: RuntimeContext,
) -> None:
    store = Store()
    operation = await preview(store)

    with pytest.raises(OperationUnavailable):
        await service(store).execute_approved(
            other_context,
            operation_id=operation.operation_id,
            decision="approve",
            executor=Executor(),
        )
