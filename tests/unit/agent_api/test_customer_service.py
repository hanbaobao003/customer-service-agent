import asyncio

import pytest

from customer_service_agent.agent_api.service import (
    CustomerService,
    InMemoryRunLock,
    InMemoryThreadBindings,
)
from customer_service_agent.shared.errors import ServiceError
from customer_service_agent.shared.models import RuntimeContext


def context(thread_id: str) -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id="customer-a",
        thread_id=thread_id,
        request_id=f"request-{thread_id}",
    )


async def collect(service: CustomerService, ctx: RuntimeContext, message: str = "你好"):
    return [event async for event in service.stream_message(ctx, message)]


class BlockingAgent:
    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.release = asyncio.Event()

    async def stream(self, *, context: RuntimeContext, message: str):
        self.started.setdefault(context.thread_id, asyncio.Event()).set()
        await self.release.wait()
        yield context.thread_id


class FailingIfCalledAgent:
    async def stream(self, *, context: RuntimeContext, message: str):
        raise AssertionError("agent must not be called")
        yield


class CancellableAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()

    async def stream(self, *, context: RuntimeContext, message: str):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await asyncio.Event().wait()
        yield "recovered"


class ResumableAgent:
    def __init__(self) -> None:
        self.resume_args = None

    async def stream(self, *, context: RuntimeContext, message: str):
        yield "unused"

    async def resume(
        self,
        *,
        context: RuntimeContext,
        interrupt_id: str,
        decision: str,
        reason: str | None,
    ):
        self.resume_args = (context, interrupt_id, decision, reason)
        yield "resumed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_thread_rejects_second_active_run() -> None:
    agent = BlockingAgent()
    service = CustomerService(
        bindings=InMemoryThreadBindings(),
        run_lock=InMemoryRunLock(),
        agent=agent,
    )
    first = asyncio.create_task(collect(service, context("thread-1")))
    while "thread-1" not in agent.started:
        await asyncio.sleep(0)

    try:
        with pytest.raises(ServiceError) as captured:
            await asyncio.wait_for(
                collect(service, context("thread-1")),
                timeout=0.05,
            )
        assert captured.value.code == "THREAD_BUSY"
    finally:
        agent.release.set()
        await first


@pytest.mark.unit
@pytest.mark.asyncio
async def test_different_threads_can_run_concurrently() -> None:
    agent = BlockingAgent()
    service = CustomerService(
        bindings=InMemoryThreadBindings(),
        run_lock=InMemoryRunLock(),
        agent=agent,
    )
    first = asyncio.create_task(collect(service, context("thread-1")))
    second = asyncio.create_task(collect(service, context("thread-2")))

    while set(agent.started) != {"thread-1", "thread-2"}:
        await asyncio.sleep(0)
    agent.release.set()

    assert await first == ["thread-1"]
    assert await second == ["thread-2"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blank_message_is_rejected_before_binding_or_agent_call() -> None:
    service = CustomerService(
        bindings=InMemoryThreadBindings(),
        run_lock=InMemoryRunLock(),
        agent=FailingIfCalledAgent(),
    )

    with pytest.raises(ServiceError) as captured:
        await collect(service, context("thread-1"), "  ")

    assert captured.value.code == "INVALID_MESSAGE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_cancellation_releases_thread_lock() -> None:
    agent = CancellableAgent()
    service = CustomerService(
        bindings=InMemoryThreadBindings(),
        run_lock=InMemoryRunLock(),
        agent=agent,
    )
    interrupted = asyncio.create_task(collect(service, context("thread-1")))
    await agent.started.wait()

    interrupted.cancel()
    with pytest.raises(asyncio.CancelledError):
        await interrupted

    assert await collect(service, context("thread-1")) == ["recovered"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_decision_uses_same_thread_authorization_and_lock() -> None:
    agent = ResumableAgent()
    service = CustomerService(
        bindings=InMemoryThreadBindings({"thread-1": "customer-a"}),
        run_lock=InMemoryRunLock(),
        agent=agent,
    )
    ctx = context("thread-1")

    result = [
        event
        async for event in service.resume_decision(
            ctx,
            interrupt_id="interrupt-1",
            decision="reject",
            reason="信息不正确",
        )
    ]

    assert result == ["resumed"]
    assert agent.resume_args == (ctx, "interrupt-1", "reject", "信息不正确")
