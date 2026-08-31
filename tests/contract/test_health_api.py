import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from customer_service_agent.agent_api.api import create_app


class UnusedContextProvider:
    async def get_customer_id(self, request: Request) -> str:
        raise AssertionError("health checks do not need customer identity")


class HealthSpy:
    def __init__(self, dependencies: dict[str, bool]) -> None:
        self.dependencies = dependencies
        self.calls = 0

    async def readiness(self) -> dict[str, bool]:
        self.calls += 1
        return self.dependencies


@pytest.mark.contract
@pytest.mark.asyncio
async def test_liveness_does_not_call_dependencies() -> None:
    health = HealthSpy({"postgres": False})
    app = create_app(service=object(), context_provider=UnusedContextProvider(), health=health)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert health.calls == 0


@pytest.mark.contract
@pytest.mark.asyncio
async def test_readiness_returns_only_dependency_statuses() -> None:
    health = HealthSpy({"postgres": True, "milvus": False})
    app = create_app(service=object(), context_provider=UnusedContextProvider(), health=health)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"postgres": True, "milvus": False},
    }
    assert "postgresql://" not in response.text
    assert health.calls == 1
