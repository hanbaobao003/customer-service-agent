"""PostgreSQL persistence for orders, approvals, and audit events."""

from collections.abc import Awaitable, Callable
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from customer_service_agent.commerce.orders import (
    OperationHashMismatch,
    OperationPreview,
    OperationStatus,
    OperationUnavailable,
    Order,
    OrderCommandRepository,
    OrderItem,
    OrderStatus,
    OrderVersionConflict,
    ReturnRequest,
)


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id text PRIMARY KEY,
        customer_id text NOT NULL,
        status text NOT NULL,
        contact_name text NOT NULL,
        contact_phone text NOT NULL,
        shipping_address text NOT NULL,
        currency text NOT NULL,
        total_amount numeric NOT NULL,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        version integer NOT NULL,
        cancellation_refund_status text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        order_id text NOT NULL REFERENCES orders(order_id),
        position integer NOT NULL,
        product_id text NOT NULL,
        product_name_snapshot text NOT NULL,
        unit_price numeric NOT NULL,
        quantity integer NOT NULL,
        PRIMARY KEY (order_id, position)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS return_requests (
        return_id text PRIMARY KEY,
        order_id text NOT NULL UNIQUE REFERENCES orders(order_id),
        customer_id text NOT NULL,
        reason_code text NOT NULL,
        reason_text text NOT NULL,
        status text NOT NULL,
        refund_status text NOT NULL,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_operations (
        operation_id text PRIMARY KEY,
        customer_id text NOT NULL,
        thread_id text NOT NULL,
        request_id text NOT NULL,
        interrupt_id text NOT NULL,
        tool_name text NOT NULL,
        normalized_args jsonb NOT NULL,
        args_hash text NOT NULL,
        expected_version integer,
        status text NOT NULL,
        result_artifact jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        operation_id text NOT NULL UNIQUE REFERENCES order_operations(operation_id),
        customer_id text NOT NULL,
        thread_id text NOT NULL,
        request_id text NOT NULL,
        interrupt_id text NOT NULL,
        tool_name text NOT NULL,
        result_artifact jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
)


class PostgresCommerceStore:
    """Use one PostgreSQL transaction for domain writes and approval results."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(
            self._dsn,
            row_factory=dict_row,
        )

    async def setup(self) -> None:
        async with await self._connect() as connection:
            for statement in SCHEMA:
                await connection.execute(statement)

    async def save_operation(self, operation: OperationPreview) -> None:
        async with await self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO order_operations (
                    operation_id, customer_id, thread_id, request_id, interrupt_id,
                    tool_name, normalized_args, args_hash, expected_version,
                    status, result_artifact
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    operation.operation_id,
                    operation.customer_id,
                    operation.thread_id,
                    operation.request_id,
                    operation.interrupt_id,
                    operation.tool_name,
                    Jsonb(operation.normalized_args),
                    operation.args_hash,
                    operation.expected_version,
                    operation.status.value,
                    None,
                ),
            )

    async def get_operation(
        self,
        operation_id: str,
        customer_id: str,
        thread_id: str,
    ) -> OperationPreview | None:
        async with await self._connect() as connection:
            return await _get_operation(
                connection,
                operation_id,
                customer_id,
                thread_id,
            )

    async def update_operation(self, operation: OperationPreview) -> None:
        async with await self._connect() as connection:
            await connection.execute(
                """
                UPDATE order_operations
                SET status = %s, result_artifact = %s
                WHERE operation_id = %s AND customer_id = %s AND thread_id = %s
                """,
                (
                    operation.status.value,
                    Jsonb(operation.result_artifact)
                    if operation.result_artifact is not None
                    else None,
                    operation.operation_id,
                    operation.customer_id,
                    operation.thread_id,
                ),
            )

    async def execute_atomic(
        self,
        operation: OperationPreview,
        executor: Callable[
            [OperationPreview, OrderCommandRepository],
            Awaitable[dict[str, object]],
        ],
    ) -> dict[str, object]:
        async with await self._connect() as connection:
            current = await _get_operation(
                connection,
                operation.operation_id,
                operation.customer_id,
                operation.thread_id,
                for_update=True,
            )
            if current is None:
                raise OperationUnavailable("operation unavailable")
            if current.args_hash != operation.args_hash:
                raise OperationHashMismatch("operation arguments changed")
            if current.status is OperationStatus.EXECUTED:
                return dict(current.result_artifact or {})
            if current.status is not OperationStatus.PENDING:
                raise OperationUnavailable("operation is not pending")

            result = await executor(current, _TransactionOrderRepository(connection))
            await connection.execute(
                """
                UPDATE order_operations
                SET status = %s, result_artifact = %s
                WHERE operation_id = %s
                """,
                (
                    OperationStatus.EXECUTED.value,
                    Jsonb(result),
                    current.operation_id,
                ),
            )
            await self._write_audit(connection, current, result)
            return result

    async def _write_audit(
        self,
        connection,
        operation: OperationPreview,
        result: dict[str, object],
    ) -> None:
        await connection.execute(
            """
            INSERT INTO audit_events (
                operation_id, customer_id, thread_id, request_id, interrupt_id,
                tool_name, result_artifact
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                operation.operation_id,
                operation.customer_id,
                operation.thread_id,
                operation.request_id,
                operation.interrupt_id,
                operation.tool_name,
                Jsonb(result),
            ),
        )

    async def get(self, order_id: str, customer_id: str) -> Order | None:
        async with await self._connect() as connection:
            return await _TransactionOrderRepository(connection).get(
                order_id,
                customer_id,
            )

    async def create(self, order: Order) -> None:
        async with await self._connect() as connection:
            await _TransactionOrderRepository(connection).create(order)

    async def save(self, order: Order, *, expected_version: int) -> None:
        async with await self._connect() as connection:
            await _TransactionOrderRepository(connection).save(
                order,
                expected_version=expected_version,
            )

    async def create_return(self, request: ReturnRequest) -> None:
        async with await self._connect() as connection:
            await _TransactionOrderRepository(connection).create_return(request)


class _TransactionOrderRepository:
    def __init__(self, connection) -> None:
        self._connection = connection

    async def get(self, order_id: str, customer_id: str) -> Order | None:
        cursor = await self._connection.execute(
            "SELECT * FROM orders WHERE order_id = %s AND customer_id = %s",
            (order_id, customer_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        items_cursor = await self._connection.execute(
            "SELECT * FROM order_items WHERE order_id = %s ORDER BY position",
            (order_id,),
        )
        items = await items_cursor.fetchall()
        return Order(
            order_id=row["order_id"],
            customer_id=row["customer_id"],
            status=OrderStatus(row["status"]),
            contact_name=row["contact_name"],
            contact_phone=row["contact_phone"],
            shipping_address=row["shipping_address"],
            currency=row["currency"],
            total_amount=Decimal(row["total_amount"]),
            items=tuple(
                OrderItem(
                    product_id=item["product_id"],
                    product_name_snapshot=item["product_name_snapshot"],
                    unit_price=Decimal(item["unit_price"]),
                    quantity=item["quantity"],
                )
                for item in items
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
            cancellation_refund_status=row["cancellation_refund_status"],
        )

    async def create(self, order: Order) -> None:
        await self._connection.execute(
            """
            INSERT INTO orders (
                order_id, customer_id, status, contact_name, contact_phone,
                shipping_address, currency, total_amount, created_at, updated_at,
                version, cancellation_refund_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order.order_id,
                order.customer_id,
                order.status.value,
                order.contact_name,
                order.contact_phone,
                order.shipping_address,
                order.currency,
                order.total_amount,
                order.created_at,
                order.updated_at,
                order.version,
                order.cancellation_refund_status,
            ),
        )
        for position, item in enumerate(order.items):
            await self._connection.execute(
                """
                INSERT INTO order_items (
                    order_id, position, product_id, product_name_snapshot,
                    unit_price, quantity
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    order.order_id,
                    position,
                    item.product_id,
                    item.product_name_snapshot,
                    item.unit_price,
                    item.quantity,
                ),
            )

    async def save(self, order: Order, *, expected_version: int) -> None:
        cursor = await self._connection.execute(
            """
            UPDATE orders SET
                status = %s,
                contact_name = %s,
                contact_phone = %s,
                shipping_address = %s,
                updated_at = %s,
                version = %s,
                cancellation_refund_status = %s
            WHERE order_id = %s AND customer_id = %s AND version = %s
            """,
            (
                order.status.value,
                order.contact_name,
                order.contact_phone,
                order.shipping_address,
                order.updated_at,
                order.version,
                order.cancellation_refund_status,
                order.order_id,
                order.customer_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise OrderVersionConflict("order version changed")

    async def create_return(self, request: ReturnRequest) -> None:
        await self._connection.execute(
            """
            INSERT INTO return_requests (
                return_id, order_id, customer_id, reason_code, reason_text,
                status, refund_status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.return_id,
                request.order_id,
                request.customer_id,
                request.reason_code,
                request.reason_text,
                request.status,
                request.refund_status,
                request.created_at,
                request.updated_at,
            ),
        )


async def _get_operation(
    connection,
    operation_id: str,
    customer_id: str,
    thread_id: str,
    *,
    for_update: bool = False,
) -> OperationPreview | None:
    lock = " FOR UPDATE" if for_update else ""
    cursor = await connection.execute(
        """
        SELECT * FROM order_operations
        WHERE operation_id = %s AND customer_id = %s AND thread_id = %s
        """
        + lock,
        (operation_id, customer_id, thread_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return OperationPreview(
        operation_id=row["operation_id"],
        customer_id=row["customer_id"],
        thread_id=row["thread_id"],
        request_id=row["request_id"],
        interrupt_id=row["interrupt_id"],
        tool_name=row["tool_name"],
        normalized_args=row["normalized_args"],
        args_hash=row["args_hash"],
        expected_version=row["expected_version"],
        status=OperationStatus(row["status"]),
        result_artifact=row["result_artifact"],
    )
