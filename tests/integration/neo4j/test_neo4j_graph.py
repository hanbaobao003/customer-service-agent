from collections.abc import Iterator
from dataclasses import dataclass
import os
import uuid

from neo4j import Driver, GraphDatabase
import pytest

from customer_service_agent.retrieval.graph import (
    GraphQueryRegistry,
    Neo4jDriverAdapter,
    build_graph_templates,
)


TEST_VERSION_PREFIX = "wang_agent_graph_test_"


@dataclass(frozen=True)
class IsolatedGraph:
    driver: Driver
    data_version: str
    first_product_id: str
    second_product_id: str
    category_id: str


@pytest.fixture
def isolated_graph() -> Iterator[IsolatedGraph]:
    auth_value = os.getenv("NEO4J_AUTH", "")
    if "/" not in auth_value:
        pytest.fail("set NEO4J_AUTH before running Neo4j integration tests")
    username, password = auth_value.split("/", 1)
    data_version = f"{TEST_VERSION_PREFIX}{uuid.uuid4().hex[:12]}"
    foreign_data_version = f"{TEST_VERSION_PREFIX}{uuid.uuid4().hex[:12]}"
    first_product_id = f"{data_version}_product_1"
    second_product_id = f"{data_version}_product_2"
    category_id = f"{data_version}_category"
    driver = GraphDatabase.driver(
        "bolt://127.0.0.1:7687",
        auth=(username, password),
    )
    driver.verify_connectivity()
    driver.execute_query(
        """
        CREATE (first:Product {
            product_id: $first_product_id,
            name: '测试商品一',
            active: true,
            data_version: $data_version
        })
        CREATE (second:Product {
            product_id: $second_product_id,
            name: '测试商品二',
            active: true,
            data_version: $data_version
        })
        CREATE (foreign:Product {
            product_id: $foreign_product_id,
            name: '其他版本商品',
            active: true,
            data_version: $foreign_data_version
        })
        CREATE (foreign_brand:Brand {
            brand_id: $foreign_brand_id,
            name: '其他版本品牌',
            data_version: $foreign_data_version
        })
        CREATE (foreign_category:Category {
            category_id: $category_id,
            name: '其他版本品类',
            data_version: $foreign_data_version
        })
        CREATE (foreign_promotion:Promotion {
            promotion_id: $foreign_promotion_id,
            name: '其他版本活动',
            starts_at: datetime('2026-01-01T00:00:00Z'),
            ends_at: datetime('2026-12-31T23:59:59Z'),
            rules: '其他版本规则',
            data_version: $foreign_data_version
        })
        CREATE (brand:Brand {
            brand_id: $brand_id,
            name: '测试品牌',
            data_version: $data_version
        })
        CREATE (category:Category {
            category_id: $category_id,
            name: '测试品类',
            data_version: $data_version
        })
        CREATE (attribute:Attribute {
            attribute_id: $attribute_id,
            name: '颜色',
            value: '黑色',
            data_version: $data_version
        })
        CREATE (promotion:Promotion {
            promotion_id: $promotion_id,
            name: '测试活动',
            starts_at: datetime('2026-01-01T00:00:00Z'),
            ends_at: datetime('2026-12-31T23:59:59Z'),
            rules: '测试规则',
            data_version: $data_version
        })
        CREATE (first)-[:MADE_BY]->(brand)
        CREATE (first)-[:IN_CATEGORY]->(category)
        CREATE (first)-[:HAS_ATTRIBUTE]->(attribute)
        CREATE (first)-[:ELIGIBLE_FOR]->(promotion)
        CREATE (first)-[:RELATED_TO]->(second)
        CREATE (first)-[:RELATED_TO]->(foreign)
        CREATE (foreign)-[:RELATED_TO]->(second)
        CREATE (first)-[:MADE_BY]->(foreign_brand)
        CREATE (first)-[:IN_CATEGORY]->(foreign_category)
        CREATE (first)-[:ELIGIBLE_FOR]->(foreign_promotion)
        """,
        parameters_={
            "first_product_id": first_product_id,
            "second_product_id": second_product_id,
            "foreign_product_id": f"{foreign_data_version}_product",
            "foreign_brand_id": f"{foreign_data_version}_brand",
            "foreign_promotion_id": f"{foreign_data_version}_promotion",
            "brand_id": f"{data_version}_brand",
            "category_id": category_id,
            "attribute_id": f"{data_version}_attribute",
            "promotion_id": f"{data_version}_promotion",
            "data_version": data_version,
            "foreign_data_version": foreign_data_version,
        },
        database_="neo4j",
    )
    try:
        yield IsolatedGraph(
            driver=driver,
            data_version=data_version,
            first_product_id=first_product_id,
            second_product_id=second_product_id,
            category_id=category_id,
        )
    finally:
        for owned_version in (data_version, foreign_data_version):
            assert owned_version.startswith(TEST_VERSION_PREFIX)
            driver.execute_query(
                "MATCH (node {data_version: $data_version}) DETACH DELETE node",
                parameters_={"data_version": owned_version},
                database_="neo4j",
            )
            records, _, _ = driver.execute_query(
                """
                MATCH (node {data_version: $data_version})
                RETURN count(node) AS remaining
                """,
                parameters_={"data_version": owned_version},
                database_="neo4j",
            )
            assert records[0]["remaining"] == 0
        driver.close()


@pytest.mark.integration_neo4j
def test_whitelisted_queries_return_versioned_entities_and_paths(
    isolated_graph: IsolatedGraph,
) -> None:
    registry = GraphQueryRegistry(
        driver=Neo4jDriverAdapter(
            driver=isolated_graph.driver,
            database="neo4j",
        ),
        templates=build_graph_templates(max_path_depth=2, result_limit=10),
        data_version=isolated_graph.data_version,
    )

    context = registry.execute(
        "product_context",
        {"product_id": isolated_graph.first_product_id},
    )
    paths = registry.execute(
        "bounded_path",
        {
            "left_product_id": isolated_graph.first_product_id,
            "right_product_id": isolated_graph.second_product_id,
        },
    )
    category_products = registry.execute(
        "category_products",
        {"category_id": isolated_graph.category_id},
    )
    promotions = registry.execute(
        "active_promotions",
        {
            "product_id": isolated_graph.first_product_id,
            "as_of": "2026-09-01T00:00:00Z",
        },
    )
    related = registry.execute(
        "related_products",
        {"product_id": isolated_graph.first_product_id},
    )

    assert context.template_id == "product_context"
    assert context.data_version == isolated_graph.data_version
    assert {record["relationship"] for record in context.records} == {
        "MADE_BY",
        "IN_CATEGORY",
        "HAS_ATTRIBUTE",
    }
    assert len(context.records) == 3
    assert len(paths.records) == 1
    assert paths.records[0]["relationships"] == ["RELATED_TO"]
    assert category_products.records[0]["product_id"] == (
        isolated_graph.first_product_id
    )
    assert len(category_products.records) == 1
    assert promotions.records[0]["promotion_name"] == "测试活动"
    assert len(promotions.records) == 1
    assert related.records[0]["related_product_id"] == (
        isolated_graph.second_product_id
    )
    assert len(related.records) == 1
