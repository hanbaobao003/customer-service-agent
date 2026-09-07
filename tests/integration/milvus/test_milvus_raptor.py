import asyncio

import pytest

from customer_service_agent.retrieval import raptor


def vector(first: float, second: float) -> tuple[float, ...]:
    return (first, second) + (0.0,) * 1022


@pytest.mark.integration_milvus
def test_raptor_store_returns_only_current_version_roots_and_descends_to_leaf(
    milvus_database_client,
    isolated_milvus_database,
) -> None:
    raptor.create_raptor_collection(
        milvus_database_client,
        isolated_milvus_database.raptor_collection,
    )
    first_leaf = raptor.RaptorNode.leaf(
        document_id="return-policy",
        text="未发货订单可以取消。",
        source_id="policy#cancel",
        locator="cancel",
        data_version="policy-v1",
    )
    first_root = raptor.RaptorNode.summary(
        document_id="return-policy",
        level=1,
        text="取消政策摘要",
        child_ids=[first_leaf.node_id],
        source_ids=list(first_leaf.source_ids),
        data_version="policy-v1",
    )
    second_leaf = raptor.RaptorNode.leaf(
        document_id="return-policy",
        text="退款将原路返回。",
        source_id="policy#refund",
        locator="refund",
        data_version="policy-v2",
    )
    second_root = raptor.RaptorNode.summary(
        document_id="return-policy",
        level=1,
        text="退款政策摘要",
        child_ids=[second_leaf.node_id],
        source_ids=list(second_leaf.source_ids),
        data_version="policy-v2",
    )
    store = raptor.MilvusRaptorStore(
        client=milvus_database_client,
        collection_name=isolated_milvus_database.raptor_collection,
        data_version="policy-v1",
        embed_query=lambda _query: vector(1.0, 0.0),
    )
    store.insert(
        nodes=[first_leaf, first_root, second_leaf, second_root],
        dense_vectors={
            first_leaf.node_id: vector(1.0, 0.0),
            first_root.node_id: vector(1.0, 0.0),
            second_leaf.node_id: vector(0.0, 1.0),
            second_root.node_id: vector(0.0, 1.0),
        },
    )

    root_hits = asyncio.run(store.search_roots("可以取消吗", limit=5))
    paths = raptor.descend_hits(root_hits, store)

    assert [item.node_id for item in root_hits] == [first_root.node_id]
    assert [item.source_id for item in paths] == ["policy#cancel"]
    assert paths[0].leaf_locator == "cancel"
