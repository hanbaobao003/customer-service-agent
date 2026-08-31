import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from langchain.tools import ToolRuntime

from customer_service_agent.commerce.orders import (
    Order,
    OrderService,
    OrderStatus,
    create_get_order_tool,
)
from customer_service_agent.shared.models import RuntimeContext


NOW = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)


class Repository:
    async def get(self, order_id: str, customer_id: str) -> Order | None:
        if order_id != "order-1" or customer_id != "customer-a":
            return None
        return Order(
            order_id=order_id,
            customer_id=customer_id,
            status=OrderStatus.PAID,
            contact_name="王小明",
            contact_phone="13812345678",
            shipping_address="上海市浦东新区示例路 100 号",
            currency="CNY",
            total_amount=Decimal("99.00"),
            items=(),
            created_at=NOW,
            updated_at=NOW,
            version=3,
        )


def runtime() -> ToolRuntime[RuntimeContext]:
    return ToolRuntime(
        state={},
        context=RuntimeContext.trusted(
            customer_id="customer-a",
            thread_id="thread-1",
            request_id="request-1",
        ),
        config={},
        stream_writer=lambda _: None,
        tool_call_id="call-1",
        store=None,
        tools=[],
    )


@pytest.mark.contract
def test_get_order_schema_only_exposes_order_id() -> None:
    service = OrderService(repo=Repository(), clock=lambda: NOW)
    order_tool = create_get_order_tool(service)

    schema = order_tool.tool_call_schema.model_json_schema()

    assert order_tool.name == "get_order"
    assert set(schema["properties"]) == {"order_id"}
    assert schema["required"] == ["order_id"]


@pytest.mark.contract
@pytest.mark.asyncio
async def test_get_order_tool_separates_summary_and_masked_artifact() -> None:
    service = OrderService(repo=Repository(), clock=lambda: NOW)
    order_tool = create_get_order_tool(service)

    content, artifact = await order_tool.coroutine(
        order_id="order-1",
        runtime=runtime(),
    )
    summary = json.loads(content)

    assert summary["status"] == "paid"
    assert summary["version"] == 3
    assert summary["queried_at"] == "2026-08-31T08:30:00Z"
    assert "customer_id" not in summary
    assert "13812345678" not in str(artifact)
    assert "上海市浦东新区示例路 100 号" not in str(artifact)
