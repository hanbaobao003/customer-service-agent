from contextlib import asynccontextmanager

import pytest

from customer_service_agent.mvp import app as mvp_app
from customer_service_agent.mvp.app import (
    EXPECTED_TOOL_NAMES,
    MvpAgentRunner,
    create_mvp_app,
)
from customer_service_agent.mvp.settings import MvpSettings
from customer_service_agent.shared.models import EventType, RuntimeContext


@pytest.mark.contract
def test_real_composition_exposes_every_tool_name_without_connecting() -> None:
    settings = MvpSettings(
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid/v1",
        postgres_dsn="postgresql://demo",
        mem0_dsn="postgresql://memory",
        milvus_uri="http://127.0.0.1:19530",
        neo4j_uri="neo4j://127.0.0.1:7687",
        neo4j_username="neo4j",
        neo4j_password="test-password",
    )

    app = create_mvp_app(settings)

    assert app.state.tool_names == EXPECTED_TOOL_NAMES


@pytest.mark.contract
def test_composition_defers_external_service_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MvpSettings(
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid/v1",
        postgres_dsn="postgresql://demo",
        mem0_dsn="postgresql://memory",
        milvus_uri="http://127.0.0.1:19530",
        neo4j_uri="neo4j://127.0.0.1:7687",
        neo4j_username="neo4j",
        neo4j_password="test-password",
    )

    monkeypatch.setattr(
        mvp_app,
        "build_mvp_retrieval_service",
        lambda _settings: pytest.fail("service clients must be lazy"),
    )
    monkeypatch.setattr(
        mvp_app,
        "build_deepseek_agent_model",
        lambda **_kwargs: pytest.fail("model client must be lazy"),
    )

    app = create_mvp_app(settings)

    assert app.state.tool_names == EXPECTED_TOOL_NAMES


@pytest.mark.contract
@pytest.mark.asyncio
async def test_mvp_runner_executes_a_persisted_approval_by_operation_id() -> None:
    class Coordinator:
        async def execute_approved(self, context, *, operation_id, decision):
            assert context.customer_id == "demo-customer-a"
            assert operation_id == "operation-1"
            assert decision == "approve"
            return {"order_id": "MVP-ORDER-1001", "status": "cancelled"}

    runner = MvpAgentRunner(graph_factory=lambda: None, coordinator=Coordinator())
    events = [
        event
        async for event in runner.resume(
            context=RuntimeContext.trusted(
                customer_id="demo-customer-a",
                thread_id="thread-1",
                request_id="request-1",
            ),
            interrupt_id="operation-1",
            decision="approve",
            reason=None,
        )
    ]

    assert [event.event_type for event in events] == [
        EventType.MESSAGE_DELTA,
        EventType.MESSAGE_COMPLETED,
    ]
    assert events[-1].payload["message"] == "订单操作已完成：MVP-ORDER-1001（cancelled）。"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_mvp_runner_records_decision_in_the_agent_checkpoint() -> None:
    class Coordinator:
        async def execute_approved(self, _context, *, operation_id, decision):
            assert operation_id == "operation-1"
            assert decision == "reject"
            return {"order_id": "MVP-ORDER-1001", "status": "paid"}

    class Graph:
        def __init__(self) -> None:
            self.updates = []

        async def aupdate_state(self, config, values):
            self.updates.append((config, values))

    graph = Graph()

    @asynccontextmanager
    async def graph_session():
        yield graph

    runner = MvpAgentRunner(
        graph_factory=lambda: None,
        coordinator=Coordinator(),
        graph_session_factory=graph_session,
    )
    context = RuntimeContext.trusted(
        customer_id="demo-customer-a",
        thread_id="thread-1",
        request_id="request-1",
    )

    _ = [
        event
        async for event in runner.resume(
            context=context,
            interrupt_id="operation-1",
            decision="reject",
            reason="用户拒绝本次操作",
        )
    ]

    assert graph.updates[0][0] == {"configurable": {"thread_id": "thread-1"}}
    decision_message = graph.updates[0][1]["messages"][0]
    assert decision_message.content == "已取消本次订单操作，订单状态未改变。"
