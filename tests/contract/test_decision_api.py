import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from customer_service_agent.agent_api.api import create_app
from customer_service_agent.shared.models import EventSequencer, EventType


class HeaderContextProvider:
    async def get_customer_id(self, request: Request) -> str:
        return request.headers["X-Test-Customer"]


class Healthy:
    async def readiness(self) -> dict[str, bool]:
        return {"postgres": True}


class DecisionServiceSpy:
    def __init__(self) -> None:
        self.decision = None

    async def resume_decision(self, context, *, interrupt_id, decision, reason):
        self.decision = (context, interrupt_id, decision, reason)
        yield EventSequencer(
            thread_id=context.thread_id,
            request_id=context.request_id,
        ).emit(EventType.MESSAGE_COMPLETED, {"message": "已处理"})


def app_for(service: DecisionServiceSpy):
    return create_app(
        service=service,
        context_provider=HeaderContextProvider(),
        health=Healthy(),
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_reject_decision_requires_non_blank_reason() -> None:
    service = DecisionServiceSpy()

    async with AsyncClient(
        transport=ASGITransport(app=app_for(service)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/threads/thread-1/decisions",
            headers={"X-Test-Customer": "customer-a"},
            json={"interrupt_id": "interrupt-1", "decision": "reject", "reason": " "},
        )

    assert response.status_code == 422
    assert service.decision is None


@pytest.mark.contract
@pytest.mark.asyncio
async def test_edit_decision_is_not_supported() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_for(DecisionServiceSpy())),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/threads/thread-1/decisions",
            headers={"X-Test-Customer": "customer-a"},
            json={"interrupt_id": "interrupt-1", "decision": "edit", "reason": "修改"},
        )

    assert response.status_code == 422


@pytest.mark.contract
@pytest.mark.asyncio
async def test_approve_decision_resumes_same_trusted_thread() -> None:
    service = DecisionServiceSpy()

    async with AsyncClient(
        transport=ASGITransport(app=app_for(service)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/threads/thread-1/decisions",
            headers={"X-Test-Customer": "customer-a"},
            json={"interrupt_id": "interrupt-1", "decision": "approve"},
        )

    assert response.status_code == 200
    context, interrupt_id, decision, reason = service.decision
    assert context.customer_id == "customer-a"
    assert context.thread_id == "thread-1"
    assert interrupt_id == "interrupt-1"
    assert decision == "approve"
    assert reason is None
