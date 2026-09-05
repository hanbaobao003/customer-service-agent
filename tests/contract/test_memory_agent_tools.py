import json
from datetime import UTC, datetime

import pytest
from langchain.tools import ToolRuntime

from customer_service_agent.memory.service import (
    DeleteResult,
    MemoryKind,
    MemorySummary,
    create_memory_tools,
)
from customer_service_agent.shared.models import RuntimeContext


NOW = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)


class MemoryService:
    async def remember(self, context, *, user_message, request):
        assert context.customer_id == "demo-customer-a"
        assert "记住" in user_message
        return MemorySummary(
            memory_id="memory-1",
            kind=request.kind,
            content=request.content,
            category=request.category,
            updated_at=NOW,
        )

    async def list(self, context, *, category=None):
        assert context.customer_id == "demo-customer-a"
        return (
            MemorySummary(
                memory_id="memory-1",
                kind=MemoryKind.PREFERENCE,
                content="偏好简洁中文回答",
                category=category or "language",
                updated_at=NOW,
            ),
        )

    async def forget(self, context, *, user_message, memory_id):
        assert context.customer_id == "demo-customer-a"
        assert "忘记" in user_message
        return DeleteResult(memory_id=memory_id, deleted=True, category="language")


def runtime() -> ToolRuntime[RuntimeContext]:
    return ToolRuntime(
        state={"messages": [{"type": "human", "content": "请长期记住我偏好简洁中文回答"}]},
        context=RuntimeContext.trusted(
            customer_id="demo-customer-a",
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
@pytest.mark.asyncio
async def test_memory_tool_schemas_hide_customer_identity() -> None:
    tools = {item.name: item for item in create_memory_tools(MemoryService())}

    assert set(tools) == {"remember_preference", "list_memories", "forget_memory"}
    for item in tools.values():
        assert "customer_id" not in json.dumps(item.tool_call_schema.model_json_schema())

    content, artifact = await tools["remember_preference"].coroutine(
        content="偏好简洁中文回答",
        category="language",
        runtime=runtime(),
    )

    assert json.loads(content)["memory_id"] == "memory-1"
    assert artifact["memory_id"] == "memory-1"
