"""Local, deterministic demo server for the portfolio project."""

from collections.abc import AsyncIterator, Mapping
from uuid import uuid4

from fastapi import Request

from customer_service_agent.agent_api.api import HealthProvider, TrustedContextProvider, create_app
from customer_service_agent.agent_api.service import CustomerService, InMemoryRunLock, InMemoryThreadBindings
from customer_service_agent.shared.models import AppEvent, EventSequencer, EventType, RuntimeContext


class DemoContextProvider(TrustedContextProvider):
    """Demo-only identity adapter; production identity must come from authentication."""

    async def get_customer_id(self, request: Request) -> str:
        return request.headers.get("X-Demo-Customer", "demo-customer-a")


class DemoHealth(HealthProvider):
    async def readiness(self) -> Mapping[str, bool]:
        return {"demo_memory": True}


class DemoAgent:
    """Small scripted agent that makes core API events easy to demonstrate."""

    def __init__(self) -> None:
        self._pending_interrupts: dict[str, str] = {}

    async def stream(
        self,
        *,
        context: RuntimeContext,
        message: str,
    ) -> AsyncIterator[AppEvent]:
        events = EventSequencer(thread_id=context.thread_id, request_id=context.request_id)
        if "取消" in message:
            interrupt_id = f"demo-interrupt-{uuid4()}"
            self._pending_interrupts[context.thread_id] = interrupt_id
            yield events.emit(
                EventType.APPROVAL_REQUIRED,
                {
                    "interrupt_id": interrupt_id,
                    "operation_id": "demo-operation-cancel-1001",
                    "tool_name": "cancel_order",
                    "preview": {"order_id": "DEMO-1001", "status": "pending_approval"},
                    "allowed_decisions": ["approve", "reject"],
                },
            )
            return

        if "保修" in message:
            yield events.emit(
                EventType.TOOL_STARTED,
                {"tool_call_id": "demo-faq-1", "tool_name": "search_product_faq"},
            )
            yield events.emit(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "demo-faq-1",
                    "tool_name": "search_product_faq",
                    "status": "success",
                    "duration_ms": 1,
                },
            )
            yield events.emit(
                EventType.CITATION,
                {"source_id": "faq-warranty-v1", "title": "商品保修说明"},
            )
            async for event in _complete(events, "演示商品提供 12 个月有限保修，详见引用说明。"):
                yield event
            return

        if "订单" in message:
            yield events.emit(
                EventType.TOOL_STARTED,
                {"tool_call_id": "demo-order-1", "tool_name": "get_order"},
            )
            yield events.emit(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "demo-order-1",
                    "tool_name": "get_order",
                    "status": "success",
                    "duration_ms": 1,
                },
            )
            async for event in _complete(events, "模拟订单 DEMO-1001：已付款，等待发货。"):
                yield event
            return

        async for event in _complete(
            events,
            "这是本地演示客服。可输入“查询订单”、“商品保修多久”或“取消订单”。",
        ):
            yield event

    async def resume(
        self,
        *,
        context: RuntimeContext,
        interrupt_id: str,
        decision: str,
        reason: str | None,
    ) -> AsyncIterator[AppEvent]:
        if self._pending_interrupts.get(context.thread_id) != interrupt_id:
            raise ValueError("demo interrupt does not belong to this thread")
        del self._pending_interrupts[context.thread_id]
        events = EventSequencer(thread_id=context.thread_id, request_id=context.request_id)
        if decision == "approve":
            text = "模拟订单 DEMO-1001 已取消。"
        else:
            text = f"已保留模拟订单 DEMO-1001。原因：{reason or '未提供'}。"
        async for event in _complete(events, text):
            yield event


async def _complete(
    events: EventSequencer,
    text: str,
) -> AsyncIterator[AppEvent]:
    yield events.emit(EventType.MESSAGE_DELTA, {"text": text})
    yield events.emit(
        EventType.MESSAGE_COMPLETED,
        {"message": text, "citations": [], "usage_summary": {}},
    )


def create_demo_app():
    """Create the explicitly non-production demo application."""

    return create_app(
        service=CustomerService(
            bindings=InMemoryThreadBindings(),
            run_lock=InMemoryRunLock(),
            agent=DemoAgent(),
        ),
        context_provider=DemoContextProvider(),
        health=DemoHealth(),
    )


app = create_demo_app()
