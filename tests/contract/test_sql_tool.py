import json

import pytest

from customer_service_agent.commerce.sql import (
    ProposedSql,
    SqlGuard,
    SqlOutputInvalid,
    SqlQueryService,
    create_query_business_data_tool,
)
from customer_service_agent.shared.models import RuntimeContext


class Generator:
    def __init__(self, proposed: ProposedSql) -> None:
        self.proposed = proposed

    async def generate(self, question: str) -> ProposedSql:
        assert question == "我的订单有多少个？"
        return self.proposed


class Reader:
    def __init__(self) -> None:
        self.calls = []

    async def execute_readonly(self, query, *, timeout_ms: int):
        self.calls.append((query, timeout_ms))
        return [{"order_count": 2}]


def guard() -> SqlGuard:
    return SqlGuard(
        customer_relations={"customer_orders"},
        public_relations={"catalog_products"},
        allowed_columns={
            "customer_orders": {"order_id", "status"},
            "catalog_products": {"product_id", "product_name"},
        },
        allowed_functions={"COUNT"},
        max_rows=10,
    )


def context() -> RuntimeContext:
    return RuntimeContext.trusted(
        customer_id="customer-1",
        thread_id="thread-1",
        request_id="request-1",
    )


def service(proposed: ProposedSql, reader: Reader | None = None):
    target_reader = reader or Reader()
    return (
        SqlQueryService(
            guard=guard(),
            generator=Generator(proposed),
            reader=target_reader,
            timeout_ms=1500,
        ),
        target_reader,
    )


@pytest.mark.contract
def test_query_tool_schema_only_exposes_question() -> None:
    query_service, _ = service(
        ProposedSql(
            sql="SELECT COUNT(order_id) AS order_count FROM customer_orders",
            explanation="统计订单数量",
            referenced_relations=("customer_orders",),
        )
    )

    schema = (
        create_query_business_data_tool(query_service)
        .tool_call_schema.model_json_schema()
    )

    assert set(schema["properties"]) == {"question"}
    assert "customer_id" not in str(schema)
    assert "sql" not in schema["properties"]


@pytest.mark.contract
@pytest.mark.asyncio
async def test_service_uses_trusted_customer_and_separates_artifact() -> None:
    reader = Reader()
    query_service, _ = service(
        ProposedSql(
            sql="SELECT COUNT(order_id) AS order_count FROM customer_orders",
            explanation="统计订单数量",
            referenced_relations=("customer_orders",),
        ),
        reader,
    )

    result = await query_service.answer(context(), "我的订单有多少个？")

    query, timeout_ms = reader.calls[0]
    assert query.parameters == {"customer_id": "customer-1"}
    assert timeout_ms == 1500
    assert result.content == {"rows": [{"order_count": 2}], "row_count": 1}
    assert result.artifact["sql"] == query.sql
    assert result.artifact["execution_id"] == "request-1"
    assert result.artifact["parameter_names"] == ["customer_id"]
    assert result.artifact["truncated"] is False
    assert "customer-1" not in json.dumps(result.artifact, ensure_ascii=False)


@pytest.mark.contract
@pytest.mark.asyncio
async def test_service_rejects_generator_relation_mismatch_without_querying() -> None:
    reader = Reader()
    query_service, _ = service(
        ProposedSql(
            sql="SELECT order_id FROM customer_orders",
            explanation="读取订单",
            referenced_relations=("catalog_products",),
        ),
        reader,
    )

    with pytest.raises(SqlOutputInvalid) as exc:
        await query_service.answer(context(), "我的订单有多少个？")

    assert exc.value.code == "SQL_OUTPUT_INVALID"
    assert reader.calls == []


@pytest.mark.contract
@pytest.mark.asyncio
async def test_service_never_exposes_more_rows_than_validated_limit() -> None:
    class ExcessReader(Reader):
        async def execute_readonly(self, query, *, timeout_ms: int):
            return [{"order_id": str(index)} for index in range(12)]

    query_service, _ = service(
        ProposedSql(
            sql="SELECT order_id FROM customer_orders",
            explanation="读取订单",
            referenced_relations=("customer_orders",),
        ),
        ExcessReader(),
    )

    result = await query_service.answer(context(), "我的订单有多少个？")

    assert len(result.content["rows"]) == 10
    assert result.content["row_count"] == 10
    assert result.artifact["truncated"] is True
