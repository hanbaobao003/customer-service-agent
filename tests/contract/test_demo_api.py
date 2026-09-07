import json

import pytest
from httpx import ASGITransport, AsyncClient

from customer_service_agent.demo import create_demo_app


def sse_events(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for frame in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.mark.contract
@pytest.mark.asyncio
async def test_demo_order_query_streams_tool_and_mock_order() -> None:
    app = create_demo_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/threads/demo-order/messages:stream",
            headers={"X-Demo-Customer": "demo-customer-a"},
            json={"message": "查询订单"},
        )

    events = sse_events(response.text)
    assert response.status_code == 200
    assert [name for name, _ in events] == [
        "tool.started",
        "tool.completed",
        "message.delta",
        "message.completed",
    ]
    assert "DEMO-1001" in events[-1][1]["payload"]["message"]


@pytest.mark.contract
@pytest.mark.asyncio
async def test_demo_warranty_streams_a_citation() -> None:
    app = create_demo_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/threads/demo-faq/messages:stream",
            json={"message": "商品保修多久"},
        )

    events = sse_events(response.text)
    assert response.status_code == 200
    assert [name for name, _ in events] == [
        "tool.started",
        "tool.completed",
        "citation",
        "message.delta",
        "message.completed",
    ]
    assert events[2][1]["payload"]["source_id"] == "faq-warranty-v1"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_demo_cancel_requires_approval_then_resumes() -> None:
    app = create_demo_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = await client.post(
            "/v1/threads/demo-cancel/messages:stream",
            headers={"X-Demo-Customer": "demo-customer-a"},
            json={"message": "取消订单"},
        )
        approval = sse_events(preview.text)[0][1]["payload"]
        resumed = await client.post(
            "/v1/threads/demo-cancel/decisions",
            headers={"X-Demo-Customer": "demo-customer-a"},
            json={
                "interrupt_id": approval["interrupt_id"],
                "decision": "approve",
            },
        )

    assert preview.status_code == 200
    assert sse_events(preview.text)[0][0] == "approval.required"
    assert sse_events(resumed.text)[-1][0] == "message.completed"
    assert "已取消" in sse_events(resumed.text)[-1][1]["payload"]["message"]
