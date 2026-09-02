"""Application service boundary for customer conversations."""

import asyncio
import string
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncContextManager, Protocol

import yaml
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from customer_service_agent.agent_api.middleware import GovernanceMiddleware, RunLimits, WRITE_TOOL_NAMES
from customer_service_agent.shared.errors import ServiceError
from customer_service_agent.shared.models import AppEvent, EventSequencer, EventType, RuntimeContext


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
    ) -> AsyncIterator[AppEvent]: ...

    def resume(
        self,
        *,
        context: RuntimeContext,
        interrupt_id: str,
        decision: str,
        reason: str | None,
    ) -> AsyncIterator[AppEvent]: ...


class PostgresRuntimeAdapter:
    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL checkpoint DSN must not be blank")
        self._dsn = dsn

    @asynccontextmanager
    async def checkpointer(self) -> AsyncIterator[object]:
        async with AsyncPostgresSaver.from_conn_string(self._dsn) as saver:
            await saver.setup()
            yield saver


class LangGraphAgentRunner:
    """Adapts compiled LangGraph updates to the public application event contract."""

    def __init__(self, agent: object) -> None:
        self._agent = agent

    async def stream(
        self,
        *,
        context: RuntimeContext,
        message: str,
    ) -> AsyncIterator[AppEvent]:
        async for event in self._stream_events(
            {"messages": [HumanMessage(content=message)]},
            context,
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
        config = {"configurable": {"thread_id": context.thread_id}}
        state = await self._agent.aget_state(config)
        pending_interrupt_ids = {
            interrupt.id
            for task in state.tasks
            for interrupt in task.interrupts
        }
        if interrupt_id not in pending_interrupt_ids:
            raise ValueError("interrupt does not belong to the pending thread state")
        async for event in self._stream_events(
            build_hitl_resume(decision=decision, reason=reason),
            context,
        ):
            yield event

    async def _stream_events(
        self,
        agent_input: object,
        context: RuntimeContext,
    ) -> AsyncIterator[AppEvent]:
        sequencer = EventSequencer(
            thread_id=context.thread_id,
            request_id=context.request_id,
        )
        started_at: dict[str, tuple[str, float]] = {}
        stream = self._agent.astream(
            agent_input,
            config={"configurable": {"thread_id": context.thread_id}},
            stream_mode="updates",
        )
        async for update in stream:
            for event in self._events_from_update(update, sequencer, started_at):
                yield event

    @staticmethod
    def _events_from_update(
        update: object,
        sequencer: EventSequencer,
        started_at: dict[str, tuple[str, float]],
    ) -> tuple[AppEvent, ...]:
        if not isinstance(update, dict):
            return ()

        events: list[AppEvent] = []
        for node_output in update.values():
            if not isinstance(node_output, dict):
                continue
            messages = node_output.get("messages")
            if not isinstance(messages, list):
                continue
            for item in messages:
                if isinstance(item, AIMessage):
                    for tool_call in item.tool_calls:
                        tool_call_id = str(tool_call["id"])
                        started_at[tool_call_id] = (str(tool_call["name"]), time.monotonic())
                        events.append(
                            sequencer.emit(
                                EventType.TOOL_STARTED,
                                {
                                    "tool_call_id": tool_call_id,
                                    "tool_name": str(tool_call["name"]),
                                },
                            )
                        )
                    if not item.tool_calls and isinstance(item.content, str) and item.content:
                        events.append(sequencer.emit(EventType.MESSAGE_DELTA, {"text": item.content}))
                        events.append(
                            sequencer.emit(
                                EventType.MESSAGE_COMPLETED,
                                {
                                    "message": item.content,
                                    "citations": [],
                                    "usage_summary": {},
                                },
                            )
                        )
                elif isinstance(item, ToolMessage):
                    _, started = started_at.pop(item.tool_call_id, (item.name, time.monotonic()))
                    events.append(
                        sequencer.emit(
                            EventType.TOOL_COMPLETED,
                            {
                                "tool_call_id": item.tool_call_id,
                                "tool_name": item.name,
                                "status": item.status,
                                "duration_ms": round((time.monotonic() - started) * 1_000),
                            },
                        )
                    )
        interrupts = update.get("__interrupt__")
        if isinstance(interrupts, tuple):
            for interrupt in interrupts:
                events.extend(
                    LangGraphAgentRunner._approval_events(
                        interrupt,
                        sequencer,
                        started_at,
                    )
                )
        return tuple(events)

    @staticmethod
    def _approval_events(
        interrupt: object,
        sequencer: EventSequencer,
        started_at: dict[str, tuple[str, float]],
    ) -> tuple[AppEvent, ...]:
        value = getattr(interrupt, "value", None)
        interrupt_id = getattr(interrupt, "id", None)
        if not isinstance(value, dict) or not isinstance(interrupt_id, str):
            return ()
        action_requests = value.get("action_requests")
        if not isinstance(action_requests, list):
            return ()

        events: list[AppEvent] = []
        for action in action_requests:
            if not isinstance(action, dict) or not isinstance(action.get("name"), str):
                continue
            tool_name = action["name"]
            operation_id = next(
                (
                    tool_call_id
                    for tool_call_id, (started_name, _) in started_at.items()
                    if started_name == tool_name
                ),
                "",
            )
            events.append(
                sequencer.emit(
                    EventType.APPROVAL_REQUIRED,
                    {
                        "interrupt_id": interrupt_id,
                        "operation_id": operation_id,
                        "tool_name": tool_name,
                        "preview": {},
                        "allowed_decisions": ["approve", "reject"],
                    },
                )
            )
        return tuple(events)


class _ThreadCustomerMismatch(Exception):
    pass


class _ThreadBusy(Exception):
    pass


class PromptConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str | None = None
    version: str | None = None


class PromptCatalog:
    def __init__(
        self,
        *,
        agent_system: str,
        raptor_version: str,
        raptor_system: str,
        raptor_user: str,
    ) -> None:
        self._agent_system = agent_system
        self._raptor_version = raptor_version
        self._raptor_system = raptor_system
        self._raptor_user = raptor_user

    @classmethod
    def from_path(cls, path: Path) -> "PromptCatalog":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise PromptConfigError("prompt config cannot be read") from error
        except yaml.YAMLError as error:
            raise PromptConfigError("prompt config is invalid YAML") from error

        return cls(**_validate_prompt_payload(payload))

    def render_raptor_summary(
        self,
        *,
        source_ids: tuple[str, ...],
        content: str | None = None,
    ) -> RenderedPrompt:
        if content is None:
            raise PromptConfigError("missing placeholder: content")
        return RenderedPrompt(
            system=self._raptor_system,
            user=_render_template(
                self._raptor_user,
                allowed={"source_ids", "content"},
                values={
                    "source_ids": ", ".join(source_ids),
                    "content": content,
                },
            ),
            version=self._raptor_version,
        )

    def render_agent_system(
        self,
        *,
        trusted_context: str,
        tool_policy: str,
    ) -> RenderedPrompt:
        return RenderedPrompt(
            system=_render_template(
                self._agent_system,
                allowed={"trusted_context", "tool_policy"},
                values={
                    "trusted_context": trusted_context,
                    "tool_policy": tool_policy,
                },
            )
        )


def build_agent_prompt(
    catalog: PromptCatalog,
    *,
    context: RuntimeContext,
    tool_policy: str,
) -> RenderedPrompt:
    if not tool_policy.strip():
        raise PromptConfigError("tool policy must not be blank")
    return catalog.render_agent_system(
        trusted_context=f"服务渠道：{context.channel}；语言：{context.locale}。",
        tool_policy=tool_policy,
    )


def build_customer_service_agent(
    *,
    model: object,
    tools: tuple[object, ...],
    checkpointer: object,
    system_prompt: str,
    middleware: tuple[object, ...],
) -> object:
    return create_agent(
        model=model,
        tools=tools,
        checkpointer=checkpointer,
        system_prompt=system_prompt,
        middleware=middleware,
        name="customer_service_agent",
    )


def build_deepseek_agent_model(*, api_key: str, base_url: str) -> ChatOpenAI:
    if not api_key.strip() or not base_url.strip():
        raise ValueError("DeepSeek agent credentials must not be blank")
    return ChatOpenAI(
        model="deepseek-v4-flash",
        api_key=api_key,
        base_url=base_url,
        temperature=0,
    )


def build_agent_middleware(
    *,
    limits: RunLimits,
    max_read_retries: int,
) -> tuple[object, ...]:
    return (
        GovernanceMiddleware(limits=limits, max_read_retries=max_read_retries),
        HumanInTheLoopMiddleware(
            interrupt_on={name: True for name in WRITE_TOOL_NAMES},
        ),
    )


def build_hitl_resume(*, decision: str, reason: str | None) -> Command:
    if decision == "approve":
        resume_decision: dict[str, str] = {"type": "approve"}
    elif decision == "reject" and reason and reason.strip():
        resume_decision = {"type": "reject", "message": reason.strip()}
    else:
        raise ValueError("invalid HITL decision")
    return Command(resume={"decisions": [resume_decision]})


def _validate_prompt_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise PromptConfigError("prompt config version must be 1")
    agent = _mapping(payload, "agent")
    customer_service = _mapping(agent, "customer_service")
    retrieval = _mapping(payload, "retrieval")
    raptor_summary = _mapping(retrieval, "raptor_summary")

    agent_system = _text(customer_service, "system")
    raptor_version = _text(raptor_summary, "version")
    raptor_system = _text(raptor_summary, "system")
    raptor_user = _text(raptor_summary, "user")
    _validate_template(agent_system, {"trusted_context", "tool_policy"})
    _validate_template(raptor_system, set())
    _validate_template(raptor_user, {"source_ids", "content"})
    return {
        "agent_system": agent_system,
        "raptor_version": raptor_version,
        "raptor_system": raptor_system,
        "raptor_user": raptor_user,
    }


def _mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise PromptConfigError(f"missing mapping: {key}")
    return result


def _text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise PromptConfigError(f"missing text: {key}")
    return result


def _validate_template(template: str, allowed: set[str]) -> None:
    fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name is not None
    }
    unknown = fields - allowed
    if unknown:
        raise PromptConfigError(f"unknown placeholder: {sorted(unknown)[0]}")


def _render_template(
    template: str,
    *,
    allowed: set[str],
    values: dict[str, str],
) -> str:
    missing = allowed - values.keys()
    if missing:
        raise PromptConfigError(f"missing placeholder: {sorted(missing)[0]}")
    return template.format_map(values)


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
    ) -> AsyncIterator[AppEvent]:
        if not message.strip():
            raise ServiceError(
                code="INVALID_MESSAGE",
                message="消息不能为空",
                retryable=False,
                request_id=context.request_id,
            )

        await self._bind_thread(context)
        stream = self._agent.stream(context=context, message=message)
        async for event in self._stream_with_lock(context, stream):
            yield event

    async def resume_decision(
        self,
        context: RuntimeContext,
        *,
        interrupt_id: str,
        decision: str,
        reason: str | None,
    ) -> AsyncIterator[AppEvent]:
        await self._bind_thread(context)
        stream = self._agent.resume(
            context=context,
            interrupt_id=interrupt_id,
            decision=decision,
            reason=reason,
        )
        async for event in self._stream_with_lock(context, stream):
            yield event

    async def _bind_thread(self, context: RuntimeContext) -> None:
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

    async def _stream_with_lock(
        self,
        context: RuntimeContext,
        stream: AsyncIterator[AppEvent],
    ) -> AsyncIterator[AppEvent]:
        try:
            async with self._run_lock.acquire(context.thread_id):
                async for event in stream:
                    yield event
        except _ThreadBusy as error:
            raise ServiceError(
                code="THREAD_BUSY",
                message="当前会话正在处理其他请求",
                retryable=True,
                request_id=context.request_id,
                cause=error,
            ) from error
