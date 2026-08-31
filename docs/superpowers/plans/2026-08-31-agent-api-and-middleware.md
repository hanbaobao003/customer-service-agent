# Agent、API 与中间件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立单 LangChain 客服 Agent 的可信上下文、稳定 API/SSE 契约、异步中间件、HITL 恢复和 PostgreSQL 检查点基础。

**Architecture:** 目录遵循 `docs/architecture/code-layout.md`，Spec 010 的生产行为集中在 `agent_api/`。应用层以 `CustomerService` 为唯一入口，FastAPI 与 CLI 只做适配；可信身份通过 `RuntimeContext` 注入，单元测试使用内存实现，生产检查点使用 PostgreSQL。SSE 由 `agent_api/api.py` 统一编码，领域工具不依赖 LangGraph stream writer。

**Tech Stack:** Python 3.13.15、LangChain 1.3.18、LangGraph 1.2.11、FastAPI、Pydantic v2、psycopg3、langgraph-checkpoint-postgres、pytest、pytest-asyncio、uv。

**Spec:** `docs/specs/010-agent-api-and-middleware.md`

## Global Constraints

- 只创建一个名为 `customer_service_agent` 的 `create_agent` 实例。
- `customer_id`、`thread_id`、`request_id` 只来自可信适配器，不进入模型工具 schema。
- 异步工具中间件必须实现 `awrap_tool_call` 并等待异步 handler。
- 写工具不进入通用重试；HITL 使用 PostgreSQL `AsyncPostgresSaver`。
- 模型 ID 受 DG-002 约束；未批准时只使用 scripted model 完成单元和契约测试。
- 遵循 `docs/tdd/000-tdd-strategy.md`，每个任务保存有效 RED 证据。

## File Structure

```text
pyproject.toml                                      # Python、依赖、pytest/ruff 配置
src/customer_service_agent/config.py               # 配置 schema 与决策门
src/customer_service_agent/cli.py                  # CLI 适配器
src/customer_service_agent/shared/models.py        # RuntimeContext 与应用事件
src/customer_service_agent/shared/errors.py        # 稳定错误码和公开 envelope
src/customer_service_agent/agent_api/service.py    # prompt、Agent 工厂、CustomerService、checkpointer
src/customer_service_agent/agent_api/middleware.py # 调用上限、重试、HITL、摘要
src/customer_service_agent/agent_api/api.py        # FastAPI、schema、SSE、decision、健康检查
tests/unit/agent_api/                               # 上下文、事件、中间件、服务测试
tests/contract/                                     # API、SSE、工具 schema 契约
tests/integration/postgres/                         # 检查点暂停/恢复与绑定测试
```

### Task 1: 项目测试入口与可信运行时上下文

**Files:**
- Create: `pyproject.toml`
- Create: `src/customer_service_agent/config.py`
- Create: `src/customer_service_agent/shared/models.py`
- Create: `src/customer_service_agent/shared/errors.py`
- Test: `tests/unit/agent_api/test_context.py`

**Interfaces:**
- Produces: `RuntimeContext(customer_id, thread_id, request_id, locale, channel)`。
- Produces: `ServiceError(code, message, retryable, request_id)`；公开序列化不得包含内部异常。

- [x] **Step 1: 建立测试配置并写第一个失败测试**

  `pyproject.toml` 固定 Python `==3.13.*`、已批准的 LangChain/LangGraph 版本和 pytest markers；`.gitignore` 忽略 `.venv/`、`.uv-cache/`、缓存和测试报告，不忽略 Spec、计划或测试数据。测试表达希望的可信构造接口：

  ```python
  def test_runtime_context_rejects_blank_customer_id():
      with pytest.raises(ValueError, match="customer_id"):
          RuntimeContext.trusted(
              customer_id=" ", thread_id="thread-1", request_id="req-1"
          )
  ```

- [x] **Step 2: 运行 RED 并确认失败原因**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_context.py -q`

  Expected: pytest 成功收集测试，断言因 `RuntimeContext.trusted` 尚未实现或尚未拒绝空身份而失败；依赖安装或导入环境错误必须先修复再重跑 RED。

- [x] **Step 3: 写最小上下文与错误类型**

  ```python
  @dataclass(frozen=True, slots=True)
  class RuntimeContext:
      customer_id: str
      thread_id: str
      request_id: str
      locale: str = "zh-CN"
      channel: Literal["api", "cli"] = "api"

      @classmethod
      def trusted(cls, *, customer_id: str, thread_id: str, request_id: str,
                  locale: str = "zh-CN", channel: Literal["api", "cli"] = "api") -> "RuntimeContext":
          values = {"customer_id": customer_id, "thread_id": thread_id, "request_id": request_id}
          for name, value in values.items():
              if not value.strip():
                  raise ValueError(f"{name} must not be blank")
          return cls(customer_id.strip(), thread_id.strip(), request_id.strip(), locale, channel)
  ```

  `ServiceError` 使用受控 `code` 和用户消息，内部 `cause` 只能用于日志/Trace，不进入 `to_public_dict()`。

- [x] **Step 4: 运行 GREEN 与上下文回归**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_context.py -q`

  Expected: 空客户、空线程、空请求 ID 分别被拒绝；有效 API/CLI 上下文保持不可变。

- [x] **Step 5: 提交基础测试入口**

  Commit: `test: establish trusted runtime context`

### Task 2: 线程绑定、单运行锁与应用服务

**Files:**
- Create: `src/customer_service_agent/agent_api/service.py`
- Test: `tests/unit/agent_api/test_thread_access.py`
- Test: `tests/unit/agent_api/test_customer_service.py`

**Interfaces:**
- Consumes: `RuntimeContext`、`ServiceError`。
- Produces: `ThreadBindingRepository.bind_or_validate(thread_id, customer_id)`。
- Produces: `RunLock.acquire(thread_id)` 异步上下文管理器。
- Produces: `CustomerService.stream_message(context, message) -> AsyncIterator[AppEvent]`。

- [x] **Step 1: 写客户不匹配和并发运行 RED**

  ```python
  async def test_bound_thread_rejects_other_customer_before_agent_call():
      bindings = InMemoryThreadBindings({"t-1": "customer-a"})
      agent = FailingIfCalledAgent()
      service = CustomerService(bindings=bindings, run_lock=InMemoryRunLock(), agent=agent)
      with pytest.raises(ServiceError) as exc:
          [event async for event in service.stream_message(ctx("customer-b", "t-1"), "查询订单")]
      assert exc.value.code == "THREAD_CUSTOMER_MISMATCH"
      assert agent.called is False
  ```

  第二个测试先占用 `t-1`，再次调用必须得到 `THREAD_BUSY`，不同线程仍可运行。

- [x] **Step 2: 验证两个 RED 都由行为缺失造成**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_thread_access.py tests/unit/agent_api/test_customer_service.py -q`

  Expected: 客户绑定或运行锁断言失败，不是 asyncio fixture 错误。

- [x] **Step 3: 实现端口和最小内存替身语义**

  ```python
  class ThreadBindingRepository(Protocol):
      async def bind_or_validate(self, *, thread_id: str, customer_id: str) -> None: ...

  class RunLock(Protocol):
      def acquire(self, thread_id: str) -> AsyncContextManager[None]: ...

  class AgentRunner(Protocol):
      def stream(self, *, context: RuntimeContext, message: str) -> AsyncIterator["AppEvent"]: ...
  ```

  `CustomerService` 的顺序固定为：校验消息 → bind/validate → acquire → agent stream。客户端取消只释放锁，不产生批准或拒绝事件。

- [x] **Step 4: 运行 GREEN 和取消回归**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_thread_access.py tests/unit/agent_api/test_customer_service.py -q`

  Expected: 客户不匹配、同线程忙、不同线程并行和取消释放锁均通过。

- [x] **Step 5: 提交应用服务边界**

  Commit: `feat: enforce thread ownership and single active run`

### Task 3: 应用事件与 SSE 契约

**Files:**
- Modify: `src/customer_service_agent/shared/models.py`
- Create: `src/customer_service_agent/agent_api/api.py`
- Test: `tests/unit/agent_api/test_events.py`
- Test: `tests/contract/test_sse_contract.py`

**Interfaces:**
- Produces: `EventType` 固定八种事件。
- Produces: `EventSequencer.emit(type, data) -> AppEvent`。
- Produces: `encode_sse(event) -> bytes`。

- [x] **Step 1: 写序号、终止事件和脱敏 RED**

  ```python
  def test_event_sequence_starts_at_one_and_increments():
      sequencer = EventSequencer(thread_id="t-1", request_id="r-1")
      assert sequencer.emit(EventType.MESSAGE_DELTA, {"text": "你"}).sequence == 1
      assert sequencer.emit(EventType.MESSAGE_COMPLETED, {}).sequence == 2
  ```

  契约测试遍历序列化 JSON，断言不存在 `customer_id`、`api_key`、`prompt`、`chain_of_thought` 字段，并验证终止事件之后拒绝继续 emit。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_events.py tests/contract/test_sse_contract.py -q`

  Expected: 序号/终止或脱敏断言失败。

- [x] **Step 3: 实现固定事件枚举和编码器**

  ```python
  class EventType(StrEnum):
      MESSAGE_DELTA = "message.delta"
      TOOL_STARTED = "tool.started"
      TOOL_COMPLETED = "tool.completed"
      CITATION = "citation"
      APPROVAL_REQUIRED = "approval.required"
      HANDOFF_REQUIRED = "handoff.required"
      ERROR = "error"
      MESSAGE_COMPLETED = "message.completed"
  ```

  `encode_sse` 按 Spec 010 输出单调序号 `id:`、`event:` 与单行 JSON `data:`；Pydantic 模型设置 `extra="forbid"`。终止状态由 sequencer 管理。

- [x] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_events.py tests/contract/test_sse_contract.py -q`

  Expected: 事件类型、严格递增、终止语义和敏感字段拒绝全部通过。

- [x] **Step 5: 提交 SSE 契约**

  Commit: `feat: define stable customer service event stream`

### Task 4: FastAPI、CLI、确认恢复与健康检查

**Files:**
- Modify: `src/customer_service_agent/agent_api/api.py`
- Create: `src/customer_service_agent/cli.py`
- Test: `tests/contract/test_message_api.py`
- Test: `tests/contract/test_decision_api.py`
- Test: `tests/contract/test_health_api.py`

**Interfaces:**
- Consumes: `CustomerService`、`RuntimeContext`、SSE encoder。
- Produces: `create_app(service, health) -> FastAPI`。
- Produces: `DecisionRequest(interrupt_id, decision, reason)`，只允许 approve/reject。

- [ ] **Step 1: 写请求校验、身份来源与 decision RED**

  ```python
  def test_message_api_uses_authenticated_customer_not_body(client, service_spy):
      response = client.post(
          "/v1/threads/t-1/messages:stream",
          headers={"X-Test-Customer": "customer-a"},
          json={"message": "你好"},
      )
      assert response.status_code == 200
      assert service_spy.context.customer_id == "customer-a"
  ```

  另测空消息为 422、`edit` decision 为 422、reject 缺 reason 为 422、readiness 不返回连接串。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/contract/test_message_api.py tests/contract/test_decision_api.py tests/contract/test_health_api.py -q`

  Expected: 路由或 schema 尚不存在导致目标契约失败；先排除应用导入错误。

- [ ] **Step 3: 实现最小 ASGI 与 CLI 适配**

  `create_app` 通过可替换的 `TrustedContextProvider` 从服务端认证上下文取客户 ID；测试 header provider 只在测试工厂注入。CLI 参数 `--customer-id` 经过同一 `RuntimeContext.trusted`，不拼接消息。decision 调用 `CustomerService.resume_decision`，重复决定由服务端稳定返回。

- [ ] **Step 4: 运行 GREEN 和全部契约测试**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest -m contract -q`

  Expected: 消息流、decision、健康和错误 envelope 契约全部通过。

- [ ] **Step 5: 提交入口适配器**

  Commit: `feat: add streaming API and decision contracts`

### Task 5: 异步中间件、Agent 工厂与 PostgreSQL 恢复

**Files:**
- Create: `src/customer_service_agent/agent_api/middleware.py`
- Modify: `src/customer_service_agent/agent_api/service.py`
- Test: `tests/unit/agent_api/test_middleware.py`
- Test: `tests/unit/agent_api/test_handoff.py`
- Test: `tests/contract/test_prompt_contract.py`
- Test: `tests/contract/test_agent_tools.py`
- Test: `tests/integration/postgres/test_checkpoint_resume.py`

**Interfaces:**
- Produces: `class GovernanceMiddleware(AgentMiddleware)` 的 `awrap_tool_call`。
- Produces: `build_customer_service_agent(model, tools, checkpointer)`。
- Produces: `PostgresRuntimeAdapter`，封装线程绑定与 `AsyncPostgresSaver` 初始化。

- [ ] **Step 1: 写异步 handler、重试分类和工具 schema RED**

  ```python
  async def test_async_middleware_awaits_handler_and_returns_result():
      middleware = GovernanceMiddleware(limits=RunLimits(model_calls=4, tool_calls=8))
      result = await middleware.awrap_tool_call(request(), async_handler_returning("ok"))
      assert result == "ok"
  ```

  另测 429/5xx/timeout 才重试；订单写工具零重试；模型可见工具 schema 不含 `customer_id`；调用上限、证据不足和重复失败分别产生结构化 `handoff.required`，且不声称创建真实工单。prompt 契约断言中文专业语气、事实来源边界、禁止 chain-of-thought 和固定 `PROMPT_VERSION`。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_middleware.py tests/unit/agent_api/test_handoff.py tests/contract/test_prompt_contract.py tests/contract/test_agent_tools.py -q`

  Expected: 中间件或工厂行为缺失导致断言失败。

- [ ] **Step 3: 实现最小治理链和单 Agent 工厂**

  工厂只调用一次 `create_agent(name="customer_service_agent", ...)`；prompt 保存于模块常量并带 `PROMPT_VERSION`。中间件顺序固定为可信上下文/审计 → 调用预算 → 有限重试 → HITL → 摘要；写工具名单使用不可变集合。

- [ ] **Step 4: 运行单元/契约 GREEN，再执行 PostgreSQL 集成 RED→GREEN**

  Run unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_middleware.py tests/unit/agent_api/test_handoff.py tests/contract/test_prompt_contract.py tests/contract/test_agent_tools.py -q`

  Integration RED 在未调用 `.setup()` 或未持久化绑定时失败；最小实现使用 psycopg3 `autocommit=True`、`dict_row` 和 `AsyncPostgresSaver.setup()`。随后运行：

  `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres tests/integration/postgres/test_checkpoint_resume.py -q`

  Expected: 暂停、进程级适配器重建、同 thread resume、重复 decision 和客户不匹配均通过。该结果只证明测试 PostgreSQL，不证明生产部署。

- [ ] **Step 5: 提交 Agent 与持久恢复切片**

  Commit: `feat: assemble governed agent with durable checkpoints`

## Requirement Coverage

| Spec requirements | Plan task |
|---|---|
| AGT-001…006 | Task 5 Agent factory、prompt/tool 契约 |
| CTX-001…005 | Tasks 1、2、4 可信构造、线程绑定、API/CLI 注入 |
| API-001…004 | Tasks 2、4 消息校验、运行锁、所有权和取消 |
| HITL-001…006 | Tasks 4、5 decision 契约和 PostgreSQL 恢复 |
| API-HEALTH-001…002 | Task 4 liveness/readiness 契约 |
| SSE-001…004 | Task 3 事件与编码器 |
| MW-001…006 | Task 5 异步中间件、预算、重试、HITL、摘要边界 |
| ERR-001…003 | Tasks 1、3、5 错误分类、脱敏和转人工收敛 |
| HOF-001…004 | Task 5 结构化 handoff、实际步骤和 reason code |
| API-SCN-001…006 | Tasks 2…5 的契约与 PostgreSQL 集成场景 |

## Plan Verification

实施者完成全部任务后运行：

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres -q
UV_CACHE_DIR=.uv-cache uv run ruff check src tests
git diff --check
```

验证报告必须区分：scripted model、ASGI 契约、PostgreSQL 检查点集成和真实 DeepSeek 尚未验证的边界。
