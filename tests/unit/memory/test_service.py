from datetime import UTC, datetime

import pytest

from customer_service_agent.memory.service import (
    DeleteResult,
    MemoryKind,
    MemoryPolicy,
    MemoryPolicyError,
    MemoryRecord,
    MemoryService,
    MemorySourceType,
    MemoryStoreUnavailable,
    MemoryVerificationRequired,
    RememberMemoryRequest,
)
from customer_service_agent.shared.models import RuntimeContext


NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


def context(customer_id: str = "customer-a") -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id=customer_id,
        thread_id="thread-1",
        request_id="request-1",
    )


class Store:
    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}
        self.add_count = 0
        self.unavailable = False

    async def add(self, *, customer_id: str, record: MemoryRecord) -> str:
        self.add_count += 1
        assert record.customer_id == customer_id
        self.records[record.memory_id] = record
        return record.memory_id

    async def search(self, *, customer_id: str, query: str, limit: int):
        if self.unavailable:
            raise MemoryStoreUnavailable
        return [
            record
            for record in self.records.values()
            if record.customer_id == customer_id and query in record.content
        ][:limit]

    async def list(self, *, customer_id: str, category: str | None):
        return [
            record
            for record in self.records.values()
            if record.customer_id == customer_id
            and (category is None or record.category == category)
        ]

    async def delete(self, *, customer_id: str, memory_id: str) -> DeleteResult:
        record = self.records.get(memory_id)
        if record is None or record.customer_id != customer_id:
            return DeleteResult(memory_id=memory_id, deleted=False)
        del self.records[memory_id]
        return DeleteResult(
            memory_id=memory_id,
            deleted=True,
            category=record.category,
        )


class Evidence:
    def __init__(self, verified: bool = False) -> None:
        self.verified = verified

    async def verified_at(self, **_: object) -> datetime | None:
        return NOW if self.verified else None


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, **event: object) -> None:
        self.events.append(event)


def service(
    store: Store,
    *,
    evidence: Evidence | None = None,
    audit: Audit | None = None,
) -> MemoryService:
    return MemoryService(
        store=store,
        policy=MemoryPolicy(),
        tool_evidence=evidence or Evidence(),
        audit=audit or Audit(),
        clock=lambda: NOW,
        id_generator=lambda: "memory-1",
    )


def preference(content: str = "偏好蓝色") -> RememberMemoryRequest:
    return RememberMemoryRequest(
        kind=MemoryKind.PREFERENCE,
        content=content,
        category="color",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ordinary_message_never_writes_memory() -> None:
    store = Store()

    with pytest.raises(MemoryPolicyError) as exc:
        await service(store).remember(
            context(),
            user_message="我喜欢蓝色",
            request=preference(),
        )

    assert exc.value.code == "MEMORY_EXPLICIT_INTENT_REQUIRED"
    assert store.add_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verified_fact_requires_registered_successful_tool_call() -> None:
    store = Store()
    request = RememberMemoryRequest(
        kind=MemoryKind.VERIFIED_FACT,
        content="订单已发货",
        category="order_history",
        verification={"tool_name": "get_order", "tool_call_id": "call-1"},
    )

    with pytest.raises(MemoryVerificationRequired) as exc:
        await service(store).remember(
            context(),
            user_message="以后请记住这个订单状态",
            request=request,
        )

    assert exc.value.code == "MEMORY_VERIFICATION_REQUIRED"
    assert store.add_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_preference_is_normalized_saved_and_audited() -> None:
    store = Store()
    audit = Audit()

    summary = await service(store, audit=audit).remember(
        context(),
        user_message="以后请记住我喜欢蓝色",
        request=preference("  偏好   蓝色  "),
    )

    assert summary.content == "偏好 蓝色"
    assert store.records["memory-1"].source.type is MemorySourceType.EXPLICIT_USER_INSTRUCTION
    assert audit.events == [{
        "action": "remember",
        "customer_id": "customer-a",
        "memory_id": "memory-1",
        "category": "color",
        "result": "created",
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_and_delete_are_customer_scoped_and_delete_is_idempotent() -> None:
    store = Store()
    await service(store).remember(
        context("customer-a"),
        user_message="以后请记住我喜欢蓝色",
        request=preference(),
    )

    assert await service(store).list(context("customer-b")) == ()
    first = await service(store).forget(
        context("customer-b"),
        user_message="请删除这条记忆",
        memory_id="memory-1",
    )
    second = await service(store).forget(
        context("customer-b"),
        user_message="请删除这条记忆",
        memory_id="memory-1",
    )

    assert first == second == DeleteResult(memory_id="memory-1", deleted=False)
    assert "memory-1" in store.records


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unavailable_recall_returns_safe_empty_context() -> None:
    store = Store()
    store.unavailable = True

    recalled = await service(store).recall(
        context(),
        query="蓝色",
        limit=3,
    )

    assert recalled.items == ()
    assert recalled.treat_as_data_not_instructions is True
    assert recalled.current_facts_require_tool_verification is True
    assert recalled.warning == "MEMORY_STORE_UNAVAILABLE"
