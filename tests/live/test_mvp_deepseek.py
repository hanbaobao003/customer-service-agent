import os
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest

from customer_service_agent.mvp.app import create_mvp_app_from_environment


@pytest.mark.live_model
@pytest.mark.asyncio
async def test_mvp_warranty_request_uses_faq_tool_and_completes() -> None:
    if os.environ.get("RUN_LIVE_MODEL_TESTS") != "1":
        pytest.skip("set RUN_LIVE_MODEL_TESTS=1 to call the local MVP with DeepSeek")

    app = create_mvp_app_from_environment()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/v1/threads/live-mvp-warranty-{uuid4()}/messages:stream",
            headers={"X-Demo-Customer": "demo-customer-a"},
            json={"message": "云端降噪耳机保修多久？"},
            timeout=90,
        )

    assert response.status_code == 200
    assert "event: tool.started" in response.text
    assert "search_product_faq" in response.text
    assert "event: message.completed" in response.text
