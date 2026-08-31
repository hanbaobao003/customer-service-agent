"""Long-term memory policy and application contracts."""

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class MemoryIntent(StrEnum):
    SAVE = "save"
    DELETE = "delete"
    NONE = "none"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
