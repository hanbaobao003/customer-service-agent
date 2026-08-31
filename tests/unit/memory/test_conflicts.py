from datetime import UTC, datetime, timedelta

import pytest

from customer_service_agent.memory.service import (
    ConflictDetector,
    ConflictKind,
    DeleteResult,
    MemoryConflict,
    MemoryKind,
    MemoryPolicy,
    MemoryRecord,
    MemoryService,
    MemorySource,
    MemorySourceType,
    MemoryStoreUnavailable,
    RememberMemoryRequest,
)
from customer_service_agent.shared.models import RuntimeContext


INITIAL = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
LATER = INITIAL + timedelta(hours=1)


def context(customer_id: str = "customer-a") -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id=customer_id,
        thread_id="thread-2",
        request_id="request-2",
    )


def existing_record(content: str = "偏好中文") -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-old",
        customer_id="customer-a",
        kind=MemoryKind.PREFERENCE,
        content=content,
        source=MemorySource(
            type=MemorySourceType.EXPLICIT_USER_INSTRUCTION,
            thread_id="thread-1",
            request_id="request-1",
        ),
        created_at=INITIAL,
        updated_at=INITIAL,
        category="language",
    )


class Store:
    def __init__(self, record: MemoryRecord) -> None:
        self.records = {record.memory_id: record}
        self.fail_replace = False
        self.replace_count = 0

    async def add(self, *, customer_id: str, record: MemoryRecord) -> str:
        self.records[record.memory_id] = record
        return record.memory_id

    async def search(self, **_: object):
        return []

    async def list(self, *, customer_id: str, category: str | None):
        return [
            record
            for record in self.records.values()
            if record.customer_id == customer_id
            and (category is None or record.category == category)
        ]

    async def delete(self, *, customer_id: str, memory_id: str) -> DeleteResult:
        raise AssertionError("replacement must use the atomic store operation")

    async def replace(
        self,
        *,
        customer_id: str,
        existing_memory_id: str,
        replacement: MemoryRecord,
    ) -> str:
        self.replace_count += 1
        if self.fail_replace:
            raise MemoryStoreUnavailable
        assert self.records[existing_memory_id].customer_id == customer_id
        del self.records[existing_memory_id]
        self.records[replacement.memory_id] = replacement
        return replacement.memory_id


class Evidence:
    async def verified_at(self, **_: object):
        return None


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, **event: object) -> None:
        self.events.append(event)


def service(store: Store, audit: Audit | None = None) -> MemoryService:
    return MemoryService(
        store=store,
        policy=MemoryPolicy(),
        tool_evidence=Evidence(),
        audit=audit or Audit(),
        clock=lambda: LATER,
        id_generator=lambda: "memory-new",
    )


def preference(content: str) -> RememberMemoryRequest:
    return RememberMemoryRequest(
        kind=MemoryKind.PREFERENCE,
        content=content,
        category="language",
    )


@pytest.mark.unit
def test_detector_compares_only_same_customer_and_category() -> None:
    detector = ConflictDetector()
    existing = existing_record()

    assert detector.compare(existing, existing.model_copy()) is ConflictKind.DUPLICATE
    assert detector.compare(
        existing,
        existing.model_copy(update={"content": "偏好英文"}),
    ) is ConflictKind.CONFLICT
    assert detector.compare(
        existing,
        existing.model_copy(update={"customer_id": "customer-b"}),
    ) is ConflictKind.NONE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_opposite_preference_requires_explicit_replace() -> None:
    store = Store(existing_record())

    with pytest.raises(MemoryConflict) as exc:
        await service(store).remember(
            context(),
            user_message="以后请记住我偏好英文",
            request=preference("偏好英文"),
        )

    assert exc.value.existing.content == "偏好中文"
    assert [item.content for item in store.records.values()] == ["偏好中文"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_refreshes_existing_record_without_new_item() -> None:
    store = Store(existing_record())

    result = await service(store).remember(
        context(),
        user_message="以后请记住我偏好中文",
        request=preference("偏好中文"),
    )

    assert result.memory_id == "memory-old"
    assert result.updated_at == LATER
    assert list(store.records) == ["memory-old"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_replace_uses_atomic_store_contract_and_two_audits() -> None:
    store = Store(existing_record())
    audit = Audit()

    result = await service(store, audit).remember(
        context(),
        user_message="以后请记住我改为偏好英文，替换原偏好",
        request=preference("偏好英文"),
    )

    assert result.memory_id == "memory-new"
    assert store.replace_count == 1
    assert list(store.records) == ["memory-new"]
    assert [event["action"] for event in audit.events] == [
        "replace_delete",
        "replace_create",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_replace_preserves_existing_record() -> None:
    store = Store(existing_record())
    store.fail_replace = True

    with pytest.raises(MemoryStoreUnavailable):
        await service(store).remember(
            context(),
            user_message="以后请记住我改为偏好英文，替换原偏好",
            request=preference("偏好英文"),
        )

    assert list(store.records) == ["memory-old"]
    assert store.records["memory-old"].content == "偏好中文"
