# 评测与可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可复现的测试分层守卫、约 60 条版本化评测场景、确定性评分器、质量门禁、脱敏 Trace 和显式 opt-in 的 LangSmith 实验。

**Architecture:** 目录遵循 `docs/architecture/code-layout.md`，Spec 050 只保留 `quality/evaluation.py` 与 `quality/observability.py`。评测数据以版本化 JSONL 为事实源；确定性评分器先判安全、工具、引用、状态和检索命中，LLM 裁判不能覆盖安全失败。Trace 通过同模块内的脱敏映射器发送到可替换 `TraceSink`，LangSmith 不可用不阻断核心服务。

**Tech Stack:** Python 3.13.15、pytest、Pydantic v2、LangSmith、标准库统计函数、JSONL。

**Spec:** `docs/specs/050-evaluation-and-observability.md`

## Global Constraints

- 默认测试不得访问任何外部服务。
- 真实 DeepSeek、Tavily、SiliconFlow、Mem0 和 LangSmith 必须显式 opt-in。
- 安全、权限、HITL、幂等和状态机失败始终阻断，LLM 裁判不能覆盖。
- 固定评测集为路由 10、RAG 15、SQL 8、订单 15、记忆 6、多轮/转人工 6。
- 每次实验固定代码提交、数据版本、索引版本、模型 ID 和 prompt version。
- 不记录密钥、完整 PII、chain-of-thought 或无界 artifact。

## File Structure

```text
src/customer_service_agent/quality/evaluation.py    # schema、加载、评分、指标、报告
src/customer_service_agent/quality/observability.py # Trace、脱敏、sink、LangSmith
evals/datasets/customer_service_v1.jsonl            # 60 条固定场景
evals/datasets/CHANGELOG.md                          # 数据集版本变更
tests/unit/quality/
tests/contract/test_eval_dataset.py
tests/live/test_langsmith_upload.py
```

### Task 1: 测试分层配置与外部访问守卫

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `tests/unit/quality/test_test_boundaries.py`

**Interfaces:**
- Produces: pytest markers 与 `ExternalAccessGuard`。
- Produces: `requires_opt_in(env_name)` fixture helper。

- [x] **Step 1: 写默认测试禁止 socket 和未知 marker RED**

  ```python
  def test_unit_test_cannot_open_network_socket(external_access_guard):
      with pytest.raises(ExternalAccessBlocked):
          socket.create_connection(("example.com", 443))
  ```

  配置测试断言所有计划 marker 已注册；live 测试缺少对应环境开关时为 skipped，不是 failed 或静默执行。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_test_boundaries.py -q`

- [x] **Step 3: 实现最小测试守卫**

  守卫只在 unit/contract 测试生效，Docker/live marker 明确解除对应限制；解除不提供凭证，只允许测试读取预先配置的引用。pytest 设置 `--strict-markers`。

- [x] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_test_boundaries.py -q`

- [x] **Step 5: 提交测试边界**

  Commit: `test: enforce isolated verification layers`

### Task 2: 评测样本 schema 与 60 条固定数据集

**Files:**
- Create: `src/customer_service_agent/quality/evaluation.py`
- Create: `evals/datasets/customer_service_v1.jsonl`
- Create: `evals/datasets/CHANGELOG.md`
- Test: `tests/contract/test_eval_dataset.py`

**Interfaces:**
- Produces: `EvalCase(id, group, input, context, required_tools, forbidden_tools, gold_evidence, approval, expected_state, scoring)`。
- Produces: `load_dataset(path, expected_version) -> tuple[EvalCase, ...]`。

- [ ] **Step 1: 写重复 ID、错误分组数量和危险上下文 RED**

  ```python
  def test_dataset_has_exact_group_counts(dataset_v1):
      assert Counter(case.group for case in dataset_v1) == {
          "route": 10, "rag": 15, "sql": 8,
          "order": 15, "memory": 6, "multi_handoff": 6,
      }
  ```

  另测每条样本 ID 唯一、上下文客户 ID 非空、写订单样本有 approval/expected_state、无答案样本有禁止编造评分。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/contract/test_eval_dataset.py -q`

  Expected: 数据集缺失或数量/字段不足导致契约断言失败。

- [ ] **Step 3: 逐组添加确定性样本和严格加载器**

  每条 JSONL 只包含模拟数据和稳定 evidence ID；RAG 每种 5 条，订单覆盖越权/批准/拒绝/并发/重复，记忆覆盖明确/非明确/召回/删除/敏感/隔离。加载器 `extra="forbid"`，版本不匹配拒绝运行。

- [ ] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/contract/test_eval_dataset.py -q`

  Expected: 60 条、分组数量、唯一 ID 和必填评分字段全部通过。

- [ ] **Step 5: 提交评测数据集**

  Commit: `test: add versioned customer service evaluation set`

### Task 3: 确定性评分器与质量门禁

**Files:**
- Modify: `src/customer_service_agent/quality/evaluation.py`
- Test: `tests/unit/quality/test_evaluation_metrics.py`

**Interfaces:**
- Produces: `score_tools`、`score_citations`、`score_state`、`score_hit_at_k`。
- Produces: `evaluate_gates(records) -> GateReport`。

- [ ] **Step 1: 写安全失败不可被平均和 P95 RED**

  ```python
  def test_single_security_failure_blocks_release_even_with_high_average():
      report = evaluate_gates(records_with_one_unauthorized_write())
      assert report.metrics["safety_state_machine"] < 1.0
      assert report.release_blocked is True
  ```

  另测工具选择 ≥90%、知识 ≥85%、引用 ≥95%、三类 Hit@5 各 ≥80%、内部检索 P95 ≤2 秒；skipped 不进入分母也不能算通过。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_evaluation_metrics.py -q`

- [ ] **Step 3: 实现纯函数评分和显式分母**

  ```python
  def hit_at_k(actual: Sequence[str], accepted: set[str], k: int = 5) -> bool:
      return bool(set(actual[:k]) & accepted)
  ```

  P95 使用固定 nearest-rank 定义并在报告记录样本数；外部网络与模型耗时不进入内部检索延迟。

- [ ] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_evaluation_metrics.py -q`

- [ ] **Step 5: 提交评分器**

  Commit: `feat: add deterministic evaluation gates`

### Task 4: Trace 层级、脱敏与降级

**Files:**
- Create: `src/customer_service_agent/quality/observability.py`
- Test: `tests/unit/quality/test_observability.py`

**Interfaces:**
- Produces: `TraceRecord(run_kind, request_id, thread_id, tool_name, sizes, metadata)`。
- Produces: `redact_trace(record) -> SafeTraceRecord`。
- Produces: `BestEffortTraceSink.emit`。

- [ ] **Step 1: 写 PII/artifact 脱敏和 sink 故障 RED**

  ```python
  async def test_trace_failure_never_blocks_tool_result():
      sink = BestEffortTraceSink(delegate=AlwaysFailingSink(), local_metrics=InMemoryMetrics())
      result = await run_with_trace(sink, operation=lambda: async_value("order-ok"))
      assert result == "order-ok"
      assert sink.local_metrics.count("trace_degraded") == 1
  ```

  脱敏测试覆盖完整电话、地址、连接串、API key 字段、prompt 和 chain-of-thought；保留 request ID 和受控工具名。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_observability.py -q`

- [ ] **Step 3: 实现 allowlist 元数据和大小预算**

  Trace 明确区分 `agent_wrapper`、`llm_leaf`、`tool`；只允许规定 metadata key。content/artifact 分别记录字节大小，超过预算截断并记录标志，不上传原始无界内容。

- [ ] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_observability.py -q`

- [ ] **Step 5: 提交可观测性核心**

  Commit: `feat: add privacy-safe best-effort tracing`

### Task 5: LangSmith 实验、LLM 裁判门和失败报告

**Files:**
- Modify: `src/customer_service_agent/quality/evaluation.py`
- Modify: `src/customer_service_agent/quality/observability.py`
- Test: `tests/unit/quality/test_evaluation_report.py`
- Test: `tests/live/test_langsmith_upload.py`

**Interfaces:**
- Produces: `JudgeConfig(model_id, prompt_version, temperature, budget_ref)`。
- Produces: `LangSmithExperimentRunner.run(dataset, revision)`。
- Produces: `FailureReport`。

- [ ] **Step 1: 写未批准裁判和报告边界 RED**

  ```python
  def test_llm_judge_cannot_run_without_approved_config():
      with pytest.raises(DecisionGateClosed, match="DG-007"):
          build_llm_judge(config=None)
  ```

  报告测试断言记录 code/data/index/model/prompt 版本、失败样本和外部波动，但不包含密钥或完整供应商响应。

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_evaluation_report.py -q`

- [ ] **Step 3: 实现决策门、失败优先级和 opt-in 上传**

  确定性安全失败先写入最终 verdict；LLM judge 只能评价非安全知识质量。LangSmith adapter 在没有显式开关时不初始化 client；上传失败进入 `trace_degraded`，不更改业务结果。

- [ ] **Step 4: 运行 GREEN 和显式 live 验证**

  Unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_evaluation_report.py -q`

  Live（仅用户批准 DG-007 并提供 opt-in 时）：`UV_CACHE_DIR=.uv-cache uv run pytest -m live_langsmith tests/live/test_langsmith_upload.py -q`

- [ ] **Step 5: 提交实验与报告层**

  Commit: `feat: add gated LangSmith evaluation reporting`

## Requirement Coverage

| Spec requirements | Plan task |
|---|---|
| TDD-001…006 | Task 1 与项目 TDD 执行规范 |
| TEST-001…005 | Tasks 1、2 单元/契约隔离和 schema |
| TEST-006…009 | 各领域计划的 Docker 集成任务与 Task 1 marker |
| TEST-010…013 | Tasks 1、5 显式 opt-in 和验证表述边界 |
| EVAL-001…005 | Task 2 严格样本 schema、版本和固定上下文 |
| MET-001…009 | Task 3 确定性评分、P95 和阻断门禁 |
| JUDGE-001…004 | Task 5 DG-007、固定 schema 和安全优先 |
| OBS-001…005 | Task 4 Trace 层级、脱敏、大小和 best-effort sink |
| EVAL-SCN-001…005 | Tasks 2…5 的数据集、评分器、live 和降级场景 |

## Plan Verification

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality tests/contract/test_eval_dataset.py -q
UV_CACHE_DIR=.uv-cache uv run pytest -m eval_gate -q
git diff --check
```

最终报告必须列出每个 marker 的 passed/failed/skipped，并说明 60 条样本的统计限制。
