import asyncio

import pytest

from customer_service_agent.retrieval import hybrid


def vector(first: float, second: float) -> tuple[float, ...]:
    return (first, second) + (0.0,) * 1022


@pytest.mark.integration_milvus
def test_hybrid_adapter_filters_version_and_expands_parent(
    milvus_database_client,
    isolated_milvus_database,
) -> None:
    hybrid.create_hybrid_collection(
        milvus_database_client,
        isolated_milvus_database.hybrid_collection,
    )
    adapter = hybrid.MilvusHybridIndex(
        client=milvus_database_client,
        collection_name=isolated_milvus_database.hybrid_collection,
        data_version="faq-v1",
        embed_query=lambda _query: vector(1.0, 0.0),
    )
    adapter.insert(
        [
            hybrid.HybridIndexDocument(
                child_id="child-1",
                parent_id="parent-1",
                source_id="faq-1",
                title="配送",
                child_text="订单满 99 元包邮",
                parent_text="配送政策：订单满 99 元包邮。",
                child_locator="shipping-1",
                parent_locator="shipping",
                data_version="faq-v1",
                dense_vector=vector(1.0, 0.0),
            ),
            hybrid.HybridIndexDocument(
                child_id="child-2",
                parent_id="parent-2",
                source_id="faq-2",
                title="退款",
                child_text="退款原路返回",
                parent_text="退款政策：退款原路返回。",
                child_locator="refund-1",
                parent_locator="refund",
                data_version="faq-v2",
                dense_vector=vector(0.0, 1.0),
            ),
        ]
    )

    dense = asyncio.run(adapter.search_dense("包邮", limit=20))
    sparse = asyncio.run(adapter.search_sparse("包邮", limit=20))
    parent = adapter.get("parent-1")

    assert [hit.child_id for hit in dense] == ["child-1"]
    assert [hit.child_id for hit in sparse] == ["child-1"]
    assert parent is not None
    assert parent.text == "配送政策：订单满 99 元包邮。"
    assert parent.locator == "shipping"

