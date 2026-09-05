from decimal import Decimal

import pytest

from customer_service_agent.mvp.services import (
    MvpRetrievalService,
    MvpSqlGenerator,
    build_mvp_memory_tools,
    build_mvp_order_tools,
)
from customer_service_agent.retrieval.hybrid import ParentDocument, SearchHit
from customer_service_agent.retrieval.graph import GraphQueryResult
from customer_service_agent.retrieval.raptor import RaptorHit, RaptorNode


class HybridIndex:
    async def search_dense(self, query: str, *, limit: int):
        assert query == "耳机保修多久"
        assert limit == 20
        return [
            SearchHit(
                child_id="warranty-child",
                parent_id="warranty-parent",
                source_id="faq-warranty",
                score=Decimal("0.9"),
                raw_text="保修 12 个月",
                locator="child-1",
                metadata={"data_version": "wang_agent_mvp_faq_v1"},
            )
        ]

    async def search_sparse(self, query: str, *, limit: int):
        assert query == "耳机保修多久"
        assert limit == 20
        return [
            SearchHit(
                child_id="warranty-child",
                parent_id="warranty-parent",
                source_id="faq-warranty",
                score=Decimal("0.8"),
                raw_text="保修 12 个月",
                locator="child-1",
                metadata={"data_version": "wang_agent_mvp_faq_v1"},
            )
        ]

    def get(self, parent_id: str):
        assert parent_id == "warranty-parent"
        return ParentDocument(
            parent_id=parent_id,
            source_id="faq-warranty",
            title="耳机保修说明",
            text="云端降噪耳机自签收日起提供 12 个月有限保修。",
            locator="warranty",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_faq_returns_parent_evidence_and_citation() -> None:
    service = MvpRetrievalService(
        hybrid_index=HybridIndex(),
        faq_index_version="wang_agent_mvp_faq_v1",
    )

    result = await service.search_product_faq("耳机保修多久")

    assert result.answerable is True
    assert result.artifact.retriever == "hybrid"
    assert result.evidence[0].text == "云端降噪耳机自签收日起提供 12 个月有限保修。"
    assert result.citations[0].id == result.evidence[0].citation_id


class RaptorStore:
    def __init__(self) -> None:
        leaf = RaptorNode.leaf(
            document_id="return-policy",
            text="已付款且未发货的订单可以申请取消。",
            source_id="policy-return",
            locator="section-cancel",
            data_version="wang_agent_mvp_raptor_v1",
        )
        root = RaptorNode.summary(
            document_id=leaf.document_id,
            level=1,
            text="取消订单规则摘要。",
            child_ids=(leaf.node_id,),
            source_ids=leaf.source_ids,
            data_version=leaf.data_version,
        )
        self.nodes = {leaf.node_id: leaf, root.node_id: root}
        self.root_id = root.node_id

    async def search_roots(self, query: str, *, limit: int):
        assert query == "已付款订单可以取消吗"
        assert limit == 3
        return [RaptorHit(node_id=self.root_id, score=0.9)]

    def get(self, node_id: str):
        return self.nodes.get(node_id)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raptor_returns_leaf_evidence_with_policy_citation() -> None:
    service = MvpRetrievalService(
        hybrid_index=HybridIndex(),
        faq_index_version="wang_agent_mvp_faq_v1",
        raptor_store=RaptorStore(),
        raptor_index_version="wang_agent_mvp_raptor_v1",
    )

    result = await service.search_policy_raptor("已付款订单可以取消吗")

    assert result.answerable is True
    assert result.artifact.retriever == "raptor"
    assert result.evidence[0].text == "已付款且未发货的订单可以申请取消。"
    assert result.citations[0].id == "policy-return#section-cancel"


class GraphRegistry:
    def execute(self, template_id: str, parameters):
        assert template_id == "product_context"
        assert parameters == {"product_id": "MVP-PRODUCT-1001"}
        return GraphQueryResult(
            template_id=template_id,
            data_version="wang_agent_mvp_v1",
            records=(
                {
                    "entity_id": "MVP-PRODUCT-1001",
                    "relationship": "MADE_BY",
                    "related": {"name": "云澜"},
                },
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_returns_fixed_product_context_evidence() -> None:
    service = MvpRetrievalService(
        hybrid_index=HybridIndex(),
        faq_index_version="wang_agent_mvp_faq_v1",
        graph_registry=GraphRegistry(),
        graph_version="wang_agent_mvp_v1",
    )

    result = await service.search_commerce_graph("耳机是什么品牌")

    assert result.answerable is True
    assert result.artifact.retriever == "graph"
    assert result.evidence[0].text == "MVP-PRODUCT-1001 与 云澜 的关系：MADE_BY。"
    assert result.citations[0].id == "mvp-graph#MVP-PRODUCT-1001"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_web_search_is_explicitly_unavailable_without_a_client() -> None:
    service = MvpRetrievalService(
        hybrid_index=HybridIndex(),
        faq_index_version="wang_agent_mvp_faq_v1",
    )

    result = await service.web_search("近期物流公告")

    assert result.answerable is False
    assert result.artifact.retriever == "web"
    assert result.notice == "外部时效信息当前不可用。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sql_generator_maps_order_count_to_a_fixed_read_query() -> None:
    proposal = await MvpSqlGenerator().generate("我的订单有多少个？")

    assert proposal.sql == "SELECT COUNT(order_id) AS order_count FROM customer_orders"
    assert proposal.referenced_relations == ("customer_orders",)


@pytest.mark.unit
def test_order_tool_bundle_exposes_one_read_and_four_preview_tools() -> None:
    settings = type("Settings", (), {"postgres_dsn": "postgresql://demo"})()

    bundle = build_mvp_order_tools(settings)

    assert {tool.name for tool in bundle.tools} == {
        "get_order",
        "create_order",
        "update_order_contact",
        "cancel_order",
        "request_return",
    }


@pytest.mark.unit
def test_memory_tool_bundle_uses_isolated_pgvector_configuration(tmp_path) -> None:
    captured: list[dict[str, object]] = []
    settings = type("Settings", (), {"mem0_dsn": "postgresql://memory"})()

    bundle = build_mvp_memory_tools(
        settings,
        history_db_path=tmp_path / "history.db",
        memory_factory=lambda config: captured.append(config) or object(),
    )

    assert {tool.name for tool in bundle.tools} == {
        "remember_preference",
        "list_memories",
        "forget_memory",
    }
    assert captured[0]["vector_store"]["config"]["dbname"] == "wang_agent_mvp_mem0"
