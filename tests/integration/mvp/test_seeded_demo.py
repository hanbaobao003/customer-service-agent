import os

import pytest

from neo4j import GraphDatabase
from pymilvus import MilvusClient

from customer_service_agent.mvp.seed import (
    seed_mvp,
    seed_mem0,
    seed_milvus,
    seed_neo4j,
    seed_postgres,
)
from customer_service_agent.mvp.services import MvpProductCatalog, build_mvp_retrieval_service
from customer_service_agent.mvp.settings import MvpSettings


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_postgres_seed_is_idempotent() -> None:
    settings = MvpSettings.from_environment(
        {
            **os.environ,
            "MILVUS_URI": "http://127.0.0.1:19530",
            "NEO4J_URI": "neo4j://127.0.0.1:7687",
        }
    )

    first = await seed_postgres(settings)
    second = await seed_postgres(settings)

    assert first.orders == ("MVP-ORDER-1001", "MVP-ORDER-2001")
    assert second == first


@pytest.mark.integration_milvus
@pytest.mark.asyncio
async def test_milvus_seed_creates_owned_collections() -> None:
    settings = MvpSettings.from_environment(
        {
            **os.environ,
            "MILVUS_URI": "http://127.0.0.1:19530",
            "NEO4J_URI": "neo4j://127.0.0.1:7687",
        }
    )

    report = await seed_milvus(settings)
    client = MilvusClient(uri=settings.milvus_uri)

    assert report.faq_collection == "wang_agent_mvp_faq_v1"
    assert report.raptor_collection == "wang_agent_mvp_raptor_v1"
    assert client.has_collection(report.faq_collection)
    assert client.has_collection(report.raptor_collection)


@pytest.mark.integration_neo4j
def test_neo4j_seed_creates_versioned_product_graph() -> None:
    settings = MvpSettings.from_environment(
        {
            **os.environ,
            "MILVUS_URI": "http://127.0.0.1:19530",
            "NEO4J_URI": "neo4j://127.0.0.1:7687",
        }
    )

    report = seed_neo4j(settings)
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        records, _, _ = driver.execute_query(
            """
            MATCH (product:Product {
                product_id: 'MVP-PRODUCT-1001', data_version: $data_version
            })-[:MADE_BY]->(brand:Brand {data_version: $data_version})
            RETURN product.name AS product_name, brand.name AS brand_name
            """,
            parameters_={"data_version": report.graph_version},
            database_="neo4j",
        )
    finally:
        driver.close()

    assert report.graph_version == "wang_agent_mvp_v1"
    assert [record.data() for record in records] == [
        {"product_name": "云端降噪耳机", "brand_name": "云澜"}
    ]


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_mem0_seed_keeps_one_customer_scoped_preference(tmp_path) -> None:
    settings = MvpSettings.from_environment(
        {
            **os.environ,
            "MILVUS_URI": "http://127.0.0.1:19530",
            "NEO4J_URI": "neo4j://127.0.0.1:7687",
        }
    )

    first = await seed_mem0(
        settings,
        history_db_path=str(tmp_path / "mem0-history.db"),
    )
    second = await seed_mem0(
        settings,
        history_db_path=str(tmp_path / "mem0-history.db"),
    )

    assert first.memory_customer_id == "demo-customer-a"
    assert first.memory_count == second.memory_count == 1


@pytest.mark.integration_postgres
@pytest.mark.integration_milvus
@pytest.mark.integration_neo4j
@pytest.mark.asyncio
async def test_seed_mvp_populates_all_demo_resources(tmp_path) -> None:
    settings = MvpSettings.from_environment(
        {
            **os.environ,
            "MILVUS_URI": "http://127.0.0.1:19530",
            "NEO4J_URI": "neo4j://127.0.0.1:7687",
        }
    )

    report = await seed_mvp(
        settings,
        history_db_path=str(tmp_path / "mem0-history.db"),
    )

    assert report.orders == ("MVP-ORDER-1001", "MVP-ORDER-2001")
    assert report.faq_collection == "wang_agent_mvp_faq_v1"
    assert report.raptor_collection == "wang_agent_mvp_raptor_v1"
    assert report.graph_version == "wang_agent_mvp_v1"
    assert report.memory_count == 1


@pytest.mark.integration_milvus
@pytest.mark.integration_neo4j
@pytest.mark.asyncio
async def test_seeded_retrieval_services_return_cited_evidence() -> None:
    settings = MvpSettings.from_environment({**os.environ})
    service = build_mvp_retrieval_service(settings)

    faq = await service.search_product_faq("耳机保修多久")
    policy = await service.search_policy_raptor("已付款订单可以取消吗")
    graph = await service.search_commerce_graph("耳机是什么品牌")

    assert faq.answerable is True
    assert faq.citations[0].id == "mvp-faq-warranty#warranty"
    assert policy.answerable is True
    assert policy.citations[0].id == "mvp-policy-return#section-cancel"
    assert graph.answerable is True
    assert graph.citations[0].id == "mvp-graph#MVP-PRODUCT-1001"


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_seeded_catalog_reads_active_product_snapshot() -> None:
    settings = MvpSettings.from_environment({**os.environ})

    product = await MvpProductCatalog(settings.postgres_dsn).get("MVP-PRODUCT-1001")

    assert product is not None
    assert product.product_name == "云端降噪耳机"
    assert str(product.unit_price) == "399.00"
