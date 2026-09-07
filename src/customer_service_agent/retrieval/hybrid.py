"""Hybrid retrieval and small-to-big expansion."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pymilvus import DataType, Function, FunctionType

from customer_service_agent.retrieval.models import RetrievalArtifact, RetrievalHit


class HybridConfigError(ValueError):
    pass


class HybridIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class FusionConfig:
    method: str
    dense_weight: Decimal
    sparse_weight: Decimal
    candidate_k: int
    final_k: int

    def __post_init__(self) -> None:
        if self.method != "weighted_reciprocal_rank":
            raise HybridConfigError("unsupported fusion method")
        if self.dense_weight < 0 or self.sparse_weight < 0:
            raise HybridConfigError("fusion weights must not be negative")
        if self.dense_weight == 0 and self.sparse_weight == 0:
            raise HybridConfigError("at least one fusion weight must be positive")
        if self.candidate_k < 1 or self.final_k < 1:
            raise HybridConfigError("candidate_k and final_k must be positive")
        if self.final_k > self.candidate_k:
            raise HybridConfigError("final_k must not exceed candidate_k")


@dataclass(frozen=True)
class SearchHit:
    child_id: str
    parent_id: str
    source_id: str
    score: Decimal
    raw_text: str
    locator: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class FusedHit:
    child_id: str
    parent_id: str
    source_id: str
    raw_text: str
    locator: str
    metadata: Mapping[str, object]
    dense_rank: int | None
    sparse_rank: int | None
    dense_score: Decimal | None
    sparse_score: Decimal | None
    fusion_score: Decimal


@dataclass
class _Candidate:
    hit: SearchHit
    fusion_score: Decimal = Decimal("0")
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: Decimal | None = None
    sparse_score: Decimal | None = None


@dataclass(frozen=True)
class ParentDocument:
    parent_id: str
    source_id: str
    title: str
    text: str
    locator: str


@dataclass(frozen=True)
class HybridIndexDocument:
    child_id: str
    parent_id: str
    source_id: str
    title: str
    child_text: str
    parent_text: str
    child_locator: str
    parent_locator: str
    data_version: str
    dense_vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.dense_vector) != 1024:
            raise HybridConfigError("dense vector must have 1024 dimensions")
        if not all(
            (
                self.child_id,
                self.parent_id,
                self.source_id,
                self.title,
                self.child_text,
                self.parent_text,
                self.child_locator,
                self.parent_locator,
                self.data_version,
            )
        ):
            raise HybridConfigError("hybrid index document fields must not be empty")


@dataclass(frozen=True)
class HybridLocator:
    child_id: str
    child_locator: str
    parent_id: str
    parent_locator: str


@dataclass(frozen=True)
class ExpandedEvidence:
    citation_id: str
    text: str
    source_label: str
    locator: HybridLocator


class HybridSearchPort(Protocol):
    async def search_dense(self, query: str, *, limit: int) -> Sequence[SearchHit]: ...

    async def search_sparse(self, query: str, *, limit: int) -> Sequence[SearchHit]: ...


class ParentStorePort(Protocol):
    def get(self, parent_id: str) -> ParentDocument | None: ...


def create_hybrid_collection(client: object, collection_name: str) -> None:
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(
        field_name="child_id",
        datatype=DataType.VARCHAR,
        is_primary=True,
        max_length=256,
    )
    for field_name in (
        "parent_id",
        "source_id",
        "title",
        "child_locator",
        "parent_locator",
        "data_version",
    ):
        schema.add_field(field_name=field_name, datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(
        field_name="child_text",
        datatype=DataType.VARCHAR,
        max_length=65535,
        enable_analyzer=True,
        analyzer_params={"type": "chinese"},
    )
    schema.add_field(
        field_name="parent_text",
        datatype=DataType.VARCHAR,
        max_length=65535,
    )
    schema.add_field(
        field_name="dense_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=1024,
    )
    schema.add_field(
        field_name="sparse_vector",
        datatype=DataType.SPARSE_FLOAT_VECTOR,
    )
    schema.add_function(
        Function(
            name="bm25",
            function_type=FunctionType.BM25,
            input_field_names=["child_text"],
            output_field_names=["sparse_vector"],
        )
    )
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="dense_vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    index_params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
    )
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )


class MilvusHybridIndex:
    def __init__(
        self,
        *,
        client: object,
        collection_name: str,
        data_version: str,
        embed_query: Callable[[str], Sequence[float]],
    ) -> None:
        if not collection_name or not data_version:
            raise HybridConfigError("collection name and data version must not be empty")
        self._client = client
        self._collection_name = collection_name
        self._data_version = data_version
        self._embed_query = embed_query

    def insert(self, documents: Sequence[HybridIndexDocument]) -> None:
        self._client.insert(
            collection_name=self._collection_name,
            data=[
                {
                    "child_id": item.child_id,
                    "parent_id": item.parent_id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "child_text": item.child_text,
                    "parent_text": item.parent_text,
                    "child_locator": item.child_locator,
                    "parent_locator": item.parent_locator,
                    "data_version": item.data_version,
                    "dense_vector": list(item.dense_vector),
                }
                for item in documents
            ],
        )
        self._client.flush(self._collection_name)

    async def search_dense(self, query: str, *, limit: int) -> Sequence[SearchHit]:
        return self._search(
            anns_field="dense_vector",
            data=[list(self._embed_query(query))],
            limit=limit,
            search_params={"metric_type": "COSINE", "params": {}},
        )

    async def search_sparse(self, query: str, *, limit: int) -> Sequence[SearchHit]:
        return self._search(
            anns_field="sparse_vector",
            data=[query],
            limit=limit,
            search_params={"metric_type": "BM25", "params": {}},
        )

    def get(self, parent_id: str) -> ParentDocument | None:
        rows = self._client.query(
            collection_name=self._collection_name,
            filter=(
                f'parent_id == {_milvus_string(parent_id)} and '
                f'data_version == {_milvus_string(self._data_version)}'
            ),
            output_fields=[
                "parent_id",
                "source_id",
                "title",
                "parent_text",
                "parent_locator",
            ],
        )
        if not rows:
            return None
        first = rows[0]
        required = ("source_id", "title", "parent_text", "parent_locator")
        if any(first.get(field) is None for field in required):
            raise HybridIntegrityError("parent document is incomplete")
        parent = ParentDocument(
            parent_id=parent_id,
            source_id=str(first["source_id"]),
            title=str(first["title"]),
            text=str(first["parent_text"]),
            locator=str(first["parent_locator"]),
        )
        if any(
            (
                str(row.get("source_id")) != parent.source_id
                or str(row.get("title")) != parent.title
                or str(row.get("parent_text")) != parent.text
                or str(row.get("parent_locator")) != parent.locator
            )
            for row in rows[1:]
        ):
            raise HybridIntegrityError(f"conflicting parent: {parent_id}")
        return parent

    def _search(
        self,
        *,
        anns_field: str,
        data: list[object],
        limit: int,
        search_params: dict[str, object],
    ) -> list[SearchHit]:
        if limit < 1:
            raise HybridConfigError("search limit must be positive")
        result = self._client.search(
            collection_name=self._collection_name,
            data=data,
            anns_field=anns_field,
            filter=f'data_version == {_milvus_string(self._data_version)}',
            limit=limit,
            output_fields=[
                "child_id",
                "parent_id",
                "source_id",
                "child_text",
                "child_locator",
                "data_version",
            ],
            search_params=search_params,
        )
        return [_search_hit(item) for item in result[0]]


def _search_hit(item: Mapping[str, object]) -> SearchHit:
    entity = item.get("entity")
    row = entity if isinstance(entity, Mapping) else item
    return SearchHit(
        child_id=str(row["child_id"]),
        parent_id=str(row["parent_id"]),
        source_id=str(row["source_id"]),
        score=Decimal(str(item["distance"])),
        raw_text=str(row["child_text"]),
        locator=str(row["child_locator"]),
        metadata={"data_version": row["data_version"]},
    )


def _milvus_string(value: str) -> str:
    return f'"{value.replace("\\", "\\\\").replace('"', '\\"')}"'


def fuse_hits(
    *,
    dense: Sequence[SearchHit],
    sparse: Sequence[SearchHit],
    config: FusionConfig,
) -> list[FusedHit]:
    candidates: dict[str, _Candidate] = {}
    _merge_route(
        candidates,
        hits=dense,
        route="dense",
        weight=config.dense_weight,
        limit=config.candidate_k,
    )
    _merge_route(
        candidates,
        hits=sparse,
        route="sparse",
        weight=config.sparse_weight,
        limit=config.candidate_k,
    )

    fused = [
        FusedHit(
            child_id=child_id,
            parent_id=item.hit.parent_id,
            source_id=item.hit.source_id,
            raw_text=item.hit.raw_text,
            locator=item.hit.locator,
            metadata=item.hit.metadata,
            dense_rank=item.dense_rank,
            sparse_rank=item.sparse_rank,
            dense_score=item.dense_score,
            sparse_score=item.sparse_score,
            fusion_score=item.fusion_score,
        )
        for child_id, item in candidates.items()
    ]
    fused.sort(
        key=lambda item: (
            -item.fusion_score,
            min(
                rank
                for rank in (item.dense_rank, item.sparse_rank)
                if rank is not None
            ),
            item.child_id,
        )
    )
    return fused[: config.final_k]


def expand_parents(
    hits: Sequence[FusedHit],
    parent_store: ParentStorePort,
) -> list[ExpandedEvidence]:
    expanded: list[ExpandedEvidence] = []
    seen_parents: set[str] = set()
    for hit in hits:
        if hit.parent_id in seen_parents:
            continue
        parent = parent_store.get(hit.parent_id)
        if parent is None:
            raise HybridIntegrityError(f"missing parent: {hit.parent_id}")
        if parent.source_id != hit.source_id:
            raise HybridIntegrityError(
                f"parent source mismatch for child: {hit.child_id}"
            )
        seen_parents.add(hit.parent_id)
        expanded.append(
            ExpandedEvidence(
                citation_id=f"{parent.source_id}#{parent.locator}",
                text=parent.text,
                source_label=parent.title,
                locator=HybridLocator(
                    child_id=hit.child_id,
                    child_locator=hit.locator,
                    parent_id=parent.parent_id,
                    parent_locator=parent.locator,
                ),
            )
        )
    return expanded


def build_hybrid_artifact(
    *,
    query: str,
    index_version: str,
    dense: Sequence[SearchHit],
    sparse: Sequence[SearchHit],
    fused: Sequence[FusedHit],
) -> RetrievalArtifact:
    raw_hits = [
        _artifact_hit(hit, channel="dense") for hit in dense
    ] + [
        _artifact_hit(hit, channel="sparse") for hit in sparse
    ]
    fused_hits = [
        RetrievalHit(
            source_id=hit.source_id,
            parent_id=hit.parent_id,
            score=float(hit.fusion_score),
            raw_text=hit.raw_text,
            metadata={
                **hit.metadata,
                "channel": "fused",
                "child_id": hit.child_id,
                "child_locator": hit.locator,
                "dense_rank": hit.dense_rank,
                "sparse_rank": hit.sparse_rank,
                "dense_score": _decimal_text(hit.dense_score),
                "sparse_score": _decimal_text(hit.sparse_score),
            },
        )
        for hit in fused
    ]
    return RetrievalArtifact(
        retriever="hybrid",
        query=query,
        index_version=index_version,
        hits=tuple((*raw_hits, *fused_hits)),
    )


def _merge_route(
    candidates: dict[str, _Candidate],
    *,
    hits: Sequence[SearchHit],
    route: str,
    weight: Decimal,
    limit: int,
) -> None:
    if weight == 0:
        return
    seen: set[str] = set()
    rank = 0
    for hit in hits:
        if hit.child_id in seen:
            continue
        seen.add(hit.child_id)
        rank += 1
        if rank > limit:
            break
        item = candidates.setdefault(hit.child_id, _Candidate(hit=hit))
        existing = item.hit
        if (
            existing.parent_id != hit.parent_id
            or existing.source_id != hit.source_id
        ):
            raise HybridIntegrityError(
                f"conflicting identity for child: {hit.child_id}"
            )
        if route == "dense":
            item.dense_rank = rank
            item.dense_score = hit.score
        else:
            item.sparse_rank = rank
            item.sparse_score = hit.score
        item.fusion_score += weight / Decimal(rank)


def _artifact_hit(hit: SearchHit, *, channel: str) -> RetrievalHit:
    return RetrievalHit(
        source_id=hit.source_id,
        parent_id=hit.parent_id,
        score=float(hit.score),
        raw_text=hit.raw_text,
        metadata={
            **hit.metadata,
            "channel": channel,
            "child_id": hit.child_id,
            "child_locator": hit.locator,
        },
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
