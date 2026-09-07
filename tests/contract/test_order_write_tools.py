import json

import pytest
from langchain.tools import ToolRuntime

from customer_service_agent.commerce import orders
from customer_service_agent.shared.models import RuntimeContext


class PreviewCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RuntimeContext, str]] = []

    async def preview_create(self, context, *, interrupt_id, request):
        self.calls.append(("create_order", context, interrupt_id))
        return preview("create_order", interrupt_id)

    async def preview_update_contact(self, context, *, interrupt_id, request):
        self.calls.append(("update_order_contact", context, interrupt_id))
        return preview("update_order_contact", interrupt_id)

    async def preview_cancel(self, context, *, interrupt_id, request):
        self.calls.append(("cancel_order", context, interrupt_id))
        return preview("cancel_order", interrupt_id)

    async def preview_return(self, context, *, interrupt_id, request):
        self.calls.append(("request_return", context, interrupt_id))
        return preview("request_return", interrupt_id)


def preview(tool_name: str, approval_id: str) -> orders.OperationPreview:
    return orders.OperationPreview(
        operation_id="operation-1",
        customer_id="customer-a",
        thread_id="thread-1",
        request_id="request-1",
        interrupt_id=approval_id,
        tool_name=tool_name,
        normalized_args={"order_id": "order-1", "shipping_address": "private"},
        args_hash="hash-private",
        expected_version=3,
        status=orders.OperationStatus.PENDING,
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
        stream_writer=lambda _payload: None,
        tool_call_id="call-1",
        store=None,
        tools=[],
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_order_write_tools_create_preview_without_exposing_identity_or_arguments() -> None:
    commands = PreviewCommands()
    tools = {tool.name: tool for tool in orders.create_order_preview_tools(
        commands,
        approval_id_generator=lambda: "approval-1",
    )}

    assert set(tools) == {
        "create_order",
        "update_order_contact",
        "cancel_order",
        "request_return",
    }
    for tool in tools.values():
        schema = tool.tool_call_schema.model_json_schema()
        assert "customer_id" not in schema["properties"]
        assert "approval_id" not in schema["properties"]

    content, artifact = await tools["create_order"].coroutine(
        items=[{"product_id": "product-1", "quantity": 1}],
        contact_name="王小明",
        contact_phone="13812345678",
        shipping_address="上海市示例路 1 号",
        runtime=runtime(),
    )
    summary = json.loads(content)

    assert commands.calls == [("create_order", runtime().context, "approval-1")]
    assert summary == {
        "operation_id": "operation-1",
        "tool_name": "create_order",
        "status": "approval_required",
    }
    assert "customer-a" not in content
    assert "上海市" not in content
    assert artifact["approval_id"] == "approval-1"
    assert artifact["operation"]["args_hash"] == "hash-private"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_each_order_write_tool_routes_to_its_matching_preview() -> None:
    commands = PreviewCommands()
    tools = {tool.name: tool for tool in orders.create_order_preview_tools(
        commands,
        approval_id_generator=lambda: "approval-1",
    )}

    await tools["update_order_contact"].coroutine(
        order_id="order-1",
        shipping_address="北京市示例路 2 号",
        runtime=runtime(),
    )
    await tools["cancel_order"].coroutine(
        order_id="order-1",
        reason_code="changed_mind",
        runtime=runtime(),
    )
    await tools["request_return"].coroutine(
        order_id="order-1",
        reason_code="damaged",
        reason_text="商品破损",
        runtime=runtime(),
    )

    assert [name for name, _, _ in commands.calls] == [
        "update_order_contact",
        "cancel_order",
        "request_return",
    ]
