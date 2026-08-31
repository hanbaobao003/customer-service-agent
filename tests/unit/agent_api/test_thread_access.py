import pytest

from customer_service_agent.agent_api.service import (
    CustomerService,
    InMemoryRunLock,
    InMemoryThreadBindings,
)
from customer_service_agent.shared.errors import ServiceError
from customer_service_agent.shared.models import RuntimeContext


def context(customer_id: str, thread_id: str = "thread-1") -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id=customer_id,
        thread_id=thread_id,
        request_id="request-1",
    )


class RecordingAgent:
    def __init__(self) -> None:
        self.called = False

    async def stream(self, *, context: RuntimeContext, message: str):
        self.called = True
        yield "unexpected"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bound_thread_rejects_other_customer_before_agent_call() -> None:
    agent = RecordingAgent()
    service = CustomerService(
        bindings=InMemoryThreadBindings({"thread-1": "customer-a"}),
        run_lock=InMemoryRunLock(),
        agent=agent,
    )

    with pytest.raises(ServiceError) as captured:
        [event async for event in service.stream_message(context("customer-b"), "查询订单")]

    assert captured.value.code == "THREAD_CUSTOMER_MISMATCH"
    assert captured.value.request_id == "request-1"
    assert agent.called is False
