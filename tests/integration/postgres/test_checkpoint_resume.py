import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from customer_service_agent.agent_api import middleware, service
from customer_service_agent.shared.models import EventType, RuntimeContext


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, _tools: object, **_kwargs: object) -> "ToolAwareFakeMessagesListChatModel":
        return self


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_agent_and_saver_rebuild(
    postgres_dsn: str,
) -> None:
    config = {"configurable": {"thread_id": "thread-1"}}

    async with AsyncPostgresSaver.from_conn_string(postgres_dsn) as saver:
        await saver.setup()
        first_agent = create_agent(
            model=FakeMessagesListChatModel(responses=[AIMessage(content="您好")]),
            tools=[],
            checkpointer=saver,
            name="customer_service_agent",
        )
        await first_agent.ainvoke(
            {"messages": [HumanMessage(content="你好")]},
            config=config,
        )

    async with AsyncPostgresSaver.from_conn_string(postgres_dsn) as rebuilt_saver:
        second_agent = create_agent(
            model=FakeMessagesListChatModel(responses=[AIMessage(content="继续为您服务")]),
            tools=[],
            checkpointer=rebuilt_saver,
            name="customer_service_agent",
        )
        result = await second_agent.ainvoke(
            {"messages": [HumanMessage(content="继续")]},
            config=config,
        )

    assert [message.content for message in result["messages"]] == [
        "你好",
        "您好",
        "继续",
        "继续为您服务",
    ]


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_resumes_hitl_after_runner_rebuild(
    postgres_dsn: str,
) -> None:
    executed: list[bool] = []

    @tool
    async def create_order() -> str:
        """Create a simulated order."""
        executed.append(True)
        return "created"

    adapter = service.PostgresRuntimeAdapter(postgres_dsn)
    first_context = RuntimeContext.trusted(
        customer_id="customer-a",
        thread_id="thread-hitl",
        request_id="request-1",
    )
    async with adapter.checkpointer() as saver:
        first_runner = service.LangGraphAgentRunner(
            service.build_customer_service_agent(
                model=ToolAwareFakeMessagesListChatModel(
                    responses=[
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "create_order", "args": {}, "id": "call-1"}
                            ],
                        )
                    ]
                ),
                tools=(create_order,),
                checkpointer=saver,
                system_prompt="专业中文客服",
                middleware=service.build_agent_middleware(
                    limits=middleware.RunLimits(model_calls=4, tool_calls=8),
                    max_read_retries=1,
                ),
            )
        )
        paused = [
            event async for event in first_runner.stream(context=first_context, message="创建订单")
        ]

    assert executed == []
    assert paused[-1].event_type is EventType.APPROVAL_REQUIRED

    resumed_context = RuntimeContext.trusted(
        customer_id="customer-a",
        thread_id="thread-hitl",
        request_id="request-2",
    )
    async with adapter.checkpointer() as rebuilt_saver:
        rebuilt_runner = service.LangGraphAgentRunner(
            service.build_customer_service_agent(
                model=ToolAwareFakeMessagesListChatModel(
                    responses=[AIMessage(content="订单已创建。")]
                ),
                tools=(create_order,),
                checkpointer=rebuilt_saver,
                system_prompt="专业中文客服",
                middleware=service.build_agent_middleware(
                    limits=middleware.RunLimits(model_calls=4, tool_calls=8),
                    max_read_retries=1,
                ),
            )
        )
        resumed = [
            event
            async for event in rebuilt_runner.resume(
                context=resumed_context,
                interrupt_id=str(paused[-1].payload["interrupt_id"]),
                decision="approve",
                reason=None,
            )
        ]

    assert executed == [True]
    assert resumed[-1].event_type is EventType.MESSAGE_COMPLETED
    assert resumed[-1].payload["message"] == "订单已创建。"
