import pytest
from neo4j import RoutingControl

import customer_service_agent.retrieval.graph as graph_module
from customer_service_agent.retrieval.graph import (
    GRAPH_NODE_LABELS,
    GRAPH_RELATIONSHIP_TYPES,
    GraphQueryRegistry,
    GraphQueryRejected,
    build_graph_templates,
)


class RecordingDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        cypher: str,
        parameters: dict[str, object],
    ) -> list[dict[str, object]]:
        self.calls.append((cypher, parameters))
        return [{"entity_id": "product-1", "path": ["MADE_BY"]}]


class FailingIfCalledDriver:
    def execute(
        self,
        cypher: str,
        parameters: dict[str, object],
    ) -> list[dict[str, object]]:
        raise AssertionError("driver must not be called")


class FakeRecord:
    def data(self) -> dict[str, object]:
        return {"entity_id": "product-1", "relationship": "MADE_BY"}


class FakeNeo4jDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute_query(self, query: str, **kwargs: object) -> object:
        self.calls.append({"query": query, **kwargs})
        return ([FakeRecord()], object(), ["entity_id", "relationship"])


def registry(driver: object) -> GraphQueryRegistry:
    return GraphQueryRegistry(
        driver=driver,
        templates=build_graph_templates(max_path_depth=2, result_limit=5),
        data_version="commerce-graph-v1",
    )


@pytest.mark.unit
def test_unknown_graph_template_is_rejected_without_driver_call() -> None:
    graph = GraphQueryRegistry(
        driver=FailingIfCalledDriver(),
        templates={},
        data_version="commerce-graph-v1",
    )

    with pytest.raises(GraphQueryRejected, match="unknown template"):
        graph.execute("model_generated", {})


@pytest.mark.unit
@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"product_id": "product-1", "cypher": "MATCH (n) RETURN n"},
        {"product_id": 1},
        {"product_id": ""},
    ],
)
def test_template_parameters_are_exact_and_typed(parameters: dict[str, object]) -> None:
    graph = registry(FailingIfCalledDriver())

    with pytest.raises(GraphQueryRejected, match="parameter"):
        graph.execute("product_context", parameters)


@pytest.mark.unit
def test_registered_query_uses_versioned_cypher_and_internal_limits() -> None:
    driver = RecordingDriver()
    graph = registry(driver)

    result = graph.execute("product_context", {"product_id": "product-1"})

    assert result.template_id == "product_context"
    assert result.data_version == "commerce-graph-v1"
    assert result.records[0]["entity_id"] == "product-1"
    assert len(driver.calls) == 1
    cypher, parameters = driver.calls[0]
    assert cypher == graph.templates["product_context"].cypher
    assert parameters == {
        "product_id": "product-1",
        "data_version": "commerce-graph-v1",
        "result_limit": 5,
    }


@pytest.mark.unit
def test_neo4j_adapter_uses_read_routing_and_returns_plain_records() -> None:
    driver = FakeNeo4jDriver()
    adapter_class = graph_module.Neo4jDriverAdapter
    adapter = adapter_class(driver=driver, database="neo4j")

    records = adapter.execute("RETURN $value AS value", {"value": "ok"})

    assert records == [
        {"entity_id": "product-1", "relationship": "MADE_BY"}
    ]
    assert driver.calls == [
        {
            "query": "RETURN $value AS value",
            "parameters_": {"value": "ok"},
            "database_": "neo4j",
            "routing_": RoutingControl.READ,
        }
    ]


@pytest.mark.unit
def test_graph_schema_and_query_families_are_fixed() -> None:
    templates = build_graph_templates(max_path_depth=3, result_limit=7)

    assert GRAPH_NODE_LABELS == {
        "Product",
        "Brand",
        "Category",
        "Promotion",
        "Attribute",
    }
    assert GRAPH_RELATIONSHIP_TYPES == {
        "MADE_BY",
        "IN_CATEGORY",
        "ELIGIBLE_FOR",
        "HAS_ATTRIBUTE",
        "PARENT_OF",
        "RELATED_TO",
    }
    assert set(templates) == {
        "product_context",
        "category_products",
        "active_promotions",
        "related_products",
        "bounded_path",
    }
    assert "*1..3" in templates["bounded_path"].cypher
    assert all(template.result_limit == 7 for template in templates.values())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("max_path_depth", "result_limit"),
    [(0, 5), (2, 0), (-1, 5), (2, -1)],
)
def test_graph_limits_must_be_explicit_positive_values(
    max_path_depth: int,
    result_limit: int,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_graph_templates(
            max_path_depth=max_path_depth,
            result_limit=result_limit,
        )
