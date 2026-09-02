import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from customer_service_agent.agent_api import middleware
from customer_service_agent.agent_api import service


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, _tools: object, **_kwargs: object) -> "ToolAwareFakeMessagesListChatModel":
        return self


def request(tool_name: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": {}, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=ToolRuntime(
            state={},
            context=None,
            config={},
            stream_writer=lambda _payload: None,
            tool_call_id="call-1",
            store=None,
        ),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_middleware_awaits_handler_and_returns_result() -> None:
    called = False

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = await middleware.GovernanceMiddleware(
        limits=middleware.RunLimits(model_calls=4, tool_calls=8)
    ).awrap_tool_call(request("web_search"), handler)

    assert called is True
    assert isinstance(result, Command)
    assert result.update["messages"][0].content == "ok"
    assert result.update["governance_tool_calls"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_tool_retries_a_transient_failure_once() -> None:
    attempts = 0

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary")
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = await middleware.GovernanceMiddleware(
        limits=middleware.RunLimits(model_calls=4, tool_calls=8),
        max_read_retries=1,
    ).awrap_tool_call(request("web_search"), handler)

    assert attempts == 2
    assert isinstance(result, Command)
    assert result.update["messages"][0].content == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_tool_does_not_retry_a_transient_failure() -> None:
    attempts = 0

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("temporary")

    with pytest.raises(ConnectionError):
        await middleware.GovernanceMiddleware(
            limits=middleware.RunLimits(model_calls=4, tool_calls=8),
            max_read_retries=1,
        ).awrap_tool_call(request("create_order"), handler)

    assert attempts == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_budget_blocks_handler_after_limit_is_reached() -> None:
    calls = 0
    guarded = middleware.GovernanceMiddleware(
        limits=middleware.RunLimits(model_calls=4, tool_calls=1),
        max_read_retries=0,
    )
    shared_request = request("web_search")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        return ToolMessage(content="ok", tool_call_id="call-1")

    first = await guarded.awrap_tool_call(shared_request, handler)
    assert isinstance(first, Command)
    shared_request.state.update(first.update)
    with pytest.raises(middleware.ToolBudgetExceeded):
        await guarded.awrap_tool_call(shared_request, handler)

    assert calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_budget_blocks_second_agent_model_call() -> None:
    @tool
    async def lookup() -> str:
        """Read a fixed test value."""
        return "found"

    agent = service.build_customer_service_agent(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "lookup", "args": {}, "id": "call-1"}],
                ),
                AIMessage(content="完成"),
            ]
        ),
        tools=(lookup,),
        checkpointer=InMemorySaver(),
        system_prompt="专业中文客服",
        middleware=service.build_agent_middleware(
            limits=middleware.RunLimits(model_calls=1, tool_calls=8),
            max_read_retries=1,
        ),
    )

    with pytest.raises(middleware.ModelBudgetExceeded):
        await agent.ainvoke(
            {"messages": [HumanMessage(content="查询")]},
            config={"configurable": {"thread_id": "model-limit"}},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_call_retries_transient_http_error_once() -> None:
    attempts = 0

    class UpstreamError(RuntimeError):
        status_code = 503

    async def handler(_request: ModelRequest) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise UpstreamError("temporary")
        return ModelResponse(result=[AIMessage(content="完成")])

    result = await middleware.GovernanceMiddleware(
        limits=middleware.RunLimits(model_calls=4, tool_calls=8),
        max_model_retries=1,
    ).awrap_model_call(
        ModelRequest(
            model=ToolAwareFakeMessagesListChatModel(responses=[]),
            messages=[],
            state={"messages": []},
        ),
        handler,
    )

    assert attempts == 2
    assert result.model_response.result[0].content == "完成"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_call_does_not_retry_authentication_error() -> None:
    attempts = 0

    class UpstreamError(RuntimeError):
        status_code = 401

    async def handler(_request: ModelRequest) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        raise UpstreamError("authentication failed")

    with pytest.raises(UpstreamError):
        await middleware.GovernanceMiddleware(
            limits=middleware.RunLimits(model_calls=4, tool_calls=8),
            max_model_retries=1,
        ).awrap_model_call(
            ModelRequest(
                model=ToolAwareFakeMessagesListChatModel(responses=[]),
                messages=[],
                state={"messages": []},
            ),
            handler,
        )

    assert attempts == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_budget_blocks_second_agent_tool_call() -> None:
    @tool
    async def lookup() -> str:
        """Read a fixed test value."""
        return "found"

    agent = service.build_customer_service_agent(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "lookup", "args": {}, "id": "call-1"}],
                ),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "lookup", "args": {}, "id": "call-2"}],
                ),
                AIMessage(content="完成"),
            ]
        ),
        tools=(lookup,),
        checkpointer=InMemorySaver(),
        system_prompt="专业中文客服",
        middleware=service.build_agent_middleware(
            limits=middleware.RunLimits(model_calls=4, tool_calls=1),
            max_read_retries=1,
        ),
    )

    with pytest.raises(middleware.ToolBudgetExceeded):
        await agent.ainvoke(
            {"messages": [HumanMessage(content="查询")]},
            config={"configurable": {"thread_id": "tool-limit"}},
        )


@pytest.mark.unit
def test_agent_factory_builds_one_named_agent_with_injected_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def create_agent(**kwargs: object) -> object:
        calls.append(kwargs)
        return "compiled-agent"

    monkeypatch.setattr(service, "create_agent", create_agent)

    result = service.build_customer_service_agent(
        model="scripted-model",
        tools=("tool-a",),
        checkpointer="checkpoint",
        system_prompt="专业中文客服",
        middleware=("governance",),
    )

    assert result == "compiled-agent"
    assert calls == [{
        "model": "scripted-model",
        "tools": ("tool-a",),
        "checkpointer": "checkpoint",
        "system_prompt": "专业中文客服",
        "middleware": ("governance",),
        "name": "customer_service_agent",
    }]


@pytest.mark.unit
def test_deepseek_agent_model_uses_approved_model_and_injected_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class ChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(service, "ChatOpenAI", ChatOpenAI)

    model = service.build_deepseek_agent_model(
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )

    assert isinstance(model, ChatOpenAI)
    assert calls == [{
        "model": "deepseek-v4-flash",
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
        "temperature": 0,
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_runtime_adapter_sets_up_before_yielding_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Saver:
        async def setup(self) -> None:
            events.append("setup")

    class SaverContext:
        async def __aenter__(self) -> Saver:
            events.append("enter")
            return Saver()

        async def __aexit__(self, *_args: object) -> None:
            events.append("exit")

    monkeypatch.setattr(
        service.AsyncPostgresSaver,
        "from_conn_string",
        lambda _dsn: SaverContext(),
    )
    adapter = service.PostgresRuntimeAdapter("postgresql://redacted")

    async with adapter.checkpointer() as saver:
        assert events == ["enter", "setup"]
        assert isinstance(saver, Saver)

    assert events == ["enter", "setup", "exit"]


@pytest.mark.unit
def test_hitl_resume_uses_langgraph_decisions_array() -> None:
    command = service.build_hitl_resume(decision="reject", reason="地址有误")

    assert isinstance(command, Command)
    assert command.resume == {
        "decisions": [{"type": "reject", "message": "地址有误"}]
    }


@pytest.mark.unit
def test_agent_middleware_requires_approval_for_exactly_write_tools() -> None:
    chain = service.build_agent_middleware(
        limits=middleware.RunLimits(model_calls=4, tool_calls=8),
        max_read_retries=1,
    )

    hitl = chain[1]

    assert set(hitl.interrupt_on) == middleware.WRITE_TOOL_NAMES
    assert all(
        {"approve", "reject"}.issubset(config["allowed_decisions"])
        for config in hitl.interrupt_on.values()
    )
