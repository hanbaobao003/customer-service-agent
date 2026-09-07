from httpx import ASGITransport, AsyncClient
import pytest

from customer_service_agent.mvp.app import create_mvp_app
from customer_service_agent.mvp.settings import MvpSettings


@pytest.mark.contract
@pytest.mark.asyncio
async def test_root_has_message_tools_citations_and_approval_panels() -> None:
    app = create_mvp_app(
        MvpSettings(
            deepseek_api_key="test-key",
            deepseek_base_url="https://example.invalid/v1",
            postgres_dsn="postgresql://demo",
            mem0_dsn="postgresql://memory",
            milvus_uri="http://127.0.0.1:19530",
            neo4j_uri="neo4j://127.0.0.1:7687",
            neo4j_username="neo4j",
            neo4j_password="test-password",
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    for element in ("message-form", "tool-events", "citations", "approval"):
        assert f'id="{element}"' in response.text
