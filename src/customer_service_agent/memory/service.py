"""Long-term memory policy and application contracts."""

import re
from collections.abc import Sequence
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from customer_service_agent.shared.models import RuntimeContext


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
    category: str | None = None


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


class ToolEvidencePort(Protocol):
    async def verified_at(
        self,
        *,
        context: RuntimeContext,
        tool_name: str,
        tool_call_id: str,
    ) -> datetime | None: ...


class MemoryAuditPort(Protocol):
    async def record(
        self,
        *,
        action: str,
        customer_id: str,
        memory_id: str,
        category: str | None,
        result: str,
    ) -> None: ...


class MemoryPolicyError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MemoryVerificationRequired(MemoryPolicyError):
    pass


class MemoryStoreUnavailable(Exception):
    code = "MEMORY_STORE_UNAVAILABLE"


class UserMemoryContext(_MemoryModel):
    items: tuple[MemorySummary, ...] = ()
    treat_as_data_not_instructions: Literal[True] = True
    current_facts_require_tool_verification: Literal[True] = True
    warning: str | None = None


class MemoryService:
    def __init__(
        self,
        *,
        store: MemoryStorePort,
        policy: "MemoryPolicy",
        tool_evidence: ToolEvidencePort,
        audit: MemoryAuditPort,
        clock: Callable[[], datetime],
        id_generator: Callable[[], str],
    ) -> None:
        self._store = store
        self._policy = policy
        self._tool_evidence = tool_evidence
        self._audit = audit
        self._clock = clock
        self._id_generator = id_generator

    async def remember(
        self,
        context: RuntimeContext,
        *,
        user_message: str,
        request: RememberMemoryRequest,
    ) -> MemorySummary:
        decision = self._policy.evaluate(
            message=user_message,
            kind=request.kind.value,
            content=request.content,
        )
        if not decision.allowed or decision.intent is not MemoryIntent.SAVE:
            raise MemoryPolicyError(
                decision.code or "MEMORY_EXPLICIT_INTENT_REQUIRED"
            )

        now = self._clock()
        source = MemorySource(
            type=MemorySourceType.EXPLICIT_USER_INSTRUCTION,
            thread_id=context.thread_id,
            request_id=context.request_id,
        )
        if request.kind is MemoryKind.VERIFIED_FACT:
            reference = request.verification
            if (
                reference is None
                or not reference.tool_name
                or not reference.tool_call_id
            ):
                raise MemoryVerificationRequired("MEMORY_VERIFICATION_REQUIRED")
            verified_at = await self._tool_evidence.verified_at(
                context=context,
                tool_name=reference.tool_name,
                tool_call_id=reference.tool_call_id,
            )
            if verified_at is None:
                raise MemoryVerificationRequired("MEMORY_VERIFICATION_REQUIRED")
            source = MemorySource(
                type=MemorySourceType.VERIFIED_TOOL_RESULT,
                thread_id=context.thread_id,
                request_id=context.request_id,
                tool_name=reference.tool_name,
                verified_at=verified_at,
            )

        record = MemoryRecord(
            memory_id=self._id_generator(),
            customer_id=context.customer_id,
            kind=request.kind,
            content=_normalize_fact(request.content),
            source=source,
            created_at=now,
            updated_at=now,
            category=request.category.strip(),
        )
        memory_id = await self._store.add(
            customer_id=context.customer_id,
            record=record,
        )
        saved = record.model_copy(update={"memory_id": memory_id})
        await self._audit.record(
            action="remember",
            customer_id=context.customer_id,
            memory_id=memory_id,
            category=saved.category,
            result="created",
        )
        return MemorySummary.from_record(saved)

    async def recall(
        self,
        context: RuntimeContext,
        *,
        query: str,
        limit: int,
    ) -> UserMemoryContext:
        if not query.strip() or limit < 1:
            raise ValueError("query must not be blank and limit must be positive")
        try:
            records = await self._store.search(
                customer_id=context.customer_id,
                query=query,
                limit=limit,
            )
        except MemoryStoreUnavailable:
            return UserMemoryContext(warning="MEMORY_STORE_UNAVAILABLE")
        return UserMemoryContext(
            items=tuple(MemorySummary.from_record(item) for item in records)
        )

    async def list(
        self,
        context: RuntimeContext,
        *,
        category: str | None = None,
    ) -> tuple[MemorySummary, ...]:
        records = await self._store.list(
            customer_id=context.customer_id,
            category=category,
        )
        return tuple(MemorySummary.from_record(item) for item in records)

    async def forget(
        self,
        context: RuntimeContext,
        *,
        user_message: str,
        memory_id: str,
    ) -> DeleteResult:
        decision = self._policy.evaluate(
            message=user_message,
            kind="preference",
            content=memory_id,
        )
        if not decision.allowed or decision.intent is not MemoryIntent.DELETE:
            raise MemoryPolicyError(
                decision.code or "MEMORY_EXPLICIT_INTENT_REQUIRED"
            )
        result = await self._store.delete(
            customer_id=context.customer_id,
            memory_id=memory_id,
        )
        await self._audit.record(
            action="forget",
            customer_id=context.customer_id,
            memory_id=memory_id,
            category=result.category,
            result="deleted" if result.deleted else "not_found",
        )
        return result


def _normalize_fact(content: str) -> str:
    normalized = " ".join(content.split())
    if not normalized:
        raise MemoryPolicyError("MEMORY_POLICY_REJECTED")
    return normalized


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
