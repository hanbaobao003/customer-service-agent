import pytest

from customer_service_agent.commerce.orders import OrderApprovalCoordinator
from customer_service_agent.shared.models import RuntimeContext


class Operations:
    def __init__(self) -> None:
        self.calls = []

    async def execute_approved(self, context, *, operation_id, decision, executor):
        self.calls.append((context, operation_id, decision))
        return await executor("operation", object())


class Commands:
    async def execute(self, operation, repository):
        assert operation == "operation"
        assert repository is not None
        return {"status": "cancelled"}


@pytest.mark.contract
@pytest.mark.asyncio
async def test_approval_coordinator_executes_through_operation_service_once() -> None:
    operations = Operations()
    coordinator = OrderApprovalCoordinator(operations=operations, commands=Commands())
    context = RuntimeContext.trusted(
        customer_id="demo-customer-a",
        thread_id="thread-1",
        request_id="request-1",
    )

    result = await coordinator.execute_approved(
        context,
        operation_id="operation-1",
        decision="approve",
    )

    assert result == {"status": "cancelled"}
    assert operations.calls == [(context, "operation-1", "approve")]
