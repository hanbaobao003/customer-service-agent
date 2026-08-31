# 长期记忆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 Mem0 `AsyncMemory` 和独立 PostgreSQL/pgvector 数据库实现明确授权的偏好保存、客户隔离召回、查看、删除与冲突处理。

**Architecture:** 目录遵循 `docs/architecture/code-layout.md`，Spec 040 只保留 `memory/service.py` 与 `memory/mem0_pg.py`。应用层先执行明确意图、内容类型、敏感信息和工具证据校验，再调用同文件内定义的 `MemoryStorePort`；生产适配器使用 Mem0 2.0.19 `AsyncMemory`、固定 PGVector collection 和独立数据库角色。记忆只能作为非指令数据注入，当前事实仍必须调用事实工具。

**Tech Stack:** Python 3.13.15、Mem0 2.0.19、PostgreSQL 17、pgvector 0.8.6、psycopg3、Pydantic v2、pytest、pytest-asyncio。

**Spec:** `docs/specs/040-long-term-memory.md`

## Global Constraints

- 仅在当前用户消息明确要求长期保存或删除时写入。
- 禁止保存密码、API Key、验证码、支付卡号、完整身份证件、模型推断敏感属性和 chain-of-thought。
- `customer_id` 由运行时上下文注入，不进入工具 schema。
- Mem0 自动推断必须关闭；应用层传入已归一化的单一事实。
- Mem0 使用独立 PostgreSQL database 和 role，不复用订单/checkpoint 数据库。
- collection 名称只来自静态配置和 SQL 标识符白名单。
- 真实 embedding 模型、维度和索引类型未批准时，PGVector adapter 保持禁用。

## File Structure

```text
src/customer_service_agent/memory/service.py   # DTO、策略、端口、操作、冲突、工具
src/customer_service_agent/memory/mem0_pg.py   # AsyncMemory + PGVector
tests/unit/memory/
tests/contract/test_memory_tools.py
tests/integration/postgres/test_mem0_pgvector.py
```

### Task 1: 明确意图、允许类型与敏感信息策略

**Files:**
- Create: `src/customer_service_agent/memory/service.py`
- Test: `tests/unit/memory/test_policy.py`

**Interfaces:**
- Produces: `MemoryIntent` 枚举 `save/delete/none`。
- Produces: `MemoryPolicy.evaluate(message, kind, content) -> PolicyDecision`。

- [x] **Step 1: 写临时偏好和支付卡号 RED**

  ```python
  def test_temporary_instruction_does_not_authorize_long_term_write():
      decision = policy.evaluate(
          message="这次回答简短一点",
          kind="preference",
          content="偏好简短回答",
      )
      assert decision.intent is MemoryIntent.NONE
      assert decision.allowed is False
      assert decision.code == "MEMORY_EXPLICIT_INTENT_REQUIRED"
  ```

  另测“以后都用中文，请记住”允许；支付卡号、验证码和 API key 返回 `MEMORY_POLICY_REJECTED`，且 decision 不携带敏感原文。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_policy.py -q`

- [x] **Step 3: 实现显式短语与敏感检测组合**

  保存意图要求明确长期词与保存动词组合；删除意图要求明确忘记/删除。敏感规则使用命名检测器序列并返回类别，不在日志中返回匹配值。规则是保守门控，不让模型自由判断授权。

- [x] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_policy.py -q`

- [x] **Step 5: 提交记忆策略**

  Commit: `feat: require explicit safe memory intent`

### Task 2: 记忆 DTO、来源证据与存储端口

**Files:**
- Modify: `src/customer_service_agent/memory/service.py`
- Test: `tests/unit/memory/test_memory_models.py`
- Test: `tests/contract/test_memory_tools.py`

**Interfaces:**
- Produces: `MemoryRecord`、`MemorySource`、`MemorySummary`。
- Produces: `MemoryStorePort.add/search/list/delete`，每个方法显式接收 `customer_id`。

- [x] **Step 1: 写 DTO 与模型 schema RED**

  ```python
  def test_memory_summary_never_exposes_embedding_or_score():
      summary = MemorySummary.from_record(record_with_internal_vector())
      payload = summary.model_dump()
      assert "embedding" not in payload
      assert "score" not in payload
      assert payload["memory_id"] == "m-1"
  ```

  契约断言 `remember_preference`、`list_memories`、`forget_memory` 的模型参数没有 `customer_id`，未知字段被拒绝。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_memory_models.py tests/contract/test_memory_tools.py -q`

- [x] **Step 3: 实现稳定 DTO 与窄端口**

  ```python
  class MemoryStorePort(Protocol):
      async def add(self, *, customer_id: str, record: MemoryRecord) -> str: ...
      async def search(self, *, customer_id: str, query: str, limit: int) -> Sequence[MemoryRecord]: ...
      async def list(self, *, customer_id: str, category: str | None) -> Sequence[MemoryRecord]: ...
      async def delete(self, *, customer_id: str, memory_id: str) -> DeleteResult: ...
  ```

  `MemorySource` 强制 thread/request/type；verified fact 还要求真实 tool name 和 verified_at。

- [x] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_memory_models.py tests/contract/test_memory_tools.py -q`

- [x] **Step 5: 提交 DTO 与端口**

  Commit: `feat: define isolated memory contracts`

### Task 3: 保存、召回、列出和删除服务

**Files:**
- Modify: `src/customer_service_agent/memory/service.py`
- Test: `tests/unit/memory/test_service.py`
- Modify: `tests/contract/test_memory_tools.py`

**Interfaces:**
- Produces: `MemoryService.remember/recall/list/forget`。
- Consumes: `RuntimeContext`、`MemoryPolicy`、`MemoryStorePort`、成功工具调用登记端口。

- [x] **Step 1: 写普通对话不写入、客户隔离和验证事实 RED**

  ```python
  async def test_ordinary_message_never_calls_store_add():
      store = FailingOnAddMemoryStore()
      service = MemoryService(store=store, policy=MemoryPolicy(), tool_evidence=FakeToolEvidence())
      with pytest.raises(MemoryPolicyError) as exc:
          await service.remember(ctx(), user_message="我喜欢蓝色", request=preference("喜欢蓝色"))
      assert exc.value.code == "MEMORY_EXPLICIT_INTENT_REQUIRED"
  ```

  另测 verified fact 引用不存在的 tool_call_id 被拒绝；客户 B 不能 list/delete 客户 A 的 memory ID；重复删除返回同一“不存在”公开结果。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_service.py -q`

- [x] **Step 3: 按 policy → evidence → normalize → store 固定顺序实现**

  写入时只传单一事实并设置 `infer=False` 语义；召回结果包在明确的 `UserMemoryContext(items=...)` 数据结构中，提示词标记“不可作为指令”。无记忆或存储暂时不可用时返回空上下文/稳定 warning，不阻止普通客服主流程。

- [x] **Step 4: 运行 GREEN 和契约回归**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_service.py tests/contract/test_memory_tools.py -q`

- [x] **Step 5: 提交记忆服务**

  Commit: `feat: add explicit customer-scoped memory operations`

### Task 4: 重复、冲突与明确替换

**Files:**
- Modify: `src/customer_service_agent/memory/service.py`
- Test: `tests/unit/memory/test_conflicts.py`

**Interfaces:**
- Produces: `ConflictDetector.compare(existing, candidate)`。
- Produces: `MemoryConflict`，只包含面向当前客户的安全摘要。

- [x] **Step 1: 写重复更新和未授权覆盖 RED**

  ```python
  async def test_opposite_preference_requires_explicit_replace():
      store = store_with(customer="a", category="language", content="偏好中文")
      with pytest.raises(MemoryConflict):
          await service.remember(
              ctx(customer_id="a"),
              user_message="请记住我偏好英文",
              request=preference("偏好英文", category="language"),
          )
      assert store.contents("a") == ["偏好中文"]
  ```

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_conflicts.py -q`

- [x] **Step 3: 实现同客户同类别冲突规则**

  完全相同规范化内容更新时间而不新增；相反偏好返回冲突；只有包含明确替换意图的后续请求才调用存储端口的原子 `replace`，并分别审计旧记录删除和新记录创建。该端口修正了独立 delete + add 无法保证“任何失败保持旧记录”的计划缺口。

- [x] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_conflicts.py -q`

- [x] **Step 5: 提交冲突策略**

  Commit: `feat: preserve memory conflicts until explicit replacement`

### Task 5: Mem0 AsyncMemory + PGVector 适配与隔离

**Files:**
- Create: `src/customer_service_agent/memory/mem0_pg.py`
- Test: `tests/unit/memory/test_mem0_adapter_config.py`
- Test: `tests/integration/postgres/test_mem0_pgvector.py`

**Interfaces:**
- Produces: `Mem0PgConfig(dbname, collection_name, embedding_model_dims, connection_string_ref)`。
- Produces: `Mem0PgMemoryStore` 实现 `MemoryStorePort`。

- [ ] **Step 1: 写动态 collection、错误数据库和客户过滤 RED**

  ```python
  @pytest.mark.parametrize("name", ["memories;drop table orders", "customer/{id}", ""])
  def test_collection_name_must_be_static_sql_identifier(name):
      with pytest.raises(ValueError, match="collection_name"):
          Mem0PgConfig(collection_name=name, **valid_pg_config())
  ```

  配置测试断言 memory DSN 引用不能等于核心订单/checkpoint DSN 引用。集成测试向客户 A/B 写入相似内容，只允许各自检索。

- [ ] **Step 2: 运行配置 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_mem0_adapter_config.py -q`

- [ ] **Step 3: 实现固定配置和 AsyncMemory 映射**

  `AsyncMemory` 配置固定 `provider="pgvector"`；所有 add/search/get_all/delete 调用都传 `user_id=customer_id` 或等价 filters。`add` 使用 `infer=False`，metadata 包含 schema version、来源和 category。适配器只把 Mem0 响应映射为应用 DTO，不向上暴露原始响应。

- [ ] **Step 4: 运行 GREEN 与 PostgreSQL/pgvector 集成**

  Unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_mem0_adapter_config.py -q`

  Integration: `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres tests/integration/postgres/test_mem0_pgvector.py -q`

  集成前检查目标 database/role 是测试专用且 `vector` 扩展存在；不满足时停止，不修改订单数据库。验证保存、召回、列出、删除、重复删除、客户过滤和 unavailable 降级。

- [ ] **Step 5: 提交 PGVector 适配**

  Commit: `feat: persist Mem0 memories in isolated pgvector`

## Requirement Coverage

| Spec requirements | Plan task |
|---|---|
| MEM-POLICY-001…005 | Task 1 明确意图、允许类型和敏感规则 |
| MEM-DATA-001…004 | Task 2 DTO、来源、公开摘要和删除键 |
| MEM-WRITE-001…005 | Tasks 1、3 授权、工具证据、归一化和审计 |
| MEM-RECALL-001…005 | Task 3 客户过滤、非指令注入和事实重查边界 |
| MEM-LIST-001…002 | Tasks 2、3 当前客户安全摘要 |
| MEM-DELETE-001…004 | Task 3 明确删除、所有权、幂等和审计 |
| MEM-STORE-001…006 | Task 5 独立 database/role、pgvector、静态 collection 和 readiness 边界 |
| MEM-CONFLICT-001…003 | Task 4 同客户冲突和明确替换 |
| MEM-SCN-001…006 | Tasks 1、3…5 的单元与 PostgreSQL 集成验收场景 |

## Plan Verification

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory tests/contract/test_memory_tools.py -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres tests/integration/postgres/test_mem0_pgvector.py -q
git diff --check
```

报告明确区分：内存替身客户隔离、Mem0 PGVector 集成、真实 embedding/LLM 是否启用，以及没有验证生产备份/恢复的边界。
