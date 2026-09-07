import hashlib
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from langchain_core.embeddings import Embeddings

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


class DeterministicEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [((digest[index % len(digest)] / 255) * 2) - 1 for index in range(1024)]


def record(customer_id: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id="application-proposal-id",
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


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_mem0_pgvector_isolated_customer_lifecycle(
    postgres_memory_database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEM0_DIR", str(tmp_path / "mem0-runtime"))
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    from mem0 import AsyncMemory

    config = Mem0PgConfig(
        dbname=postgres_memory_database.dbname,
        collection_name="customer_memories_v1",
        embedding_model_dims=1024,
        connection_string_ref="MEM0_DATABASE_URL",
    )
    mem0_config = config.to_mem0_config(
        connection_string=postgres_memory_database.dsn,
        embedder_config={
            "provider": "langchain",
            "config": {"model": DeterministicEmbeddings()},
        },
        history_db_path=str(tmp_path / "history.db"),
    )
    mem0_config["llm"] = {
        "provider": "openai",
        "config": {
            "api_key": "integration-test-placeholder",
            "model": "not-called-with-infer-false",
            "openai_base_url": "http://127.0.0.1:9/v1",
        },
    }
    memory = AsyncMemory.from_config(mem0_config)
    store = Mem0PgMemoryStore(memory)
    try:
        customer_a_id = await store.add(
            customer_id="customer-a",
            record=record("customer-a", "偏好简洁中文"),
        )
        customer_b_id = await store.add(
            customer_id="customer-b",
            record=record("customer-b", "偏好详细中文"),
        )

        customer_a_results = await store.search(
            customer_id="customer-a",
            query="偏好简洁中文",
            limit=5,
        )
        customer_b_list = await store.list(customer_id="customer-b", category=None)
        assert {item.memory_id for item in customer_a_results} == {customer_a_id}
        assert {item.memory_id for item in customer_b_list} == {customer_b_id}

        with psycopg.connect(postgres_memory_database.dsn) as connection:
            vector_before_failure = connection.execute(
                "SELECT vector::text FROM customer_memories_v1 WHERE id = %s",
                (customer_a_id,),
            ).fetchone()
            connection.execute(
                "CREATE FUNCTION fail_memory_payload_update() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'injected payload failure'; END $$"
            )
            connection.execute(
                "CREATE TRIGGER fail_memory_payload_update "
                "BEFORE UPDATE OF payload ON customer_memories_v1 "
                "FOR EACH ROW EXECUTE FUNCTION fail_memory_payload_update()"
            )
            connection.commit()
        try:
            with pytest.raises(MemoryStoreUnavailable):
                await store.replace(
                    customer_id="customer-a",
                    existing_memory_id=customer_a_id,
                    replacement=record("customer-a", "不应落库的偏好"),
                )
        finally:
            with psycopg.connect(postgres_memory_database.dsn) as connection:
                connection.execute(
                    "DROP TRIGGER fail_memory_payload_update ON customer_memories_v1"
                )
                connection.execute("DROP FUNCTION fail_memory_payload_update()")
        with psycopg.connect(postgres_memory_database.dsn) as connection:
            vector_after_failure = connection.execute(
                "SELECT vector::text FROM customer_memories_v1 WHERE id = %s",
                (customer_a_id,),
            ).fetchone()
        assert vector_after_failure == vector_before_failure
        assert (await store.list(customer_id="customer-a", category=None))[0].content == "偏好简洁中文"

        preserved_id = await store.replace(
            customer_id="customer-a",
            existing_memory_id=customer_a_id,
            replacement=record("customer-a", "偏好简洁英文"),
        )
        assert preserved_id == customer_a_id
        assert (await store.list(customer_id="customer-a", category="language"))[0].content == "偏好简洁英文"

        cross_customer = await store.delete(
            customer_id="customer-b",
            memory_id=customer_a_id,
        )
        deleted = await store.delete(
            customer_id="customer-a",
            memory_id=customer_a_id,
        )
        repeated = await store.delete(
            customer_id="customer-a",
            memory_id=customer_a_id,
        )
        assert cross_customer.deleted is False
        assert deleted.deleted is True
        assert repeated.deleted is False

        with psycopg.connect(postgres_memory_database.dsn) as connection:
            vector_extension = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            hnsw_index = connection.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'customer_memories_v1' "
                "AND indexname = 'customer_memories_v1_hnsw_idx'"
            ).fetchone()
        assert vector_extension is not None
        assert hnsw_index == ("customer_memories_v1_hnsw_idx",)
    finally:
        memory.vector_store.connection_pool.close()
