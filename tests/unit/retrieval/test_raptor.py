import pytest

from customer_service_agent.retrieval.raptor import (
    RaptorHit,
    RaptorIntegrityError,
    RaptorNode,
    build_raptor_artifact,
    descend_hits,
    validate_tree,
)


def leaf(
    source_id: str,
    *,
    text: str,
    locator: str,
    data_version: str = "policy-v1",
) -> RaptorNode:
    return RaptorNode.leaf(
        document_id="return-policy",
        text=text,
        source_id=source_id,
        locator=locator,
        data_version=data_version,
    )


def summary(
    children: list[RaptorNode],
    *,
    text: str = "退货政策摘要",
    level: int = 1,
) -> RaptorNode:
    return RaptorNode.summary(
        document_id="return-policy",
        level=level,
        text=text,
        child_ids=[item.node_id for item in children],
        source_ids=[source for item in children for source in item.source_ids],
        data_version="policy-v1",
    )


class NodeStore:
    def __init__(self, nodes: list[RaptorNode]) -> None:
        self.nodes = {node.node_id: node for node in nodes}

    def get(self, node_id: str) -> RaptorNode | None:
        return self.nodes.get(node_id)


@pytest.mark.unit
def test_summary_node_without_children_is_rejected() -> None:
    node = RaptorNode.summary(
        document_id="return-policy",
        level=1,
        text="无来源摘要",
        child_ids=[],
        source_ids=[],
        data_version="policy-v1",
    )

    with pytest.raises(RaptorIntegrityError, match="children"):
        validate_tree([node])


@pytest.mark.unit
def test_node_id_is_order_independent_but_content_and_version_sensitive() -> None:
    first = RaptorNode.summary(
        document_id="return-policy",
        level=2,
        text="跨章节摘要",
        child_ids=["child-b", "child-a"],
        source_ids=["source-b", "source-a"],
        data_version="policy-v1",
    )
    reordered = RaptorNode.summary(
        document_id="return-policy",
        level=2,
        text="跨章节摘要",
        child_ids=["child-a", "child-b"],
        source_ids=["source-a", "source-b"],
        data_version="policy-v1",
    )
    changed_text = RaptorNode.summary(
        document_id="return-policy",
        level=2,
        text="不同摘要",
        child_ids=["child-a", "child-b"],
        source_ids=["source-a", "source-b"],
        data_version="policy-v1",
    )
    changed_version = RaptorNode.summary(
        document_id="return-policy",
        level=2,
        text="跨章节摘要",
        child_ids=["child-a", "child-b"],
        source_ids=["source-a", "source-b"],
        data_version="policy-v2",
    )

    assert first.node_id == reordered.node_id
    assert first.child_ids == ("child-a", "child-b")
    assert first.source_ids == ("source-a", "source-b")
    assert len({first.node_id, changed_text.node_id, changed_version.node_id}) == 3


@pytest.mark.unit
def test_tree_rejects_missing_child_and_wrong_child_level() -> None:
    first_leaf = leaf("policy#section-1", text="第一节", locator="section-1")
    missing_child = RaptorNode.summary(
        document_id="return-policy",
        level=1,
        text="摘要",
        child_ids=["not-present"],
        source_ids=["policy#section-1"],
        data_version="policy-v1",
    )
    level_two = RaptorNode.summary(
        document_id="return-policy",
        level=2,
        text="错误层级摘要",
        child_ids=[first_leaf.node_id],
        source_ids=list(first_leaf.source_ids),
        data_version="policy-v1",
    )

    with pytest.raises(RaptorIntegrityError, match="missing child"):
        validate_tree([missing_child])
    with pytest.raises(RaptorIntegrityError, match="level"):
        validate_tree([first_leaf, level_two])


@pytest.mark.unit
def test_summary_sources_must_equal_descendant_sources() -> None:
    first_leaf = leaf("policy#section-1", text="第一节", locator="section-1")
    wrong_sources = RaptorNode.summary(
        document_id="return-policy",
        level=1,
        text="摘要",
        child_ids=[first_leaf.node_id],
        source_ids=["policy#invented"],
        data_version="policy-v1",
    )

    with pytest.raises(RaptorIntegrityError, match="source"):
        validate_tree([first_leaf, wrong_sources])


@pytest.mark.unit
def test_descend_hits_preserves_root_level_path_and_leaf_source() -> None:
    first_leaf = leaf(
        "policy#section-1",
        text="未发货订单可以取消。",
        locator="section-1",
    )
    second_leaf = leaf(
        "policy#section-4",
        text="退款原路返回。",
        locator="section-4",
    )
    root = summary([first_leaf, second_leaf], text="取消与退款摘要")
    nodes = [first_leaf, second_leaf, root]
    validate_tree(nodes)

    paths = descend_hits(
        [RaptorHit(node_id=root.node_id, score=0.91)],
        NodeStore(nodes),
    )

    assert [item.root_level for item in paths] == [1, 1]
    assert [item.node_ids for item in paths] == [
        (root.node_id, first_leaf.node_id),
        (root.node_id, second_leaf.node_id),
    ]
    assert [item.source_id for item in paths] == [
        "policy#section-1",
        "policy#section-4",
    ]
    assert [item.leaf_locator for item in paths] == ["section-1", "section-4"]


@pytest.mark.unit
def test_overlapping_root_hits_do_not_duplicate_leaf_evidence() -> None:
    first_leaf = leaf("policy#section-1", text="第一节", locator="section-1")
    root = summary([first_leaf])
    nodes = [first_leaf, root]

    paths = descend_hits(
        [
            RaptorHit(node_id=root.node_id, score=0.9),
            RaptorHit(node_id=first_leaf.node_id, score=0.8),
        ],
        NodeStore(nodes),
    )

    assert len(paths) == 1
    assert paths[0].node_ids == (root.node_id, first_leaf.node_id)


@pytest.mark.unit
def test_missing_node_during_descent_is_not_silently_skipped() -> None:
    with pytest.raises(RaptorIntegrityError, match="missing node"):
        descend_hits(
            [RaptorHit(node_id="missing", score=0.9)],
            NodeStore([]),
        )


@pytest.mark.unit
def test_descent_rechecks_summary_source_integrity() -> None:
    first_leaf = leaf("policy#section-1", text="第一节", locator="section-1")
    corrupt_root = RaptorNode.summary(
        document_id="return-policy",
        level=1,
        text="摘要",
        child_ids=[first_leaf.node_id],
        source_ids=["policy#invented"],
        data_version="policy-v1",
    )

    with pytest.raises(RaptorIntegrityError, match="source"):
        descend_hits(
            [RaptorHit(node_id=corrupt_root.node_id, score=0.9)],
            NodeStore([first_leaf, corrupt_root]),
        )


@pytest.mark.unit
def test_raptor_artifact_contains_hit_level_path_and_final_source() -> None:
    first_leaf = leaf("policy#section-1", text="第一节", locator="section-1")
    root = summary([first_leaf])
    root_hits = [RaptorHit(node_id=root.node_id, score=0.9)]
    paths = descend_hits(root_hits, NodeStore([first_leaf, root]))

    artifact = build_raptor_artifact(
        query="如何取消并退款",
        index_version="policy-v1",
        root_hits=root_hits,
        paths=paths,
    )

    assert [item.metadata["channel"] for item in artifact.hits] == [
        "raptor_root",
        "raptor_leaf",
    ]
    assert artifact.hits[0].metadata["hit_level"] == 1
    assert artifact.hits[1].source_id == "policy#section-1"
    assert artifact.hits[1].metadata["path"] == [root.node_id, first_leaf.node_id]
