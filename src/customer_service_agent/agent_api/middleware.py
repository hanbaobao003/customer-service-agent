"""Agent governance middleware."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain.messages import ToolMessage
from langgraph.types import Command


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


@dataclass(frozen=True)
class RunLimits:
    model_calls: int
    tool_calls: int

    def __post_init__(self) -> None:
        if self.model_calls < 1 or self.tool_calls < 1:
            raise ValueError("run limits must be positive")


class GovernanceMiddleware(AgentMiddleware):
    def __init__(self, *, limits: RunLimits, max_read_retries: int = 0) -> None:
        if max_read_retries < 0:
            raise ValueError("max_read_retries must not be negative")
        self._limits = limits
        self._max_read_retries = max_read_retries

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        calls = int(request.state.get("governance_tool_calls", 0))
        if calls >= self._limits.tool_calls:
            raise ToolBudgetExceeded("tool call budget exceeded")
        request.state["governance_tool_calls"] = calls + 1
        tool_name = str(request.tool_call["name"])
        retries_remaining = (
            0 if tool_name in WRITE_TOOL_NAMES else self._max_read_retries
        )
        while True:
            try:
                return await handler(request)
            except (ConnectionError, TimeoutError):
                if retries_remaining == 0:
                    raise
                retries_remaining -= 1
