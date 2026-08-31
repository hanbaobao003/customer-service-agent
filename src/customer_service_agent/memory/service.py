"""Long-term memory policy and application contracts."""

import re
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryIntent(StrEnum):
    SAVE = "save"
    DELETE = "delete"
    NONE = "none"


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    VERIFIED_FACT = "verified_fact"


class MemorySourceType(StrEnum):
    EXPLICIT_USER_INSTRUCTION = "explicit_user_instruction"
    VERIFIED_TOOL_RESULT = "verified_tool_result"


class _MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemorySource(_MemoryModel):
    type: MemorySourceType
    thread_id: str
    request_id: str
    tool_name: str | None = None
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_verified_source(self) -> "MemorySource":
        if self.type is MemorySourceType.VERIFIED_TOOL_RESULT:
            if not self.tool_name or self.verified_at is None:
                raise ValueError(
                    "verified tool source requires tool_name and verified_at"
                )
            if self.verified_at.utcoffset() is None:
                raise ValueError("verified_at must be timezone-aware")
        return self


class MemoryRecord(_MemoryModel):
    memory_id: str
    customer_id: str
    kind: MemoryKind
    content: str
    source: MemorySource
    created_at: datetime
    updated_at: datetime
    category: str
    schema_version: Literal["1.0"] = "1.0"
    embedding: tuple[float, ...] | None = Field(default=None, exclude=True)
    score: float | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_kind_source(self) -> "MemoryRecord":
        if (
            self.kind is MemoryKind.VERIFIED_FACT
            and self.source.type is not MemorySourceType.VERIFIED_TOOL_RESULT
        ):
            raise ValueError("verified_fact requires verified tool source")
        return self


class MemorySummary(_MemoryModel):
    memory_id: str
    kind: MemoryKind
    content: str
    category: str
    updated_at: datetime

    @classmethod
    def from_record(cls, record: MemoryRecord) -> "MemorySummary":
        return cls(
            memory_id=record.memory_id,
            kind=record.kind,
            content=record.content,
            category=record.category,
            updated_at=record.updated_at,
        )


class VerificationReference(_MemoryModel):
    tool_name: str | None = None
    tool_call_id: str | None = None


class RememberMemoryRequest(_MemoryModel):
    kind: MemoryKind
    content: str
    category: str
    verification: VerificationReference | None = None


class ListMemoriesRequest(_MemoryModel):
    category: str | None = None


class ForgetMemoryRequest(_MemoryModel):
    memory_id: str


class DeleteResult(_MemoryModel):
    memory_id: str
    deleted: bool


class MemoryStorePort(Protocol):
    async def add(self, *, customer_id: str, record: MemoryRecord) -> str: ...

    async def search(
        self,
        *,
        customer_id: str,
        query: str,
        limit: int,
    ) -> Sequence[MemoryRecord]: ...

    async def list(
        self,
        *,
        customer_id: str,
        category: str | None,
    ) -> Sequence[MemoryRecord]: ...

    async def delete(
        self,
        *,
        customer_id: str,
        memory_id: str,
    ) -> DeleteResult: ...


class PolicyDecision(_MemoryModel):
    intent: MemoryIntent
    allowed: bool
    code: str | None = None
    sensitive_category: str | None = None


class MemoryPolicy:
    def evaluate(
        self,
        *,
        message: str,
        kind: Literal["preference", "verified_fact"],
        content: str,
    ) -> PolicyDecision:
        intent = _detect_intent(message)
        if intent is MemoryIntent.NONE:
            return PolicyDecision(
                intent=intent,
                allowed=False,
                code="MEMORY_EXPLICIT_INTENT_REQUIRED",
            )
        if intent is MemoryIntent.DELETE:
            return PolicyDecision(intent=intent, allowed=True)

        sensitive_category = _detect_sensitive_category(content)
        if sensitive_category is not None or not content.strip():
            return PolicyDecision(
                intent=intent,
                allowed=False,
                code="MEMORY_POLICY_REJECTED",
                sensitive_category=sensitive_category,
            )
        return PolicyDecision(intent=intent, allowed=True)


_LONG_TERM_MARKERS = ("以后", "今后", "长期", "一直", "每次")
_SAVE_VERBS = ("记住", "保存", "记下来")
_DELETE_VERBS = ("忘记", "删除", "清除")


def _detect_intent(message: str) -> MemoryIntent:
    lowered = message.lower()
    if any(verb in lowered for verb in _DELETE_VERBS):
        return MemoryIntent.DELETE
    if any(marker in lowered for marker in _LONG_TERM_MARKERS) and any(
        verb in lowered for verb in _SAVE_VERBS
    ):
        return MemoryIntent.SAVE
    return MemoryIntent.NONE


_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "verification_code",
        re.compile(r"(?:验证码|verification\s*code).{0,8}\b\d{4,8}\b", re.I),
    ),
    ("api_key", re.compile(r"(?:api[ _-]?key|\bsk-[a-z0-9_-]{8,})", re.I)),
    ("password", re.compile(r"(?:密码|password|口令)", re.I)),
    (
        "identity_document",
        re.compile(r"(?:身份证|identity\s*(?:card|number)).{0,8}\d{17}[0-9xX]", re.I),
    ),
    (
        "payment_card",
        re.compile(r"(?:支付卡|银行卡|card).{0,8}(?:\d[ -]?){13,19}", re.I),
    ),
    (
        "internal_reasoning",
        re.compile(r"(?:chain[-_ ]of[-_ ]thought|思维链|系统提示词|system\s*prompt)", re.I),
    ),
)


def _detect_sensitive_category(content: str) -> str | None:
    for category, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(content):
            return category
    return None
