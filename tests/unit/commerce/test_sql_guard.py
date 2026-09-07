import pytest

from customer_service_agent.commerce.sql import (
    SqlGuard,
    SqlPolicyRejected,
    create_business_sql_guard,
)


def guard() -> SqlGuard:
    return SqlGuard(
        customer_relations={"customer_orders", "customer_order_items"},
        public_relations={"catalog_products"},
        allowed_columns={
            "customer_orders": {"order_id", "status", "total_amount"},
            "customer_order_items": {"order_id", "product_id", "quantity"},
            "catalog_products": {"product_id", "product_name"},
        },
        allowed_functions={"COUNT", "SUM"},
        max_rows=25,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customer_orders",
        "SELECT order_id FROM customer_orders; SELECT 1",
        "SELECT order_id FROM customer_orders FOR UPDATE",
        "COPY customer_orders TO STDOUT",
        "SELECT order_id FROM pg_catalog.pg_tables",
        "SELECT secret_column FROM customer_orders",
        "SELECT unknown_function(order_id) FROM customer_orders",
        "SELECT order_id FROM customer_orders WHERE customer_id = 'other'",
        "SELECT * FROM customer_orders",
    ],
)
def test_guard_rejects_unsafe_or_out_of_scope_sql(sql: str) -> None:
    with pytest.raises(SqlPolicyRejected) as exc:
        guard().validate(sql, customer_id="customer-1")

    assert exc.value.code == "SQL_POLICY_REJECTED"


@pytest.mark.unit
def test_guard_injects_trusted_customer_and_bounded_limit() -> None:
    query = guard().validate(
        "SELECT order_id, status FROM customer_orders ORDER BY order_id",
        customer_id="customer-1",
    )

    assert query.sql == (
        "SELECT order_id, status FROM customer_orders "
        "WHERE customer_orders.customer_id = %(customer_id)s "
        "ORDER BY order_id LIMIT 25"
    )
    assert query.parameters == {"customer_id": "customer-1"}
    assert query.relations == ("customer_orders",)
    assert query.row_limit == 25


@pytest.mark.unit
def test_guard_keeps_stricter_literal_limit_for_public_query() -> None:
    query = guard().validate(
        "SELECT product_id FROM catalog_products LIMIT 5",
        customer_id="customer-1",
    )

    assert query.sql == "SELECT product_id FROM catalog_products LIMIT 5"
    assert query.parameters == {}
    assert query.row_limit == 5


@pytest.mark.unit
def test_guard_scopes_each_customer_relation_in_join() -> None:
    query = guard().validate(
        """
        SELECT o.order_id, i.product_id
        FROM customer_orders AS o
        JOIN customer_order_items AS i ON o.order_id = i.order_id
        """,
        customer_id="customer-1",
    )

    assert "o.customer_id = %(customer_id)s" in query.sql
    assert "i.customer_id = %(customer_id)s" in query.sql


@pytest.mark.unit
def test_guard_allows_cte_that_ends_in_select() -> None:
    query = guard().validate(
        """
        WITH recent AS (
            SELECT order_id, status FROM customer_orders
        )
        SELECT order_id FROM recent
        """,
        customer_id="customer-1",
    )

    assert "customer_orders.customer_id = %(customer_id)s" in query.sql
    assert query.relations == ("customer_orders",)


@pytest.mark.unit
def test_guard_checks_qualified_column_against_its_relation() -> None:
    with pytest.raises(SqlPolicyRejected):
        guard().validate(
            """
            SELECT o.product_id
            FROM customer_orders AS o
            JOIN customer_order_items AS i ON o.order_id = i.order_id
            """,
            customer_id="customer-1",
        )


@pytest.mark.unit
def test_business_guard_uses_approved_fifty_row_limit() -> None:
    query = create_business_sql_guard().validate(
        "SELECT order_id FROM customer_orders",
        customer_id="customer-1",
    )

    assert query.row_limit == 50
    assert query.sql.endswith("LIMIT 50")
