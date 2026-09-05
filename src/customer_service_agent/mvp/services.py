"""Concrete, minimal services used by the local portfolio MVP."""

from decimal import Decimal
import json

from customer_service_agent.commerce.postgres import PostgresSqlReader
from customer_service_agent.commerce.sql import (
    ProposedSql,
    SqlPolicyRejected,
    SqlQueryService,
    create_business_sql_guard,
)
from customer_service_agent.config import SQL_STATEMENT_TIMEOUT_MS
from customer_service_agent.mvp.seed import MvpResources, _demo_vector
from customer_service_agent.retrieval.graph import (
    GraphQueryRegistry,
    Neo4jDriverAdapter,
    build_graph_templates,
)
from customer_service_agent.retrieval.hybrid import (
    FusionConfig,
    MilvusHybridIndex,
    build_hybrid_artifact,
    expand_parents,
    fuse_hits,
)
from customer_service_agent.retrieval.models import (
    Citation,
    Evidence,
    RetrievalArtifact,
    RetrievalHit,
    RetrievalResult,
)
from customer_service_agent.retrieval.raptor import build_raptor_artifact, descend_hits
from customer_service_agent.retrieval.raptor import MilvusRaptorStore
from neo4j import GraphDatabase
from pymilvus import MilvusClient


class MvpRetrievalService:
    """Expose seeded internal knowledge through the fixed retrieval tool port."""

    def __init__(
        self,
        *,
        hybrid_index: object,
        faq_index_version: str,
        raptor_store: object | None = None,
        raptor_index_version: str | None = None,
        graph_registry: object | None = None,
        graph_version: str | None = None,
    ) -> None:
        self._hybrid_index = hybrid_index
        self._faq_index_version = faq_index_version
        self._raptor_store = raptor_store
        self._raptor_index_version = raptor_index_version
        self._graph_registry = graph_registry
        self._graph_version = graph_version

    async def search_product_faq(self, query: str) -> RetrievalResult:
        dense = await self._hybrid_index.search_dense(query, limit=20)
        sparse = await self._hybrid_index.search_sparse(query, limit=20)
        fused = fuse_hits(
            dense=dense,
            sparse=sparse,
            config=FusionConfig(
                method="weighted_reciprocal_rank",
                dense_weight=Decimal("0.5"),
                sparse_weight=Decimal("0.5"),
                candidate_k=20,
                final_k=3,
            ),
        )
        expanded = expand_parents(fused, self._hybrid_index)
        artifact = build_hybrid_artifact(
            query=query,
            index_version=self._faq_index_version,
            dense=dense,
            sparse=sparse,
            fused=fused,
        )
        return RetrievalResult.build(
            answerable=bool(expanded),
            evidence=[
                Evidence(
                    citation_id=item.citation_id,
                    text=item.text,
                    source_label=item.source_label,
                )
                for item in expanded
            ],
            citations=[
                Citation(
                    id=item.citation_id,
                    source_id=item.citation_id.split("#", 1)[0],
                    locator=item.locator.parent_locator,
                    title=item.source_label,
                )
                for item in expanded
            ],
            artifact=artifact,
            max_evidence_chars=500,
        )

    async def search_policy_raptor(self, query: str) -> RetrievalResult:
        if self._raptor_store is None or self._raptor_index_version is None:
            raise RuntimeError("RAPTOR store is not configured")
        roots = await self._raptor_store.search_roots(query, limit=3)
        paths = descend_hits(roots, self._raptor_store)
        artifact = build_raptor_artifact(
            query=query,
            index_version=self._raptor_index_version,
            root_hits=roots,
            paths=paths,
        )
        return RetrievalResult.build(
            answerable=bool(paths),
            evidence=[
                Evidence(
                    citation_id=f"{path.source_id}#{path.leaf_locator}",
                    text=path.leaf_text,
                    source_label="政策证据",
                )
                for path in paths
            ],
            citations=[
                Citation(
                    id=f"{path.source_id}#{path.leaf_locator}",
                    source_id=path.source_id,
                    locator=path.leaf_locator,
                    title="政策证据",
                )
                for path in paths
            ],
            artifact=artifact,
            max_evidence_chars=500,
        )

    async def search_commerce_graph(self, query: str) -> RetrievalResult:
        if self._graph_registry is None or self._graph_version is None:
            raise RuntimeError("commerce graph is not configured")
        if "耳机" not in query:
            return RetrievalResult.build(
                answerable=False,
                evidence=[],
                citations=[],
                artifact=RetrievalArtifact(
                    retriever="graph",
                    query=query,
                    index_version=self._graph_version,
                    hits=(),
                ),
                max_evidence_chars=500,
                notice="演示图谱仅包含耳机相关商品关系。",
            )
        result = self._graph_registry.execute(
            "product_context",
            {"product_id": "MVP-PRODUCT-1001"},
        )
        evidence: list[Evidence] = []
        hits: list[RetrievalHit] = []
        citation_id = "mvp-graph#MVP-PRODUCT-1001"
        for record in result.records:
            related = record.get("related")
            related_name = related.get("name") if isinstance(related, dict) else None
            relationship = record.get("relationship")
            entity_id = record.get("entity_id")
            if not all(isinstance(value, str) and value for value in (
                entity_id,
                related_name,
                relationship,
            )):
                continue
            evidence.append(
                Evidence(
                    citation_id=citation_id,
                    text=f"{entity_id} 与 {related_name} 的关系：{relationship}。",
                    source_label="商品图谱",
                )
            )
            hits.append(
                RetrievalHit(
                    source_id="mvp-graph",
                    parent_id=entity_id,
                    score=1.0,
                    raw_text=json.dumps(record, ensure_ascii=False),
                    metadata={"template_id": result.template_id},
                )
            )
        return RetrievalResult.build(
            answerable=bool(evidence),
            evidence=evidence,
            citations=[
                Citation(
                    id=citation_id,
                    source_id="mvp-graph",
                    locator="MVP-PRODUCT-1001",
                    title="商品图谱",
                )
            ] if evidence else [],
            artifact=RetrievalArtifact(
                retriever="graph",
                query=query,
                index_version=self._graph_version,
                hits=tuple(hits),
            ),
            max_evidence_chars=500,
        )

    async def web_search(self, query: str) -> RetrievalResult:
        return RetrievalResult.build(
            answerable=False,
            evidence=[],
            citations=[],
            artifact=RetrievalArtifact(
                retriever="web",
                query=query,
                index_version="external-current-events",
                hits=(),
            ),
            max_evidence_chars=500,
            notice="外部时效信息当前不可用。",
        )


class MvpSqlGenerator:
    """Map a few MVP demo questions to audited, fixed read-only SQL."""

    async def generate(self, question: str) -> ProposedSql:
        if "订单" in question and any(marker in question for marker in ("多少", "几个", "几笔")):
            return ProposedSql(
                sql="SELECT COUNT(order_id) AS order_count FROM customer_orders",
                explanation="统计当前客户的订单数量。",
                referenced_relations=("customer_orders",),
            )
        if "订单" in question:
            return ProposedSql(
                sql=(
                    "SELECT order_id, status, total_amount, currency "
                    "FROM customer_orders"
                ),
                explanation="查看当前客户的订单摘要。",
                referenced_relations=("customer_orders",),
            )
        if any(marker in question for marker in ("商品", "耳机", "收纳包", "快充线")):
            return ProposedSql(
                sql=(
                    "SELECT product_id, product_name, unit_price, currency "
                    "FROM catalog_products"
                ),
                explanation="查看演示商品目录。",
                referenced_relations=("catalog_products",),
            )
        raise SqlPolicyRejected("MVP SQL tool only supports orders and catalog questions")


def build_mvp_sql_service(postgres_dsn: str) -> SqlQueryService:
    return SqlQueryService(
        guard=create_business_sql_guard(),
        generator=MvpSqlGenerator(),
        reader=PostgresSqlReader(postgres_dsn),
        timeout_ms=SQL_STATEMENT_TIMEOUT_MS,
    )


def build_mvp_retrieval_service(settings: object) -> MvpRetrievalService:
    resources = MvpResources.default()
    client = MilvusClient(uri=getattr(settings, "milvus_uri"))
    driver = GraphDatabase.driver(
        getattr(settings, "neo4j_uri"),
        auth=(
            getattr(settings, "neo4j_username"),
            getattr(settings, "neo4j_password"),
        ),
    )
    return MvpRetrievalService(
        hybrid_index=MilvusHybridIndex(
            client=client,
            collection_name=resources.faq_collection,
            data_version=resources.faq_collection,
            embed_query=_demo_vector,
        ),
        faq_index_version=resources.faq_collection,
        raptor_store=MilvusRaptorStore(
            client=client,
            collection_name=resources.raptor_collection,
            data_version=resources.raptor_collection,
            embed_query=_demo_vector,
        ),
        raptor_index_version=resources.raptor_collection,
        graph_registry=GraphQueryRegistry(
            driver=Neo4jDriverAdapter(driver=driver, database="neo4j"),
            templates=build_graph_templates(max_path_depth=2, result_limit=5),
            data_version=resources.graph_version,
        ),
        graph_version=resources.graph_version,
    )
