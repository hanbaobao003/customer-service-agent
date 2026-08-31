from decimal import Decimal

import pytest

from customer_service_agent.retrieval.hybrid import (
    FusionConfig,
    HybridConfigError,
    HybridIntegrityError,
    ParentDocument,
    SearchHit,
    build_hybrid_artifact,
    expand_parents,
    fuse_hits,
)


def hit(
    child_id: str,
    *,
    parent_id: str,
    score: str,
    source_id: str = "faq-1",
) -> SearchHit:
    return SearchHit(
        child_id=child_id,
        parent_id=parent_id,
        source_id=source_id,
        score=Decimal(score),
        raw_text=f"child text {child_id}",
        locator=f"section-{child_id}",
        metadata={"domain": "product_faq"},
    )


class ParentStore:
    def __init__(self, documents: dict[str, ParentDocument]) -> None:
        self.documents = documents

    def get(self, parent_id: str) -> ParentDocument | None:
        return self.documents.get(parent_id)


@pytest.mark.unit
def test_weighted_reciprocal_rank_fusion_is_reproducible() -> None:
    dense = [
        hit("c-a", parent_id="p-a", score="0.91"),
        hit("c-b", parent_id="p-b", score="0.82"),
    ]
    sparse = [
        hit("c-b", parent_id="p-b", score="18.0"),
        hit("c-a", parent_id="p-a", score="12.0"),
        hit("c-c", parent_id="p-c", score="9.0"),
    ]
    config = FusionConfig(
        method="weighted_reciprocal_rank",
        dense_weight=Decimal("2"),
        sparse_weight=Decimal("1"),
        candidate_k=3,
        final_k=3,
    )

    first = fuse_hits(dense=dense, sparse=sparse, config=config)
    second = fuse_hits(dense=dense, sparse=sparse, config=config)

    assert first == second
    assert [item.child_id for item in first] == ["c-a", "c-b", "c-c"]
    assert first[0].fusion_score == Decimal("2.5")
    assert first[0].dense_score == Decimal("0.91")
    assert first[0].sparse_score == Decimal("12.0")
    assert first[2].dense_score is None


@pytest.mark.unit
def test_fusion_uses_explicit_candidate_and_final_limits() -> None:
    dense = [
        hit("c-a", parent_id="p-a", score="0.9"),
        hit("c-b", parent_id="p-b", score="0.8"),
    ]
    sparse = [hit("c-c", parent_id="p-c", score="10")]
    config = FusionConfig(
        method="weighted_reciprocal_rank",
        dense_weight=Decimal("1"),
        sparse_weight=Decimal("1"),
        candidate_k=1,
        final_k=1,
    )

    result = fuse_hits(dense=dense, sparse=sparse, config=config)

    assert len(result) == 1
    assert result[0].child_id == "c-a"


@pytest.mark.unit
@pytest.mark.parametrize(
    "changes",
    [
        {"dense_weight": Decimal("-1")},
        {"dense_weight": Decimal("0"), "sparse_weight": Decimal("0")},
        {"candidate_k": 0},
        {"final_k": 0},
        {"candidate_k": 1, "final_k": 2},
    ],
)
def test_invalid_fusion_config_is_rejected(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "method": "weighted_reciprocal_rank",
        "dense_weight": Decimal("1"),
        "sparse_weight": Decimal("1"),
        "candidate_k": 5,
        "final_k": 3,
    }
    values.update(changes)

    with pytest.raises(HybridConfigError):
        FusionConfig(**values)


@pytest.mark.unit
def test_unapproved_fusion_method_is_rejected() -> None:
    with pytest.raises(HybridConfigError, match="method"):
        FusionConfig(
            method="hidden_default",
            dense_weight=Decimal("1"),
            sparse_weight=Decimal("1"),
            candidate_k=5,
            final_k=3,
        )


@pytest.mark.unit
def test_same_child_with_conflicting_parent_is_rejected() -> None:
    with pytest.raises(HybridIntegrityError, match="c-a"):
        fuse_hits(
            dense=[hit("c-a", parent_id="p-a", score="0.9")],
            sparse=[hit("c-a", parent_id="p-other", score="10")],
            config=FusionConfig(
                method="weighted_reciprocal_rank",
                dense_weight=Decimal("1"),
                sparse_weight=Decimal("1"),
                candidate_k=5,
                final_k=3,
            ),
        )


@pytest.mark.unit
def test_small_to_big_returns_parent_and_preserves_best_child_locator() -> None:
    fused = fuse_hits(
        dense=[
            hit("c-2", parent_id="p-1", score="0.9"),
            hit("c-3", parent_id="p-1", score="0.8"),
        ],
        sparse=[],
        config=FusionConfig(
            method="weighted_reciprocal_rank",
            dense_weight=Decimal("1"),
            sparse_weight=Decimal("1"),
            candidate_k=5,
            final_k=5,
        ),
    )
    parents = ParentStore({
        "p-1": ParentDocument(
            parent_id="p-1",
            source_id="faq-1",
            title="完整商品说明",
            text="这是完整商品说明，而不是 child 文本。",
            locator="faq-section-1",
        )
    })

    result = expand_parents(fused, parents)

    assert len(result) == 1
    assert result[0].text == "这是完整商品说明，而不是 child 文本。"
    assert result[0].locator.child_id == "c-2"
    assert result[0].locator.child_locator == "section-c-2"
    assert result[0].locator.parent_id == "p-1"
    assert result[0].locator.parent_locator == "faq-section-1"


@pytest.mark.unit
def test_missing_parent_is_not_silently_dropped() -> None:
    fused = fuse_hits(
        dense=[hit("c-a", parent_id="missing", score="0.9")],
        sparse=[],
        config=FusionConfig(
            method="weighted_reciprocal_rank",
            dense_weight=Decimal("1"),
            sparse_weight=Decimal("1"),
            candidate_k=5,
            final_k=3,
        ),
    )

    with pytest.raises(HybridIntegrityError, match="missing"):
        expand_parents(fused, ParentStore({}))


@pytest.mark.unit
def test_hybrid_artifact_preserves_raw_route_hits_and_fusion_scores() -> None:
    dense = [hit("c-a", parent_id="p-a", score="0.9")]
    sparse = [hit("c-a", parent_id="p-a", score="12")]
    fused = fuse_hits(
        dense=dense,
        sparse=sparse,
        config=FusionConfig(
            method="weighted_reciprocal_rank",
            dense_weight=Decimal("1"),
            sparse_weight=Decimal("1"),
            candidate_k=5,
            final_k=3,
        ),
    )

    artifact = build_hybrid_artifact(
        query="商品说明",
        index_version="faq-v1",
        dense=dense,
        sparse=sparse,
        fused=fused,
    )

    assert [item.metadata["channel"] for item in artifact.hits] == [
        "dense",
        "sparse",
        "fused",
    ]
    assert artifact.hits[0].score == 0.9
    assert artifact.hits[1].score == 12.0
    assert artifact.hits[2].metadata["dense_score"] == "0.9"
    assert artifact.hits[2].metadata["sparse_score"] == "12"
