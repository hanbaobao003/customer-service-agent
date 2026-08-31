# 订单与受控 SQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现带客户隔离、状态机、预览审批、幂等执行和审计事务的模拟订单生命周期，以及只读受控 NL2SQL。

**Architecture:** 目录遵循 `docs/architecture/code-layout.md`，Spec 030 集中在 `commerce/`：`orders.py` 保存订单领域行为，`sql.py` 保存只读安全行为，`postgres.py` 保存共享数据库访问。写工具先生成不可变 operation 预览和规范化哈希，批准后用同一 operation 在 PostgreSQL 事务中执行；SQL 使用 AST 白名单、可信客户参数、只读角色、LIMIT 和超时。

**Tech Stack:** Python 3.13.15、PostgreSQL 17、Pydantic v2、psycopg3、SQLGlot、pytest、pytest-asyncio。

**Spec:** `docs/specs/030-orders-and-sql.md`

## Global Constraints

- 所有订单 repository 方法显式接收可信 `customer_id`。
- 不存在与不属于客户统一返回 `ORDER_NOT_FOUND`。
- 所有写操作先预览再 approve/reject；禁止物理删除。
- 状态变更、operation 结果和审计必须位于同一 PostgreSQL 事务。
- 写工具不使用通用自动重试；提交不明先查 operation 结果。
- SQL 只允许单条 SELECT，使用 AST 校验、客户作用域视图、LIMIT、超时和只读事务。
- 最大行数与超时受 DG-006/相关用户批准约束；未批准时真实 SQL 执行端口禁用。

## File Structure

```text
src/customer_service_agent/commerce/orders.py   # DTO、状态机、预览、幂等、订单工具
src/customer_service_agent/commerce/sql.py      # SQL DTO、AST、安全服务和工具
src/customer_service_agent/commerce/postgres.py # repository、事务、审计、只读执行
tests/unit/commerce/
tests/contract/test_order_tools.py
tests/contract/test_sql_tool.py
tests/integration/postgres/test_orders.py
tests/integration/postgres/test_sql_readonly.py
```

### Task 1: 订单模型与显式状态机

**Files:**
- Create: `src/customer_service_agent/commerce/orders.py`
- Test: `tests/unit/commerce/test_order_state_machine.py`

**Interfaces:**
- Produces: `OrderStatus`、`Order`、`OrderItem`、`ReturnRequest`。
- Produces: `transition(order, target) -> Order`，非法转换抛出 `BusinessRuleRejected`。

- [ ] **Step 1: 写允许/禁止转换 RED**

  ```python
  def test_shipped_order_cannot_be_cancelled():
      order = make_order(status=OrderStatus.SHIPPED, version=4)
      with pytest.raises(BusinessRuleRejected) as exc:
          transition(order, OrderStatus.CANCELLED)
      assert exc.value.code == "BUSINESS_RULE_REJECTED"
      assert order.version == 4
  ```

  参数化覆盖 `pending_payment -> paid -> processing -> shipped -> delivered`、发货前取消和 delivered 退货申请。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_state_machine.py -q`

  Expected: 转换矩阵尚未实现导致目标断言失败。

- [ ] **Step 3: 实现不可变模型和转换矩阵**

  ```python
  ALLOWED_TRANSITIONS = {
      OrderStatus.PENDING_PAYMENT: {OrderStatus.PAID, OrderStatus.CANCELLED},
      OrderStatus.PAID: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
      OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
      OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
      OrderStatus.DELIVERED: {OrderStatus.RETURN_REQUESTED},
  }
  ```

  每次成功转换版本加一；失败不修改原对象。

- [ ] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_state_machine.py -q`

- [ ] **Step 5: 提交状态机**

  Commit: `feat: define explicit order lifecycle`

### Task 2: 客户隔离 repository 与读取工具

**Files:**
- Modify: `src/customer_service_agent/commerce/orders.py`
- Test: `tests/unit/commerce/test_order_access.py`
- Test: `tests/contract/test_order_tools.py`

**Interfaces:**
- Produces: `OrderRepository.get(order_id, customer_id)`。
- Produces: `OrderService.get_order(context, order_id)`。
- Produces: 模型工具 `get_order(order_id)`，schema 中没有 `customer_id`。

- [ ] **Step 1: 写越权不可观察和 schema RED**

  ```python
  async def test_other_customer_order_is_indistinguishable_from_missing():
      repo = InMemoryOrderRepository([make_order(id="o-1", customer_id="a")])
      service = OrderService(repo=repo, clock=FixedClock())
      with pytest.raises(OrderNotFound):
          await service.get_order(ctx(customer_id="b"), "o-1")
  ```

  契约断言 `get_order` 模型参数只有 `order_id`，返回摘要带状态、版本和查询时间，artifact 不含完整地址/电话。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_access.py tests/contract/test_order_tools.py -q`

- [ ] **Step 3: 实现客户作用域端口和摘要映射**

  repository 协议不提供无客户参数的 `get_by_id`。工具从 `ToolRuntime.context` 取 `customer_id`，把完整脱敏实体放 artifact，模型 content 只放客服摘要。

- [ ] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_access.py tests/contract/test_order_tools.py -q`

- [ ] **Step 5: 提交订单读取边界**

  Commit: `feat: isolate order reads by trusted customer`

### Task 3: Operation 预览、哈希、审批和幂等

**Files:**
- Modify: `src/customer_service_agent/commerce/orders.py`
- Test: `tests/unit/commerce/test_order_operations.py`

**Interfaces:**
- Produces: `OperationPreview(operation_id, tool_name, normalized_args, args_hash, expected_version, status)`。
- Produces: `preview_operation(...)` 与 `execute_approved(operation_id, decision)`。

- [ ] **Step 1: 写参数篡改、重复批准和拒绝 RED**

  ```python
  async def test_approval_rejects_changed_normalized_arguments():
      operation = await service.preview_cancel(ctx(), order_id="o-1", reason="不需要")
      store.tamper(operation.id, normalized_args={"order_id": "o-2"})
      with pytest.raises(OperationHashMismatch):
          await service.approve(ctx(), operation.id)
  ```

  重复批准返回首次 `result_artifact`，审计和领域写入计数保持一次；reject 后不能批准。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_operations.py -q`

- [ ] **Step 3: 实现规范化哈希和 operation 状态机**

  `operation_id` 由 `IdGenerator` 创建；哈希输入为固定键顺序 JSON、工具名、客户、线程和 expected version。状态只允许 `pending -> approved/executed` 或 `pending -> rejected/expired`。

- [ ] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_operations.py -q`

- [ ] **Step 5: 提交审批与幂等核心**

  Commit: `feat: add immutable order operation approvals`

### Task 4: 创建、修改、取消和退货服务

**Files:**
- Modify: `src/customer_service_agent/commerce/orders.py`
- Create: `src/customer_service_agent/commerce/postgres.py`
- Test: `tests/unit/commerce/test_order_commands.py`
- Test: `tests/integration/postgres/test_orders.py`

**Interfaces:**
- Produces: `create_order`、`update_order_contact`、`cancel_order`、`request_return` 预览。
- Consumes: Task 3 的统一批准执行器。

- [ ] **Step 1: 为四种写操作分别写最小 RED**

  核心断言：创建在批准前无订单；发货后不能修改；已付款取消只标记 `refund_status=pending`；只有 delivered 可创建唯一退货申请且只承诺“申请已提交”。

  ```python
  async def test_create_preview_does_not_insert_order():
      preview = await service.preview_create(ctx(), valid_create_request())
      assert preview.tool_name == "create_order"
      assert repo.orders == []
  ```

- [ ] **Step 2: 运行四组 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_commands.py -q`

- [ ] **Step 3: 每次只实现当前测试所需的领域 handler**

  handler 在批准执行时重新加载订单并比较 `expected_version`；版本不符返回 `ORDER_VERSION_CONFLICT`，要求重新预览，不自动合并。

- [ ] **Step 4: 运行 GREEN 与 PostgreSQL 原子性集成**

  Unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_state_machine.py tests/unit/commerce/test_order_access.py tests/unit/commerce/test_order_operations.py tests/unit/commerce/test_order_commands.py -q`

  Integration: `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres tests/integration/postgres/test_orders.py -q`

  集成测试故意在审计写入处注入事务失败，断言订单状态、operation 结果和审计全部回滚。

- [ ] **Step 5: 提交完整订单生命周期**

  Commit: `feat: implement approved order operations`

### Task 5: AST 白名单与只读 NL2SQL

**Files:**
- Create: `src/customer_service_agent/commerce/sql.py`
- Modify: `src/customer_service_agent/commerce/postgres.py`
- Test: `tests/unit/commerce/test_sql_guard.py`
- Test: `tests/contract/test_sql_tool.py`
- Test: `tests/integration/postgres/test_sql_readonly.py`

**Interfaces:**
- Produces: `SqlGuard.validate(sql, allowed_relations) -> ValidatedQuery`。
- Produces: `SqlQueryService.execute(context, proposed_sql) -> SqlResult`。

- [ ] **Step 1: 写危险 SQL 和客户参数 RED**

  ```python
  @pytest.mark.parametrize("sql", [
      "DELETE FROM customer_orders",
      "SELECT * FROM customer_orders; SELECT 1",
      "SELECT * FROM customer_orders FOR UPDATE",
      "COPY customer_orders TO STDOUT",
  ])
  def test_guard_rejects_non_readonly_sql(sql):
      with pytest.raises(SqlValidationError):
          SqlGuard(allowed_relations={"customer_orders"}).validate(sql)
  ```

  另测模型 SQL 中出现客户字面量不能覆盖可信参数，未知表/函数/系统目录被拒绝。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_sql_guard.py tests/contract/test_sql_tool.py -q`

- [ ] **Step 3: 实现 AST 流水线和参数化执行**

  流水线固定为：解析单 statement → 根节点 SELECT → 禁止节点扫描 → relation/function 白名单 → 客户视图要求 → 应用层 LIMIT 上限 → 参数化可信客户 → 只读事务与 statement timeout。解析失败返回稳定错误，不静默重写再执行。

- [ ] **Step 4: 运行 GREEN 与只读角色集成**

  Unit/contract: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_sql_guard.py tests/contract/test_sql_tool.py -q`

  Integration: `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres tests/integration/postgres/test_sql_readonly.py -q`

  集成断言只读角色无法写表、客户视图只返回当前客户、LIMIT 和 timeout 生效。

- [ ] **Step 5: 提交受控 SQL**

  Commit: `feat: add AST-guarded readonly business queries`

## Requirement Coverage

| Spec requirements | Plan task |
|---|---|
| ORD-STATE-001…004 | Tasks 1、4 状态机、事务和乐观锁 |
| ORD-READ-001…003 | Task 2 客户作用域读取和摘要/artifact |
| ORD-CREATE-001…003 | Task 4 创建预览与批准执行 |
| ORD-UPDATE-001…003 | Task 4 可修改状态、旧/新摘要和版本校验 |
| ORD-CANCEL-001…003 | Task 4 发货前取消、退款语义和重复结果 |
| ORD-RETURN-001…003 | Task 4 delivered 限制、唯一申请和承诺边界 |
| ORD-HITL-001…004 | Task 3 哈希、绑定、决策和过期 |
| ORD-IDEM-001…004 | Tasks 3、4 operation 唯一性和提交不明恢复 |
| ORD-AUTH-001…004 | Task 2 repository、schema 和不可观察性 |
| SQL-001…008 | Task 5 AST、白名单、客户视图、只读执行和 artifact |
| ORD/SQL-SCN | Tasks 2…5 的单元与 PostgreSQL 集成验收场景 |

## Plan Verification

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce tests/contract/test_order_tools.py tests/contract/test_sql_tool.py -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres -q
git diff --check
```

发布门禁要求安全、状态机、客户隔离和幂等断言 100% 通过。
