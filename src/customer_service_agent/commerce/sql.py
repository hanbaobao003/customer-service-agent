"""AST-guarded read-only business queries."""

import json
from typing import Protocol

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

from customer_service_agent.config import SQL_MAX_ROWS
from customer_service_agent.shared.models import RuntimeContext


class SqlPolicyRejected(Exception):
    code = "SQL_POLICY_REJECTED"


class SqlOutputInvalid(Exception):
    code = "SQL_OUTPUT_INVALID"


class SqlTimeout(Exception):
    code = "SQL_TIMEOUT"


class ProposedSql(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: str
    explanation: str
    referenced_relations: tuple[str, ...]


class SqlResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: dict[str, object]
    artifact: dict[str, object]

    def to_tool_output(self) -> tuple[str, dict[str, object]]:
        return json.dumps(self.content, ensure_ascii=False), self.artifact


class ValidatedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: str
    relations: tuple[str, ...]
    parameters: dict[str, str]
    row_limit: int


class SqlGuard:
    def __init__(
        self,
        *,
        customer_relations: set[str],
        public_relations: set[str],
        allowed_columns: dict[str, set[str]],
        allowed_functions: set[str],
        max_rows: int,
    ) -> None:
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")
        self._customer_relations = frozenset(customer_relations)
        self._public_relations = frozenset(public_relations)
        self._allowed_relations = self._customer_relations | self._public_relations
        self._allowed_columns = {
            relation: frozenset(columns)
            for relation, columns in allowed_columns.items()
        }
        if set(self._allowed_columns) != self._allowed_relations:
            raise ValueError("allowed columns must match allowed relations")
        self._allowed_functions = frozenset(
            function.upper() for function in allowed_functions
        )
        self._max_rows = max_rows

    def validate(self, sql: str, *, customer_id: str) -> ValidatedQuery:
        try:
            statements = [
                statement
                for statement in parse(sql, read="postgres")
                if statement
            ]
        except ParseError as exc:
            raise SqlPolicyRejected("SQL could not be parsed") from exc
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise SqlPolicyRejected("only one SELECT statement is allowed")
        query = statements[0]
        if query.find(exp.Lock) is not None:
            raise SqlPolicyRejected("row locking is not allowed")
        if query.find(exp.Star) is not None:
            raise SqlPolicyRejected("star projections are not allowed")
        if any(
            column.name.lower() == "customer_id"
            for column in query.find_all(exp.Column)
        ):
            raise SqlPolicyRejected("customer scope is supplied by the service")

        cte_names = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
        tables = tuple(
            sorted(
                {
                    table.name.lower()
                    for table in query.find_all(exp.Table)
                    if table.name.lower() not in cte_names
                }
            )
        )
        if not tables or any(table not in self._allowed_relations for table in tables):
            raise SqlPolicyRejected("query contains an unknown relation")

        allowed_column_names = set().union(
            *(self._allowed_columns[table] for table in tables)
        )
        relation_by_alias = {
            table.alias_or_name.lower(): table.name.lower()
            for table in query.find_all(exp.Table)
            if table.name.lower() not in cte_names
        }
        for column in query.find_all(exp.Column):
            if column.table:
                qualifier = column.table.lower()
                relation = relation_by_alias.get(qualifier)
                if relation is None and qualifier not in cte_names:
                    raise SqlPolicyRejected("query contains an unknown table alias")
                permitted = (
                    self._allowed_columns[relation]
                    if relation is not None
                    else allowed_column_names
                )
            else:
                permitted = allowed_column_names
            if column.name not in permitted:
                raise SqlPolicyRejected("query contains an unknown column")

        for function in query.find_all(exp.Func):
            name = (
                function.name
                if isinstance(function, exp.Anonymous)
                else function.sql_name()
            ).upper()
            if name not in self._allowed_functions:
                raise SqlPolicyRejected("query contains a forbidden function")

        customer_scoped = False
        for scope in traverse_scope(query):
            predicates = []
            for table in scope.tables:
                if table.name.lower() not in self._customer_relations:
                    continue
                customer_scoped = True
                predicates.append(
                    exp.column("customer_id", table=table.alias_or_name).eq(
                        exp.Placeholder(this="customer_id")
                    )
                )
            if predicates:
                predicate = predicates[0]
                for additional in predicates[1:]:
                    predicate = predicate.and_(additional)
                scope.expression.where(predicate, copy=False)

        row_limit = self._max_rows
        existing_limit = query.args.get("limit")
        if existing_limit is not None:
            literal = existing_limit.expression
            if not isinstance(literal, exp.Literal) or not literal.is_int:
                raise SqlPolicyRejected("LIMIT must be an integer literal")
            row_limit = min(int(literal.this), self._max_rows)
        query.limit(row_limit, copy=False)

        return ValidatedQuery(
            sql=query.sql(dialect="postgres"),
            relations=tables,
            parameters={"customer_id": customer_id} if customer_scoped else {},
            row_limit=row_limit,
        )


def create_business_sql_guard() -> SqlGuard:
    return SqlGuard(
        customer_relations={
            "customer_orders",
            "customer_order_items",
            "customer_return_requests",
        },
        public_relations={"catalog_products", "catalog_promotions"},
        allowed_columns={
            "customer_orders": {
                "order_id",
                "status",
                "currency",
                "total_amount",
                "created_at",
                "updated_at",
                "version",
            },
            "customer_order_items": {
                "order_id",
                "product_id",
                "product_name_snapshot",
                "unit_price",
                "quantity",
            },
            "customer_return_requests": {
                "return_id",
                "order_id",
                "reason_code",
                "status",
                "refund_status",
                "created_at",
                "updated_at",
            },
            "catalog_products": {
                "product_id",
                "product_name",
                "unit_price",
                "currency",
            },
            "catalog_promotions": {
                "promotion_id",
                "promotion_name",
                "description",
            },
        },
        allowed_functions={"COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE"},
        max_rows=SQL_MAX_ROWS,
    )


class SqlGenerator(Protocol):
    async def generate(self, question: str) -> ProposedSql: ...


class SqlReadPort(Protocol):
    async def execute_readonly(
        self,
        query: ValidatedQuery,
        *,
        timeout_ms: int,
    ) -> list[dict[str, object]]: ...


class SqlQueryService:
    def __init__(
        self,
        *,
        guard: SqlGuard,
        generator: SqlGenerator,
        reader: SqlReadPort,
        timeout_ms: int,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._guard = guard
        self._generator = generator
        self._reader = reader
        self._timeout_ms = timeout_ms

    async def answer(
        self,
        context: RuntimeContext,
        question: str,
    ) -> SqlResult:
        proposed = await self._generator.generate(question)
        query = self._guard.validate(
            proposed.sql,
            customer_id=context.customer_id,
        )
        if tuple(sorted(proposed.referenced_relations)) != query.relations:
            raise SqlOutputInvalid("referenced relations do not match SQL")
        rows = await self._reader.execute_readonly(
            query,
            timeout_ms=self._timeout_ms,
        )
        truncated = len(rows) >= query.row_limit
        visible_rows = rows[: query.row_limit]
        return SqlResult(
            content={"rows": visible_rows, "row_count": len(visible_rows)},
            artifact={
                "execution_id": context.request_id,
                "sql": query.sql,
                "explanation": proposed.explanation,
                "relations": list(query.relations),
                "parameter_names": sorted(query.parameters),
                "row_limit": query.row_limit,
                "truncated": truncated,
                "timeout_ms": self._timeout_ms,
            },
        )


def create_query_business_data_tool(service: SqlQueryService):
    @tool("query_business_data", response_format="content_and_artifact")
    async def query_business_data(
        question: str,
        runtime: ToolRuntime[RuntimeContext],
    ) -> tuple[str, dict[str, object]]:
        """查询当前客户的订单业务数据或公共商品数据。"""
        result = await service.answer(runtime.context, question)
        return result.to_tool_output()

    return query_business_data
