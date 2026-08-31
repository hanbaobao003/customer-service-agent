"""Hybrid retrieval and small-to-big expansion."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

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
