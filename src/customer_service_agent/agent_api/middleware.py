"""Agent governance middleware."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain.agents.middleware.types import PrivateStateAttr
from langchain.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import NotRequired

from customer_service_agent.shared.models import RuntimeContext


WRITE_TOOL_NAMES = frozenset(
    {
        "create_order",
        "update_order_contact",
        "cancel_order",
        "request_return",
        "remember_preference",
        "forget_memory",
    }
)


class ToolBudgetExceeded(RuntimeError):
    pass


class ModelBudgetExceeded(RuntimeError):
    pass


class TrustedContextMissing(PermissionError):
    pass


class GovernanceState(AgentState[Any]):
    governance_model_calls: NotRequired[Annotated[int, PrivateStateAttr]]
    governance_tool_calls: NotRequired[Annotated[int, PrivateStateAttr]]


@dataclass(frozen=True)
class RunLimits:
    model_calls: int
    tool_calls: int

    def __post_init__(self) -> None:
        if self.model_calls < 1 or self.tool_calls < 1:
            raise ValueError("run limits must be positive")


class GovernanceMiddleware(AgentMiddleware):
    state_schema = GovernanceState

    def __init__(
        self,
        *,
        limits: RunLimits,
        max_model_retries: int = 2,
        max_read_retries: int = 0,
    ) -> None:
        if max_model_retries < 0 or max_read_retries < 0:
            raise ValueError("retry limits must not be negative")
        self._limits = limits
        self._max_model_retries = max_model_retries
        self._max_read_retries = max_read_retries

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ExtendedModelResponse:
        calls = int(request.state.get("governance_model_calls", 0))
        if calls >= self._limits.model_calls:
            raise ModelBudgetExceeded("model call budget exceeded")
        retries_remaining = self._max_model_retries
        while True:
            try:
                response = await handler(request)
                return ExtendedModelResponse(
                    model_response=response,
                    command=Command(update={"governance_model_calls": calls + 1}),
                )
            except Exception as error:
                if retries_remaining == 0 or not _is_transient(error):
                    raise
                retries_remaining -= 1

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        calls = int(request.state.get("governance_tool_calls", 0))
        if calls >= self._limits.tool_calls:
            raise ToolBudgetExceeded("tool call budget exceeded")
        tool_name = str(request.tool_call["name"])
        retries_remaining = (
            0 if tool_name in WRITE_TOOL_NAMES else self._max_read_retries
        )
        while True:
            try:
                result = await handler(request)
                if isinstance(result, ToolMessage):
                    return Command(
                        update={
                            "messages": [result],
                            "governance_tool_calls": calls + 1,
                        }
                    )
                return result
            except Exception as error:
                if retries_remaining == 0 or not _is_transient(error):
                    raise
                retries_remaining -= 1


class RuntimeAuthorizationMiddleware(AgentMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if not isinstance(request.runtime.context, RuntimeContext):
            raise TrustedContextMissing("trusted runtime context is required")
        return await handler(request)


def _is_transient(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    status_code = getattr(error, "status_code", None)
    return isinstance(status_code, int) and (status_code == 429 or status_code >= 500)
