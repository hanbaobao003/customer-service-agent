"""GraphRAG schema and whitelisted query contracts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from neo4j import Driver, RoutingControl


GRAPH_NODE_LABELS = {
    "Product",
    "Brand",
    "Category",
    "Promotion",
    "Attribute",
}
GRAPH_RELATIONSHIP_TYPES = {
    "MADE_BY",
    "IN_CATEGORY",
    "ELIGIBLE_FOR",
    "HAS_ATTRIBUTE",
    "PARENT_OF",
    "RELATED_TO",
}


class GraphQueryRejected(ValueError):
    pass


@dataclass(frozen=True)
class GraphTemplate:
    template_id: str
    cypher: str
    parameter_types: Mapping[str, type]
    result_limit: int


@dataclass(frozen=True)
class GraphQueryResult:
    template_id: str
    data_version: str
    records: tuple[Mapping[str, object], ...]


class GraphDriverPort(Protocol):
    def execute(
        self,
        cypher: str,
        parameters: dict[str, object],
    ) -> Sequence[Mapping[str, object]]: ...


class Neo4jDriverAdapter:
    def __init__(self, *, driver: Driver, database: str) -> None:
        if not database:
            raise ValueError("database must not be empty")
        self.driver = driver
        self.database = database

    def execute(
        self,
        cypher: str,
        parameters: dict[str, object],
    ) -> list[dict[str, object]]:
        records, _, _ = self.driver.execute_query(
            cypher,
            parameters_=parameters,
            database_=self.database,
            routing_=RoutingControl.READ,
        )
        return [record.data() for record in records]


class GraphQueryRegistry:
    def __init__(
        self,
        *,
        driver: GraphDriverPort,
        templates: Mapping[str, GraphTemplate],
        data_version: str,
    ) -> None:
        if not data_version:
            raise ValueError("data_version must not be empty")
        self.driver = driver
        self.templates = dict(templates)
        self.data_version = data_version

    def execute(
        self,
        template_id: str,
        parameters: Mapping[str, object],
    ) -> GraphQueryResult:
        template = self.templates.get(template_id)
        if template is None:
            raise GraphQueryRejected(f"unknown template: {template_id}")
        expected_names = set(template.parameter_types)
        if set(parameters) != expected_names:
            raise GraphQueryRejected("parameter names do not match template")
        for name, expected_type in template.parameter_types.items():
            value = parameters[name]
            if not isinstance(value, expected_type):
                raise GraphQueryRejected(f"parameter type mismatch: {name}")
            if isinstance(value, str) and not value.strip():
                raise GraphQueryRejected(f"parameter must not be empty: {name}")

        bound_parameters = dict(parameters)
        bound_parameters.update(
            data_version=self.data_version,
            result_limit=template.result_limit,
        )
        records = self.driver.execute(template.cypher, bound_parameters)
        return GraphQueryResult(
            template_id=template_id,
            data_version=self.data_version,
            records=tuple(dict(record) for record in records),
        )


def build_graph_templates(
    *,
    max_path_depth: int,
    result_limit: int,
) -> dict[str, GraphTemplate]:
    if max_path_depth < 1 or result_limit < 1:
        raise ValueError("graph limits must be positive")

    def template(
        template_id: str,
        cypher: str,
        **parameter_types: type,
    ) -> GraphTemplate:
        return GraphTemplate(
            template_id=template_id,
            cypher=cypher.strip(),
            parameter_types=parameter_types,
            result_limit=result_limit,
        )

    templates = [
        template(
            "product_context",
            """
            MATCH (product:Product {
                product_id: $product_id,
                data_version: $data_version
            })
            OPTIONAL MATCH (product)-[relationship:
                MADE_BY|IN_CATEGORY|HAS_ATTRIBUTE
            ]->(related)
            WHERE related.data_version = $data_version
            RETURN product.product_id AS entity_id,
                   type(relationship) AS relationship,
                   labels(related) AS related_labels,
                   properties(related) AS related
            LIMIT $result_limit
            """,
            product_id=str,
        ),
        template(
            "category_products",
            """
            MATCH (product:Product {data_version: $data_version})
                  -[:IN_CATEGORY]->
                  (category:Category {
                      category_id: $category_id,
                      data_version: $data_version
                  })
            RETURN category.category_id AS entity_id,
                   product.product_id AS product_id,
                   product.name AS product_name
            ORDER BY product.product_id
            LIMIT $result_limit
            """,
            category_id=str,
        ),
        template(
            "active_promotions",
            """
            MATCH (product:Product {
                product_id: $product_id,
                data_version: $data_version
            })-[:ELIGIBLE_FOR]->(promotion:Promotion {
                data_version: $data_version
            })
            WHERE promotion.starts_at <= datetime($as_of)
              AND promotion.ends_at >= datetime($as_of)
            RETURN product.product_id AS entity_id,
                   promotion.promotion_id AS promotion_id,
                   promotion.name AS promotion_name,
                   promotion.rules AS rules
            ORDER BY promotion.promotion_id
            LIMIT $result_limit
            """,
            product_id=str,
            as_of=str,
        ),
        template(
            "related_products",
            """
            MATCH (product:Product {
                product_id: $product_id,
                data_version: $data_version
            })-[:RELATED_TO]-(related:Product {data_version: $data_version})
            RETURN product.product_id AS entity_id,
                   related.product_id AS related_product_id,
                   related.name AS related_product_name
            ORDER BY related.product_id
            LIMIT $result_limit
            """,
            product_id=str,
        ),
        template(
            "bounded_path",
            f"""
            MATCH (left:Product {{
                product_id: $left_product_id,
                data_version: $data_version
            }}),
            (right:Product {{
                product_id: $right_product_id,
                data_version: $data_version
            }})
            MATCH path = (left)-[*1..{max_path_depth}]-(right)
            WHERE all(node IN nodes(path)
                WHERE node.data_version = $data_version)
              AND all(relationship IN relationships(path)
                WHERE type(relationship) IN {sorted(GRAPH_RELATIONSHIP_TYPES)!r})
            RETURN left.product_id AS entity_id,
                   [node IN nodes(path) | properties(node)] AS nodes,
                   [relationship IN relationships(path) |
                       type(relationship)] AS relationships
            LIMIT $result_limit
            """,
            left_product_id=str,
            right_product_id=str,
        ),
    ]
    return {item.template_id: item for item in templates}
