"""Application service boundary for customer conversations."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import AsyncContextManager, Protocol

from customer_service_agent.shared.errors import ServiceError
from customer_service_agent.shared.models import RuntimeContext


class ThreadBindingRepository(Protocol):
    async def bind_or_validate(self, *, thread_id: str, customer_id: str) -> None: ...


class RunLock(Protocol):
    def acquire(self, thread_id: str) -> AsyncContextManager[None]: ...


class AgentRunner(Protocol):
    def stream(
        self,
        *,
        context: RuntimeContext,
        message: str,
    ) -> AsyncIterator[object]: ...


class _ThreadCustomerMismatch(Exception):
    pass


class _ThreadBusy(Exception):
    pass


class InMemoryThreadBindings:
    def __init__(self, bindings: dict[str, str] | None = None) -> None:
        self._bindings = dict(bindings or {})
        self._guard = asyncio.Lock()

    async def bind_or_validate(self, *, thread_id: str, customer_id: str) -> None:
        async with self._guard:
            bound_customer = self._bindings.get(thread_id)
            if bound_customer is None:
                self._bindings[thread_id] = customer_id
            elif bound_customer != customer_id:
                raise _ThreadCustomerMismatch


class InMemoryRunLock:
    def __init__(self) -> None:
        self._active_threads: set[str] = set()
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, thread_id: str) -> AsyncIterator[None]:
        async with self._guard:
            if thread_id in self._active_threads:
                raise _ThreadBusy
            self._active_threads.add(thread_id)
        try:
            yield
        finally:
            async with self._guard:
                self._active_threads.remove(thread_id)


class CustomerService:
    def __init__(
        self,
        *,
        bindings: ThreadBindingRepository,
        run_lock: RunLock,
        agent: AgentRunner,
    ) -> None:
        self._bindings = bindings
        self._run_lock = run_lock
        self._agent = agent

    async def stream_message(
        self,
        context: RuntimeContext,
        message: str,
    ) -> AsyncIterator[object]:
        if not message.strip():
            raise ServiceError(
                code="INVALID_MESSAGE",
                message="消息不能为空",
                retryable=False,
                request_id=context.request_id,
            )

        try:
            await self._bindings.bind_or_validate(
                thread_id=context.thread_id,
                customer_id=context.customer_id,
            )
        except _ThreadCustomerMismatch as error:
            raise ServiceError(
                code="THREAD_CUSTOMER_MISMATCH",
                message="当前客户无权访问该会话",
                retryable=False,
                request_id=context.request_id,
                cause=error,
            ) from error

        try:
            async with self._run_lock.acquire(context.thread_id):
                async for event in self._agent.stream(context=context, message=message):
                    yield event
        except _ThreadBusy as error:
            raise ServiceError(
                code="THREAD_BUSY",
                message="当前会话正在处理其他请求",
                retryable=True,
                request_id=context.request_id,
                cause=error,
            ) from error
