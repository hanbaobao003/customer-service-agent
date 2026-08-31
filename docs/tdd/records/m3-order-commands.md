# M3 订单写操作与 PostgreSQL 原子事务 RED–GREEN 记录

- Spec：`docs/specs/030-orders-and-sql.md`
- 需求 ID：`ORD-CREATE-001…003`、`ORD-UPDATE-001…003`、`ORD-CANCEL-001…003`、`ORD-RETURN-001…003`、`ORD-IDEM-001…004`
- 公开行为：四类写操作先形成绑定可信上下文的预览，批准后才执行；领域写入、operation 结果和审计使用同一 PostgreSQL 事务。
- 测试文件：`tests/unit/commerce/test_order_commands.py`、`tests/unit/commerce/test_order_operations.py`、`tests/integration/postgres/test_orders.py`

## RED

- 领域命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_commands.py -q`，退出码 `1`；首轮四个预览 handler 均运行到明确的 `NotImplementedError`。
- 执行 handler：移除尚未由测试驱动的创建/修改实现后补测试，结果 `3 failed, 4 passed`；失败来自批准后 handler 尚未实现。
- 规则补强：联系方式预览缺少脱敏前后值、重复取消丢失首次 `refund_status=pending`，结果 `2 failed, 7 passed`。
- 原子端口：批准路径仍调用“领域写入后再更新 operation”的旧序列，专用测试失败于未使用 `execute_atomic`。
- PostgreSQL 行为：安全夹具成功创建隔离资源后，正常提交和失败回滚测试均失败于 adapter 尚未实现构造与事务行为。早先的模块导入失败不计作有效 RED。

## GREEN

- 最小领域实现：服务端商品价格快照、状态校验、乐观版本检查、首次取消退款状态持久化、唯一退货申请语义，以及只承诺“申请已提交”的结果。
- 最小事务实现：`PostgresCommerceStore` 在同一连接事务中加锁读取 operation，把事务绑定 repository 传给 handler，随后更新 operation 并写审计；任一步抛错由 psycopg 连接上下文回滚。
- 审计关联：operation 哈希绑定可信 `request_id`，审计保存 customer、thread、request、interrupt 和工具标识。
- 单元命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_state_machine.py tests/unit/commerce/test_order_access.py tests/unit/commerce/test_order_operations.py tests/unit/commerce/test_order_commands.py -q`，`36 passed`（后续补强后纳入全量快速测试）。
- PostgreSQL 命令：`UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres tests/integration/postgres/test_orders.py -q`，`3 passed`。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，`110 passed, 3 deselected`。

## REFACTOR

- 将 operation 端口方法明确命名为 `save_operation`、`get_operation`、`update_operation`，避免与订单 repository 的 `get/save` 冲突。
- PostgreSQL adapter 集中在一个 `commerce/postgres.py`；未引入 ORM、连接池、通用 Unit of Work 或额外 repository 文件。
- PostgreSQL 夹具并入根 `tests/conftest.py`，避免子级同名模块遮蔽测试边界；DSN 的失败展示固定脱敏。

## 资源与边界

- 每次集成测试创建 `wang_agent_orders_test_db_<run_id>` database 和 `wang_agent_orders_test_role_<run_id>` owner，结束后精确删除；未修改已有业务 database。
- 已验证：创建、修改、取消、退货的真实 SQL；重复批准结果复用；审计故障时订单、operation 和审计全部回滚；客户作用域读取。
- 未验证：API 工具装配、跨进程崩溃恢复、连接池、生产迁移工具和生产并发容量；这些不属于 Task 4 的当前完成声明。
- 安全说明：首轮集成 RED 的 pytest fixture 表示曾显示一次临时 DSN；对应 database/role 已在该次测试结束时删除，随后加入固定脱敏表示。

