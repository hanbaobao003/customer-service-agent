import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver

from customer_service_agent.agent_api import middleware, service
from customer_service_agent.shared.models import EventType, RuntimeContext


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    """Small scripted model that accepts tool binding for an Agent loop test."""

    def bind_tools(self, _tools: object, **_kwargs: object) -> "ToolAwareFakeMessagesListChatModel":
        return self


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_tool_waits_for_approval_then_resumes_same_thread() -> None:
    executed: list[bool] = []

    @tool
    async def create_order() -> str:
        """Create a simulated order."""
        executed.append(True)
        return "created"

    agent = service.build_customer_service_agent(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "create_order", "args": {}, "id": "call-1"}],
                ),
                AIMessage(content="订单已创建。"),
            ]
        ),
        tools=(create_order,),
        checkpointer=InMemorySaver(),
        system_prompt="专业中文客服",
        middleware=service.build_agent_middleware(
            limits=middleware.RunLimits(model_calls=4, tool_calls=8),
            max_read_retries=1,
        ),
    )
    config = {"configurable": {"thread_id": "hitl-test-thread"}}
    context = RuntimeContext.trusted(
        customer_id="customer-a",
        thread_id="hitl-test-thread",
        request_id="request-hitl-test",
    )

    paused = await agent.ainvoke(
        {"messages": [HumanMessage(content="创建订单")]},
        config=config,
        context=context,
    )

    assert paused["__interrupt__"]
    assert executed == []

    resumed = await agent.ainvoke(
        service.build_hitl_resume(decision="approve", reason=None),
        config=config,
        context=context,
    )

    assert executed == [True]
    assert resumed["messages"][-1].content == "订单已创建。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_adapts_tool_progress_and_final_answer() -> None:
    class ScriptedGraph:
        async def astream(self, _input: object, **_kwargs: object):
            yield {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "create_order", "args": {}, "id": "call-1"}
                            ],
                        )
                    ]
                }
            }
            yield {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="created",
                            tool_call_id="call-1",
                            name="create_order",
                        )
                    ]
                }
            }
            yield {"model": {"messages": [AIMessage(content="订单已创建。")]}}

    runner = service.LangGraphAgentRunner(ScriptedGraph())
    events = [
        event
        async for event in runner.stream(
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-1",
                request_id="request-1",
            ),
            message="创建订单",
        )
    ]

    assert [event.event_type for event in events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.MESSAGE_DELTA,
        EventType.MESSAGE_COMPLETED,
    ]
    assert events[0].payload == {"tool_call_id": "call-1", "tool_name": "create_order"}
    assert events[1].payload["status"] == "success"
    assert events[1].payload["duration_ms"] >= 0
    assert events[-1].payload == {
        "message": "订单已创建。",
        "citations": [],
        "usage_summary": {},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_emits_safe_citations_from_tool_artifact() -> None:
    class ScriptedGraph:
        async def astream(self, _input: object, **_kwargs: object):
            yield {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="证据摘要",
                            tool_call_id="call-1",
                            name="search_product_faq",
                            artifact={
                                "citations": [
                                    {
                                        "id": "faq-1",
                                        "source_id": "faq",
                                        "locator": "warranty",
                                        "title": "保修说明",
                                    }
                                ],
                                "retrieval": {"raw_sql": "must-not-leak"},
                            },
                        )
                    ]
                }
            }

    runner = service.LangGraphAgentRunner(ScriptedGraph())
    events = [
        event
        async for event in runner.stream(
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-1",
                request_id="request-1",
            ),
            message="保修多久",
        )
    ]

    assert [event.event_type for event in events] == [
        EventType.TOOL_COMPLETED,
        EventType.CITATION,
    ]
    assert events[-1].payload == {
        "id": "faq-1",
        "source_id": "faq",
        "locator": "warranty",
        "title": "保修说明",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_stops_at_persisted_order_preview() -> None:
    class ScriptedGraph:
        async def astream(self, _input: object, **_kwargs: object):
            yield {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "cancel_order", "args": {}, "id": "call-1"}
                            ],
                        )
                    ]
                }
            }
            yield {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="等待审批",
                            tool_call_id="call-1",
                            name="cancel_order",
                            artifact={
                                "approval_id": "preview-opaque-id",
                                "operation": {
                                    "operation_id": "operation-1",
                                    "tool_name": "cancel_order",
                                    "normalized_args": {"order_id": "MVP-ORDER-1001"},
                                },
                            },
                        )
                    ]
                }
            }
            yield {"model": {"messages": [AIMessage(content="不应继续生成")]} }

    runner = service.LangGraphAgentRunner(ScriptedGraph())
    events = [
        event
        async for event in runner.stream(
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-1",
                request_id="request-1",
            ),
            message="取消订单 MVP-ORDER-1001",
        )
    ]

    assert [event.event_type for event in events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.APPROVAL_REQUIRED,
    ]
    assert events[-1].payload == {
        "interrupt_id": "operation-1",
        "operation_id": "operation-1",
        "tool_name": "cancel_order",
        "preview": {},
        "allowed_decisions": ["approve", "reject"],
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_emits_redacted_approval_event_for_hitl_interrupt() -> None:
    @tool
    async def create_order(sku: str) -> str:
        """Create a simulated order."""
        return sku

    graph = service.build_customer_service_agent(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_order",
                            "args": {"sku": "sku-secret"},
                            "id": "call-1",
                        }
                    ],
                )
            ]
        ),
        tools=(create_order,),
        checkpointer=InMemorySaver(),
        system_prompt="专业中文客服",
        middleware=service.build_agent_middleware(
            limits=middleware.RunLimits(model_calls=4, tool_calls=8),
            max_read_retries=1,
        ),
    )
    runner = service.LangGraphAgentRunner(graph)

    events = [
        event
        async for event in runner.stream(
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-1",
                request_id="request-1",
            ),
            message="创建订单",
        )
    ]

    assert [event.event_type for event in events] == [
        EventType.TOOL_STARTED,
        EventType.APPROVAL_REQUIRED,
    ]
    approval = events[-1].payload
    assert approval["operation_id"] == "call-1"
    assert approval["tool_name"] == "create_order"
    assert approval["allowed_decisions"] == ["approve", "reject"]
    assert "sku-secret" not in str(approval)
    assert approval["interrupt_id"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_resumes_only_the_pending_thread_interrupt() -> None:
    executed: list[bool] = []

    @tool
    async def create_order() -> str:
        """Create a simulated order."""
        executed.append(True)
        return "created"

    graph = service.build_customer_service_agent(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "create_order", "args": {}, "id": "call-1"}],
                ),
                AIMessage(content="订单已创建。"),
            ]
        ),
        tools=(create_order,),
        checkpointer=InMemorySaver(),
        system_prompt="专业中文客服",
        middleware=service.build_agent_middleware(
            limits=middleware.RunLimits(model_calls=4, tool_calls=8),
            max_read_retries=1,
        ),
    )
    runner = service.LangGraphAgentRunner(graph)
    context = RuntimeContext.trusted(
        customer_id="customer-a",
        thread_id="thread-1",
        request_id="request-1",
    )

    paused = [event async for event in runner.stream(context=context, message="创建订单")]
    interrupt_id = str(paused[-1].payload["interrupt_id"])
    resumed = [
        event
        async for event in runner.resume(
            context=context,
            interrupt_id=interrupt_id,
            decision="approve",
            reason=None,
        )
    ]

    assert executed == [True]
    assert [event.event_type for event in resumed] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.MESSAGE_DELTA,
        EventType.MESSAGE_COMPLETED,
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_converts_call_budget_exhaustion_to_handoff() -> None:
    @tool
    async def lookup() -> str:
        """Read a fixed test value."""
        return "found"

    runner = service.LangGraphAgentRunner(
        service.build_customer_service_agent(
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
    )

    events = [
        event
        async for event in runner.stream(
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-limit",
                request_id="request-limit",
            ),
            message="查询",
        )
    ]

    assert events[-1].event_type is EventType.HANDOFF_REQUIRED
    assert events[-1].payload == {
        "reason_code": "AGENT_EXECUTION_LIMIT",
        "summary": "为保障服务稳定，本次请求需要人工协助。",
        "completed_steps": [],
        "suggested_next_step": "请转人工处理。",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_langgraph_runner_passes_trusted_context_to_tool_runtime() -> None:
    observed_customer_ids: list[str] = []

    @tool
    async def get_order(runtime: ToolRuntime[RuntimeContext]) -> str:
        """Read a simulated order for the trusted customer."""
        assert runtime.context is not None
        observed_customer_ids.append(runtime.context.customer_id)
        return "found"

    runner = service.LangGraphAgentRunner(
        service.build_customer_service_agent(
            model=ToolAwareFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "get_order", "args": {}, "id": "call-1"}],
                    ),
                    AIMessage(content="订单已找到。"),
                ]
            ),
            tools=(get_order,),
            checkpointer=InMemorySaver(),
            system_prompt="专业中文客服",
            middleware=service.build_agent_middleware(
                limits=middleware.RunLimits(model_calls=4, tool_calls=8),
                max_read_retries=1,
            ),
        )
    )

    events = [
        event
        async for event in runner.stream(
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-context",
                request_id="request-context",
            ),
            message="查询订单",
        )
    ]

    assert observed_customer_ids == ["customer-a"]
    assert events[-1].event_type is EventType.MESSAGE_COMPLETED
