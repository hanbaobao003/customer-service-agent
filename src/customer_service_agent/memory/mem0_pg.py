"""Customer-scoped Mem0 adapter backed by an isolated PGVector database."""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from customer_service_agent.memory.service import (
    DeleteResult,
    MemoryRecord,
    MemorySource,
    MemoryStoreUnavailable,
)


APPROVED_COLLECTION = "customer_memories_v1"
APPROVED_EMBEDDING_DIMS = 1024
_SQL_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_ENV_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHARED_DATABASE_REFS = {
    "DATABASE_URL",
    "ORDER_DATABASE_URL",
    "CHECKPOINT_DATABASE_URL",
}


class Mem0PgConfig(BaseModel):
    """Approved, non-secret PGVector settings.

    ``connection_string_ref`` stores only the environment variable name. The
    actual connection string is resolved at the application composition edge.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dbname: str
    collection_name: str = APPROVED_COLLECTION
    embedding_model_dims: int = APPROVED_EMBEDDING_DIMS
    connection_string_ref: str

    @field_validator("dbname", "collection_name")
    @classmethod
    def validate_sql_identifier(cls, value: str, info: Any) -> str:
        if not _SQL_IDENTIFIER.fullmatch(value):
            raise ValueError(f"{info.field_name} must be a static SQL identifier")
        return value

    @field_validator("connection_string_ref")
    @classmethod
    def validate_connection_reference(cls, value: str) -> str:
        if not _ENV_REFERENCE.fullmatch(value) or value in _SHARED_DATABASE_REFS:
            raise ValueError(
                "connection_string_ref must name the isolated Mem0 database secret"
            )
        return value

    @model_validator(mode="after")
    def validate_approved_vector_parameters(self) -> "Mem0PgConfig":
        if self.collection_name != APPROVED_COLLECTION:
            raise ValueError(
                f"collection_name must be the approved {APPROVED_COLLECTION!r}"
            )
        if self.embedding_model_dims != APPROVED_EMBEDDING_DIMS:
            raise ValueError(
                f"embedding_model_dims must be {APPROVED_EMBEDDING_DIMS}"
            )
        return self

    def to_mem0_config(
        self,
        *,
        connection_string: str,
        embedder_config: Mapping[str, object],
        history_db_path: str,
    ) -> dict[str, object]:
        if not connection_string:
            raise ValueError("connection_string must not be empty")
        if not history_db_path:
            raise ValueError("history_db_path must not be empty")
        return {
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "dbname": self.dbname,
                    "collection_name": self.collection_name,
                    "embedding_model_dims": self.embedding_model_dims,
                    "connection_string": connection_string,
                    "diskann": False,
                    "hnsw": True,
                },
            },
            "embedder": dict(embedder_config),
            "history_db_path": history_db_path,
        }


class AsyncMemoryPort(Protocol):
    async def add(self, messages: str, **kwargs: object) -> object: ...

    async def search(self, query: str, **kwargs: object) -> object: ...

    async def get_all(self, **kwargs: object) -> object: ...

    async def get(self, memory_id: str) -> object: ...

    async def delete(self, memory_id: str) -> object: ...

    async def update(self, memory_id: str, **kwargs: object) -> object: ...


class Mem0PgMemoryStore:
    """Map Mem0 responses to stable application records and enforce ownership."""

    def __init__(self, memory: AsyncMemoryPort) -> None:
        self._memory = memory

    async def add(self, *, customer_id: str, record: MemoryRecord) -> str:
        try:
            response = await self._memory.add(
                record.content,
                user_id=customer_id,
                metadata=_metadata(record),
                infer=False,
            )
            items = _result_items(response)
            memory_id = items[0].get("id") if items else None
            if not isinstance(memory_id, str) or not memory_id:
                raise ValueError("Mem0 add did not return a memory id")
            return memory_id
        except MemoryStoreUnavailable:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable from exc

    async def search(
        self,
        *,
        customer_id: str,
        query: str,
        limit: int,
    ) -> Sequence[MemoryRecord]:
        try:
            response = await self._memory.search(
                query,
                top_k=limit,
                filters={"user_id": customer_id},
            )
            return tuple(
                record
                for item in _result_items(response)
                if (record := _to_record(item)) is not None
                and record.customer_id == customer_id
            )
        except MemoryStoreUnavailable:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable from exc

    async def list(
        self,
        *,
        customer_id: str,
        category: str | None,
    ) -> Sequence[MemoryRecord]:
        try:
            response = await self._memory.get_all(
                filters={"user_id": customer_id},
                top_k=100,
            )
            records = tuple(
                record
                for item in _result_items(response)
                if (record := _to_record(item)) is not None
                and record.customer_id == customer_id
            )
            if category is None:
                return records
            return tuple(record for record in records if record.category == category)
        except MemoryStoreUnavailable:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable from exc

    async def delete(
        self,
        *,
        customer_id: str,
        memory_id: str,
    ) -> DeleteResult:
        try:
            existing = await self._owned_record(customer_id, memory_id)
            if existing is None:
                return DeleteResult(memory_id=memory_id, deleted=False)
            await self._memory.delete(memory_id)
            return DeleteResult(
                memory_id=memory_id,
                deleted=True,
                category=existing.category,
            )
        except MemoryStoreUnavailable:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable from exc

    async def replace(
        self,
        *,
        customer_id: str,
        existing_memory_id: str,
        replacement: MemoryRecord,
    ) -> str:
        try:
            existing = await self._owned_record(customer_id, existing_memory_id)
            if existing is None:
                raise ValueError("memory does not exist in customer scope")
            updated = replacement.model_copy(
                update={
                    "memory_id": existing_memory_id,
                    "customer_id": customer_id,
                    "created_at": existing.created_at,
                }
            )
            await self._memory.update(
                existing_memory_id,
                text=updated.content,
                metadata=_metadata(updated),
            )
            return existing_memory_id
        except MemoryStoreUnavailable:
            raise
        except Exception as exc:
            raise MemoryStoreUnavailable from exc

    async def _owned_record(
        self,
        customer_id: str,
        memory_id: str,
    ) -> MemoryRecord | None:
        item = await self._memory.get(memory_id)
        if not isinstance(item, Mapping):
            return None
        record = _to_record(item)
        if record is None or record.customer_id != customer_id:
            return None
        return record


def _metadata(record: MemoryRecord) -> dict[str, object]:
    return {
        "schema_version": record.schema_version,
        "kind": record.kind.value,
        "category": record.category,
        "source": record.source.model_dump(mode="json"),
        "app_created_at": record.created_at.isoformat(),
        "app_updated_at": record.updated_at.isoformat(),
    }


def _result_items(response: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(response, Mapping):
        return ()
    results = response.get("results")
    if not isinstance(results, list):
        return ()
    return tuple(item for item in results if isinstance(item, Mapping))


def _to_record(item: Mapping[str, object]) -> MemoryRecord | None:
    metadata = item.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    memory_id = item.get("id")
    customer_id = item.get("user_id")
    content = item.get("memory")
    source = metadata.get("source")
    if not all(isinstance(value, str) and value for value in (
        memory_id,
        customer_id,
        content,
    )) or not isinstance(source, Mapping):
        return None
    try:
        return MemoryRecord(
            memory_id=memory_id,
            customer_id=customer_id,
            kind=metadata["kind"],
            content=content,
            source=MemorySource.model_validate(source),
            created_at=datetime.fromisoformat(str(metadata["app_created_at"])),
            updated_at=datetime.fromisoformat(str(metadata["app_updated_at"])),
            category=metadata["category"],
            schema_version=metadata["schema_version"],
            score=item.get("score"),
        )
    except (KeyError, TypeError, ValueError):
        return None
