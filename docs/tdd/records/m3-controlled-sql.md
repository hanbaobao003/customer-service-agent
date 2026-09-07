# M3 受控 SQL RED–GREEN 记录

- Spec：`docs/specs/030-orders-and-sql.md`
- 需求 ID：`SQL-001…008`、`ORD-AUTH-003`
- 公开行为：只允许经过 AST 白名单的单条 SELECT；可信客户参数由服务注入；结果受 50 行和 2000ms 上限约束；PostgreSQL 使用最低权限 role 和只读事务。
- 测试文件：`tests/unit/commerce/test_sql_guard.py`、`tests/contract/test_sql_tool.py`、`tests/integration/postgres/test_sql_readonly.py`

## RED

- AST 首轮：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_sql_guard.py -q`，`12 failed`；全部失败于尚未实现的 `validate()`。
- 工具契约：与 AST 测试合跑时 `3 failed, 12 passed`；服务尚未执行生成、校验、可信客户注入和 artifact 分离。
- 安全补强：分别取得“限定列错误归属未拒绝”“星号投影未拒绝”的单测试 RED。
- 结果上限：契约测试 `2 failed, 2 passed`；底层 reader 超额返回时服务未二次截断，也没有 `truncated` 标记。
- PostgreSQL：安全资源创建成功后 `3 failed`；缺少五个白名单视图和只读 reader。
- 只读事务变异：临时移除 `set_read_only(True)` 后，owner 凭据成功执行 DELETE，目标测试以非查询结果错误失败；证明该测试能够捕获只读事务设置缺失。

## GREEN

- SQLGlot 29.0.1 解析 PostgreSQL AST；固定流水线校验 statement、根节点、锁、星号、relation、column、function、CTE 和 LIMIT。
- 客户作用域 predicate 通过 AST 加到每个相关 scope，使用 psycopg 命名参数，不接受模型提供的 `customer_id`。
- `create_business_sql_guard()` 固定五个白名单视图、列和聚合函数；批准参数位于 `config.py`：50 行、2000ms。
- `PostgresSqlReader` 使用独立 reader DSN、只读事务和事务内 `statement_timeout`，超时映射为 `SQL_TIMEOUT`，Decimal/时间等结果规范化为 JSON 安全值。
- Unit/contract：`19 passed`；全量快速测试：`129 passed, 6 deselected`。
- PostgreSQL：订单与 SQL 全组 `7 passed`，覆盖客户过滤、50 行上限、role 权限、owner 凭据只读事务、超时和既有订单事务回归。

## REFACTOR

- 继续遵循 Spec 目录：AST、DTO、服务和工具集中在 `commerce/sql.py`；PostgreSQL 视图和 reader 留在共享的 `commerce/postgres.py`，未新增 repository 层或 ORM。
- SQL 测试资源使用 `wang_agent_sql_test_` 安全前缀，database owner 与 reader role 分离；DSN 输出固定脱敏。

## 边界

- 已验证：确定性 SQL 输入到真实 PostgreSQL 的完整安全路径。
- 未验证：真实 DeepSeek NL2SQL 生成、模型修复重试、生产迁移与并发容量；真实模型仍受 DG-002 和显式 opt-in 约束。
- 公共商品/促销底表和视图已建立，但当前只由测试或后续模拟数据导入填充。

