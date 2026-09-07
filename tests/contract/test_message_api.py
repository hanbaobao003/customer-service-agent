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


class ServiceSpy:
    def __init__(self) -> None:
        self.context = None
        self.message = None

    async def stream_message(self, context, message):
        self.context = context
        self.message = message
        yield EventSequencer(
            thread_id=context.thread_id,
            request_id=context.request_id,
        ).emit(EventType.MESSAGE_COMPLETED, {"message": "收到"})


def app_for(service: ServiceSpy):
    return create_app(
        service=service,
        context_provider=HeaderContextProvider(),
        health=Healthy(),
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_message_api_uses_authenticated_customer_not_body() -> None:
    service = ServiceSpy()

    async with AsyncClient(
        transport=ASGITransport(app=app_for(service)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/threads/thread-1/messages:stream",
            headers={"X-Test-Customer": "customer-a", "Accept": "text/event-stream"},
            json={"message": "你好"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert service.context.customer_id == "customer-a"
    assert service.context.thread_id == "thread-1"
    assert service.message == "你好"


@pytest.mark.contract
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"message": "  "},
        {"message": "你好", "customer_id": "attacker-selected"},
        {"message": "界" * 4001},
    ],
)
async def test_message_api_rejects_invalid_or_untrusted_body_fields(
    body: dict[str, str],
) -> None:
    service = ServiceSpy()

    async with AsyncClient(
        transport=ASGITransport(app=app_for(service)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/threads/thread-1/messages:stream",
            headers={"X-Test-Customer": "customer-a"},
            json=body,
        )

    assert response.status_code == 422
    assert service.context is None


@pytest.mark.contract
@pytest.mark.asyncio
async def test_message_api_accepts_exactly_four_thousand_characters() -> None:
    service = ServiceSpy()
    message = "界" * 4000

    async with AsyncClient(
        transport=ASGITransport(app=app_for(service)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/threads/thread-1/messages:stream",
            headers={"X-Test-Customer": "customer-a"},
            json={"message": message},
        )

    assert response.status_code == 200
    assert service.message == message
