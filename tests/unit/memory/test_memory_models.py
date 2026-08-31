from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from customer_service_agent.memory.service import (
    MemoryKind,
    MemoryRecord,
    MemorySource,
    MemorySourceType,
    MemorySummary,
)


NOW = datetime(2026, 8, 31, tzinfo=UTC)


def record() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-1",
        customer_id="customer-1",
        kind=MemoryKind.PREFERENCE,
        content="偏好使用中文回答",
        source=MemorySource(
            type=MemorySourceType.EXPLICIT_USER_INSTRUCTION,
            thread_id="thread-1",
            request_id="request-1",
        ),
        created_at=NOW,
        updated_at=NOW,
        category="language",
        embedding=(0.1, 0.2),
        score=0.92,
    )


@pytest.mark.unit
def test_memory_summary_never_exposes_internal_or_customer_fields() -> None:
    payload = MemorySummary.from_record(record()).model_dump(mode="json")

    assert payload["memory_id"] == "memory-1"
    assert "embedding" not in payload
    assert "score" not in payload
    assert "customer_id" not in payload
    assert "source" not in payload


@pytest.mark.unit
def test_verified_source_requires_tool_and_verification_time() -> None:
    with pytest.raises(ValidationError):
        MemorySource(
            type=MemorySourceType.VERIFIED_TOOL_RESULT,
            thread_id="thread-1",
            request_id="request-1",
        )


@pytest.mark.unit
def test_memory_dtos_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MemorySummary(
            memory_id="memory-1",
            kind=MemoryKind.PREFERENCE,
            content="偏好中文",
            category="language",
            updated_at=NOW,
            connection_string="secret",
        )
