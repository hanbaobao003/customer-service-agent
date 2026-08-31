from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from psycopg import sql
import pytest

from customer_service_agent.commerce.orders import Order, OrderStatus
from customer_service_agent.commerce.postgres import (
    PostgresCommerceStore,
    PostgresSqlReader,
)
from customer_service_agent.commerce.sql import (
    SqlTimeout,
    ValidatedQuery,
    create_business_sql_guard,
)
from customer_service_agent.config import SQL_STATEMENT_TIMEOUT_MS


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def order(order_id: str, customer_id: str) -> Order:
    return Order(
        order_id=order_id,
        customer_id=customer_id,
        status=OrderStatus.PAID,
        contact_name="测试客户",
        contact_phone="13800000000",
        shipping_address="测试地址",
        currency="CNY",
        total_amount=Decimal("10.00"),
        items=(),
        created_at=NOW,
        updated_at=NOW,
        version=1,
    )


async def prepare_database(postgres_sql_dsns):
    store = PostgresCommerceStore(postgres_sql_dsns.owner)
    await store.setup()
    for index in range(55):
        await store.create(order(f"customer-a-{index:02}", "customer-a"))
    await store.create(order("customer-b-00", "customer-b"))
    async with await psycopg.AsyncConnection.connect(
        postgres_sql_dsns.owner
    ) as connection:
        await connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(postgres_sql_dsns.reader_role)
            )
        )
        await connection.execute(
            sql.SQL(
                "GRANT SELECT ON customer_orders, customer_order_items, "
                "customer_return_requests, catalog_products, "
                "catalog_promotions TO {}"
            ).format(sql.Identifier(postgres_sql_dsns.reader_role))
        )
    return store


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_reader_role_filters_customer_and_enforces_fifty_row_limit(
    postgres_sql_dsns,
) -> None:
    await prepare_database(postgres_sql_dsns)
    query = create_business_sql_guard().validate(
        "SELECT order_id, total_amount FROM customer_orders ORDER BY order_id",
        customer_id="customer-a",
    )

    rows = await PostgresSqlReader(postgres_sql_dsns.reader).execute_readonly(
        query,
        timeout_ms=SQL_STATEMENT_TIMEOUT_MS,
    )

    assert len(rows) == 50
    assert rows[0] == {"order_id": "customer-a-00", "total_amount": "10.00"}
    assert all(row["order_id"].startswith("customer-a-") for row in rows)


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_reader_role_cannot_write_base_tables(postgres_sql_dsns) -> None:
    await prepare_database(postgres_sql_dsns)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with await psycopg.AsyncConnection.connect(
            postgres_sql_dsns.reader
        ) as connection:
            await connection.execute("DELETE FROM orders")


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_reader_transaction_is_readonly_even_with_owner_credentials(
    postgres_sql_dsns,
) -> None:
    await prepare_database(postgres_sql_dsns)
    unsafe_query = ValidatedQuery(
        sql="DELETE FROM orders",
        relations=("customer_orders",),
        parameters={},
        row_limit=50,
    )

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await PostgresSqlReader(postgres_sql_dsns.owner).execute_readonly(
            unsafe_query,
            timeout_ms=SQL_STATEMENT_TIMEOUT_MS,
        )


@pytest.mark.integration_postgres
@pytest.mark.asyncio
async def test_reader_maps_statement_timeout_to_stable_error(
    postgres_sql_dsns,
) -> None:
    await prepare_database(postgres_sql_dsns)
    slow_query = ValidatedQuery(
        sql="SELECT pg_sleep(1)",
        relations=("catalog_products",),
        parameters={},
        row_limit=1,
    )

    with pytest.raises(SqlTimeout) as exc:
        await PostgresSqlReader(postgres_sql_dsns.reader).execute_readonly(
            slow_query,
            timeout_ms=10,
        )

    assert exc.value.code == "SQL_TIMEOUT"
