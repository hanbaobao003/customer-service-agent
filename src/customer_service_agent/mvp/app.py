"""Composition root for the local portfolio MVP."""

import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from langchain_core.messages import AIMessage
from pymilvus import MilvusClient

from customer_service_agent.agent_api.api import (
    HealthProvider,
    TrustedContextProvider,
    create_app,
)
from customer_service_agent.agent_api.middleware import (
    GovernanceMiddleware,
    RunLimits,
    RuntimeAuthorizationMiddleware,
)
from customer_service_agent.agent_api.service import (
    CustomerService,
    InMemoryRunLock,
    InMemoryThreadBindings,
    LangGraphAgentRunner,
    PostgresRuntimeAdapter,
    PromptCatalog,
    build_agent_prompt,
    build_customer_service_agent,
    build_deepseek_agent_model,
)
from customer_service_agent.commerce.orders import OrderApprovalCoordinator
from customer_service_agent.commerce.sql import create_query_business_data_tool
from customer_service_agent.mvp.services import (
    build_mvp_memory_tools,
    build_mvp_order_tools,
    build_mvp_retrieval_service,
    build_mvp_sql_service,
)
from customer_service_agent.mvp.settings import MvpSettings
from customer_service_agent.retrieval.tools import create_retrieval_tools
from customer_service_agent.shared.models import AppEvent, EventSequencer, EventType, RuntimeContext


EXPECTED_TOOL_NAMES = frozenset(
    {
        "web_search",
        "search_product_faq",
        "search_policy_raptor",
        "search_commerce_graph",
        "query_business_data",
        "get_order",
        "create_order",
        "update_order_contact",
        "cancel_order",
        "request_return",
        "remember_preference",
        "list_memories",
        "forget_memory",
    }
)


class MvpAgentRunner:
    """Run the real graph and execute persisted order previews from the decision API."""

    def __init__(
        self,
        *,
        graph_factory: Callable[[], object],
        coordinator: object | None = None,
        coordinator_factory: Callable[[], object] | None = None,
        graph_session_factory: Callable[[], object] | None = None,
    ) -> None:
        self._graph_factory = graph_factory
        self._coordinator = coordinator
        self._coordinator_factory = coordinator_factory
        self._graph_session_factory = graph_session_factory

    async def stream(
        self,
        *,
        context: RuntimeContext,
        message: str,
    ) -> AsyncIterator[AppEvent]:
        if self._graph_session_factory is None:
            runner = LangGraphAgentRunner(self._graph_factory())
            async for event in runner.stream(context=context, message=message):
                yield event
            return
        async with self._graph_session_factory() as graph:
            runner = LangGraphAgentRunner(graph)
            async for event in runner.stream(context=context, message=message):
                yield event

    async def resume(
        self,
        *,
        context: RuntimeContext,
        interrupt_id: str,
        decision: Literal["approve", "reject"],
        reason: str | None,
    ) -> AsyncIterator[AppEvent]:
        coordinator = self._coordinator or (
            self._coordinator_factory() if self._coordinator_factory is not None else None
        )
        if coordinator is None:
            raise RuntimeError("MVP order approval coordinator is not configured")
        result = await coordinator.execute_approved(
            context,
            operation_id=interrupt_id,
            decision=decision,
        )
        text = _decision_message(decision, result)
        if self._graph_session_factory is not None:
            async with self._graph_session_factory() as graph:
                await graph.aupdate_state(
                    {"configurable": {"thread_id": context.thread_id}},
                    {"messages": [AIMessage(content=text)]},
                )
        events = EventSequencer(thread_id=context.thread_id, request_id=context.request_id)
        yield events.emit(EventType.MESSAGE_DELTA, {"text": text})
        yield events.emit(
            EventType.MESSAGE_COMPLETED,
            {"message": text, "citations": [], "usage_summary": {}},
        )


class _DemoContextProvider(TrustedContextProvider):
    async def get_customer_id(self, request: Request) -> str:
        return request.headers.get("X-Demo-Customer", "demo-customer-a")


class _MvpHealth(HealthProvider):
    def __init__(self, settings: MvpSettings) -> None:
        self._settings = settings

    async def readiness(self) -> Mapping[str, bool]:
        return {
            "postgres": await self._postgres_ready(),
            "milvus": await _sync_ready(self._milvus_ready),
        }

    async def _postgres_ready(self) -> bool:
        try:
            async with await psycopg.AsyncConnection.connect(self._settings.postgres_dsn) as connection:
                await connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _milvus_ready(self) -> bool:
        try:
            return MilvusClient(uri=self._settings.milvus_uri).has_collection(
                "wang_agent_mvp_faq_v1"
            )
        except Exception:
            return False


def create_mvp_app(settings: MvpSettings) -> FastAPI:
    """Create the browser MVP; connections are opened only while handling requests."""
    prompt = _load_system_prompt()
    middleware = (
        RuntimeAuthorizationMiddleware(),
        GovernanceMiddleware(limits=RunLimits(model_calls=6, tool_calls=12)),
    )
    runtime = PostgresRuntimeAdapter(settings.postgres_dsn)

    def build_tools() -> tuple[tuple[object, ...], object]:
        retrieval = build_mvp_retrieval_service(settings)
        order_bundle = build_mvp_order_tools(settings)
        memory_bundle = build_mvp_memory_tools(
            settings,
            history_db_path=Path(".mvp-runtime/mem0-history.db"),
        )
        tools = (
            *create_retrieval_tools(retrieval),
            create_query_business_data_tool(build_mvp_sql_service(settings.postgres_dsn)),
            *order_bundle.tools,
            *memory_bundle.tools,
        )
        if frozenset(str(tool.name) for tool in tools) != EXPECTED_TOOL_NAMES:
            raise ValueError("MVP tool composition does not match the public contract")
        return tools, order_bundle

    @asynccontextmanager
    async def graph_session() -> AsyncIterator[object]:
        async with runtime.checkpointer() as checkpointer:
            tools, _ = build_tools()
            yield build_customer_service_agent(
                model=build_deepseek_agent_model(
                    api_key=settings.deepseek_api_key,
                    base_url=settings.deepseek_base_url,
                ),
                tools=tools,
                checkpointer=checkpointer,
                system_prompt=prompt,
                middleware=middleware,
            )

    service = CustomerService(
        bindings=InMemoryThreadBindings(),
        run_lock=InMemoryRunLock(),
        agent=MvpAgentRunner(
            graph_factory=lambda: None,
            graph_session_factory=graph_session,
            coordinator_factory=lambda: _build_order_coordinator(settings),
        ),
    )
    app = create_app(
        service=service,
        context_provider=_DemoContextProvider(),
        health=_MvpHealth(settings),
    )
    app.title = "Customer Service Agent MVP"
    app.state.settings = settings
    app.state.tool_names = EXPECTED_TOOL_NAMES

    @app.get("/", include_in_schema=False)
    async def page() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    return app


def _build_order_coordinator(settings: MvpSettings) -> OrderApprovalCoordinator:
    order_bundle = build_mvp_order_tools(settings)
    return OrderApprovalCoordinator(
        operations=order_bundle.operations,
        commands=order_bundle.commands,
    )


def create_mvp_app_from_environment() -> FastAPI:
    return create_mvp_app(MvpSettings.from_environment(os.environ))


def _load_system_prompt() -> str:
    catalog = PromptCatalog.from_path(
        Path(__file__).resolve().parents[3] / "config" / "prompts.yaml"
    )
    return build_agent_prompt(
        catalog,
        context=RuntimeContext.trusted(
            customer_id="composition-only",
            thread_id="composition-only",
            request_id="composition-only",
        ),
        tool_policy="优先使用已提供工具；订单写操作必须在用户审批后执行。",
    ).system


def _decision_message(decision: str, result: object) -> str:
    if decision == "reject":
        return "已取消本次订单操作，订单状态未改变。"
    if not isinstance(result, dict):
        return "订单操作已完成。"
    order_id = result.get("order_id")
    status = result.get("status")
    if isinstance(order_id, str) and isinstance(status, str):
        return f"订单操作已完成：{order_id}（{status}）。"
    return "订单操作已完成。"


async def _sync_ready(check: Callable[[], bool]) -> bool:
    import asyncio

    return await asyncio.to_thread(check)
