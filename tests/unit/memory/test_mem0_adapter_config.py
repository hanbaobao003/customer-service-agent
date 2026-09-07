from datetime import UTC, datetime

import pytest

from customer_service_agent.memory.mem0_pg import (
    Mem0PgConfig,
    Mem0PgMemoryStore,
)
from customer_service_agent.memory.service import (
    MemoryKind,
    MemoryRecord,
    MemorySource,
    MemorySourceType,
    MemoryStoreUnavailable,
)


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def valid_config(**changes: object) -> Mem0PgConfig:
    values: dict[str, object] = {
        "dbname": "customer_memory",
        "collection_name": "customer_memories_v1",
        "embedding_model_dims": 1024,
        "connection_string_ref": "MEM0_DATABASE_URL",
    }
    values.update(changes)
    return Mem0PgConfig(**values)


def record(
    *,
    memory_id: str = "requested-id",
    customer_id: str = "customer-a",
    content: str = "偏好中文",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        customer_id=customer_id,
        kind=MemoryKind.PREFERENCE,
        content=content,
        source=MemorySource(
            type=MemorySourceType.EXPLICIT_USER_INSTRUCTION,
            thread_id="thread-1",
            request_id="request-1",
        ),
        created_at=NOW,
        updated_at=NOW,
        category="language",
    )


class FakeAsyncMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.items: dict[str, dict[str, object]] = {}
        self.fail_search = False

    async def add(self, messages: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("add", {"messages": messages, **kwargs}))
        item = {
            "id": "mem0-id",
            "memory": messages,
            "user_id": kwargs["user_id"],
            "metadata": kwargs["metadata"],
        }
        self.items["mem0-id"] = item
        return {"results": [{"id": "mem0-id", "memory": messages}]}

    async def search(self, query: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("search", {"query": query, **kwargs}))
        if self.fail_search:
            raise RuntimeError("database unavailable")
        customer_id = kwargs["filters"]["user_id"]  # type: ignore[index]
        return {
            "results": [
                item
                for item in self.items.values()
                if item["user_id"] == customer_id
            ]
        }

    async def get_all(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_all", kwargs))
        customer_id = kwargs["filters"]["user_id"]  # type: ignore[index]
        return {
            "results": [
                item
                for item in self.items.values()
                if item["user_id"] == customer_id
            ]
        }

    async def get(self, memory_id: str) -> dict[str, object] | None:
        self.calls.append(("get", {"memory_id": memory_id}))
        return self.items.get(memory_id)

    async def delete(self, memory_id: str) -> None:
        self.calls.append(("delete", {"memory_id": memory_id}))
        del self.items[memory_id]

    async def update(self, memory_id: str, **kwargs: object) -> None:
        self.calls.append(("update", {"memory_id": memory_id, **kwargs}))
        current = self.items[memory_id]
        current["memory"] = kwargs["text"]
        current["metadata"] = kwargs["metadata"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    ["memories;drop table orders", "customer/{id}", "", "customer-memories"],
)
def test_collection_name_must_be_static_sql_identifier(name: str) -> None:
    with pytest.raises(ValueError, match="collection_name"):
        valid_config(collection_name=name)


@pytest.mark.unit
def test_approved_vector_parameters_are_required() -> None:
    with pytest.raises(ValueError, match="embedding_model_dims"):
        valid_config(embedding_model_dims=1536)
    with pytest.raises(ValueError, match="collection_name"):
        valid_config(collection_name="another_valid_collection")

    mem0_config = valid_config().to_mem0_config(
        connection_string="postgresql://redacted",
        embedder_config={"provider": "langchain", "config": {"model": object()}},
        history_db_path="/tmp/mem0-history.db",
    )

    assert mem0_config["vector_store"] == {
        "provider": "pgvector",
        "config": {
            "dbname": "customer_memory",
            "collection_name": "customer_memories_v1",
            "embedding_model_dims": 1024,
            "connection_string": "postgresql://redacted",
            "diskann": False,
            "hnsw": True,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "reference",
    ["DATABASE_URL", "ORDER_DATABASE_URL", "CHECKPOINT_DATABASE_URL"],
)
def test_memory_database_reference_must_be_isolated(reference: str) -> None:
    with pytest.raises(ValueError, match="connection_string_ref"):
        valid_config(connection_string_ref=reference)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_and_queries_are_customer_scoped() -> None:
    memory = FakeAsyncMemory()
    store = Mem0PgMemoryStore(memory)

    memory_id = await store.add(customer_id="customer-a", record=record())
    searched = await store.search(customer_id="customer-a", query="中文", limit=3)
    listed_other = await store.list(customer_id="customer-b", category=None)

    assert memory_id == "mem0-id"
    assert searched[0].customer_id == "customer-a"
    assert listed_other == ()
    add_call = memory.calls[0]
    assert add_call[0] == "add"
    assert add_call[1]["user_id"] == "customer-a"
    assert add_call[1]["infer"] is False
    assert add_call[1]["metadata"]["schema_version"] == "1.0"  # type: ignore[index]
    assert ("search", {"query": "中文", "top_k": 3, "filters": {"user_id": "customer-a"}}) in memory.calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_checks_owner_and_hides_cross_customer_existence() -> None:
    memory = FakeAsyncMemory()
    store = Mem0PgMemoryStore(memory)
    await store.add(customer_id="customer-a", record=record())

    result = await store.delete(customer_id="customer-b", memory_id="mem0-id")

    assert result.deleted is False
    assert "mem0-id" in memory.items
    assert not any(call[0] == "delete" for call in memory.calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replace_preserves_id_and_uses_mem0_update() -> None:
    memory = FakeAsyncMemory()
    store = Mem0PgMemoryStore(memory)
    await store.add(customer_id="customer-a", record=record())

    memory_id = await store.replace(
        customer_id="customer-a",
        existing_memory_id="mem0-id",
        replacement=record(memory_id="ignored-new-id", content="偏好英文"),
    )

    assert memory_id == "mem0-id"
    assert memory.items["mem0-id"]["memory"] == "偏好英文"
    update_call = next(call for call in memory.calls if call[0] == "update")
    assert update_call[1]["memory_id"] == "mem0-id"
    assert "user_id" not in update_call[1]["metadata"]  # type: ignore[operator]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_store_failures_are_mapped_without_leaking_details() -> None:
    memory = FakeAsyncMemory()
    memory.fail_search = True
    store = Mem0PgMemoryStore(memory)

    with pytest.raises(MemoryStoreUnavailable) as exc:
        await store.search(customer_id="customer-a", query="中文", limit=3)

    assert str(exc.value) == ""
