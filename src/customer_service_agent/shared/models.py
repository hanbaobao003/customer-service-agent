"""Shared application value objects."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Identity and request metadata supplied by a trusted adapter."""

    customer_id: str
    thread_id: str
    request_id: str
    locale: str = "zh-CN"
    channel: Literal["api", "cli"] = "api"

    @classmethod
    def trusted(
        cls,
        *,
        customer_id: str,
        thread_id: str,
        request_id: str,
        locale: str = "zh-CN",
        channel: Literal["api", "cli"] = "api",
    ) -> "RuntimeContext":
        identifiers = {
            "customer_id": customer_id,
            "thread_id": thread_id,
            "request_id": request_id,
        }
        for name, value in identifiers.items():
            if not value.strip():
                raise ValueError(f"{name} must not be blank")

        return cls(
            customer_id=customer_id.strip(),
            thread_id=thread_id.strip(),
            request_id=request_id.strip(),
            locale=locale,
            channel=channel,
        )


class EventType(StrEnum):
    MESSAGE_DELTA = "message.delta"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    CITATION = "citation"
    APPROVAL_REQUIRED = "approval.required"
    HANDOFF_REQUIRED = "handoff.required"
    ERROR = "error"
    MESSAGE_COMPLETED = "message.completed"


class AppEvent(BaseModel):
    """A public application event safe to serialize to SSE."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    event_id: str
    event_type: EventType
    thread_id: str
    request_id: str
    sequence: int
    timestamp: datetime
    payload: dict[str, object]


_SENSITIVE_EVENT_FIELDS = {
    "api_key",
    "chain_of_thought",
    "customer_id",
    "prompt",
}
_TERMINAL_EVENT_TYPES = {
    EventType.APPROVAL_REQUIRED,
    EventType.HANDOFF_REQUIRED,
    EventType.MESSAGE_COMPLETED,
}


def _contains_sensitive_field(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _SENSITIVE_EVENT_FIELDS
            or _contains_sensitive_field(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_sensitive_field(item) for item in value)
    return False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _event_id() -> str:
    return str(uuid4())


class EventSequencer:
    def __init__(
        self,
        *,
        thread_id: str,
        request_id: str,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _event_id,
    ) -> None:
        self._thread_id = thread_id
        self._request_id = request_id
        self._clock = clock
        self._id_factory = id_factory
        self._sequence = 0
        self._terminated = False

    def emit(self, event_type: EventType, payload: dict[str, object]) -> AppEvent:
        if self._terminated:
            raise ValueError("event stream is already terminated")
        if _contains_sensitive_field(payload):
            raise ValueError("sensitive fields are not allowed in event payload")

        next_sequence = self._sequence + 1
        event = AppEvent(
            event_id=self._id_factory(),
            event_type=event_type,
            thread_id=self._thread_id,
            request_id=self._request_id,
            sequence=next_sequence,
            timestamp=self._clock(),
            payload=payload,
        )
        self._sequence = next_sequence
        if event_type in _TERMINAL_EVENT_TYPES or (
            event_type is EventType.ERROR and payload.get("retryable") is not True
        ):
            self._terminated = True
        return event
