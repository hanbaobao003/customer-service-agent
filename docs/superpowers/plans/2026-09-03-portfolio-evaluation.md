# Portfolio Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为简历演示提供可复现的六条本地评测，以及显式 opt-in 的 LangSmith 数据集和实验。

**Architecture:** `quality/portfolio_eval.py` 只负责读取固定 JSONL、执行确定性工具选择评分、通过注入的 LangSmith Client 同步数据集并启动实验。真实模型 Trace 继续复用现有 `live_model` 测试，避免为了演示复制一套 Agent。

**Tech Stack:** Python 3.13、Pydantic v2、LangSmith SDK、pytest。

**Spec:** `docs/specs/050-evaluation-and-observability.md` 第 1.1 节。

## Global Constraints

- 默认单元、契约测试不得访问网络；LangSmith 仅在 `RUN_LIVE_LANGSMITH_TESTS=1` 时访问。
- 数据集中不得出现真实客户、地址、电话、密钥、连接串或 chain-of-thought。
- 此档案只验证评测与 Trace 管线，不得描述为完整 60 条质量门禁或生产认证。

---

### Task 1: 固定作品集样本与确定性评分

**Files:**
- Create: `src/customer_service_agent/quality/portfolio_eval.py`
- Create: `src/customer_service_agent/quality/__init__.py`
- Create: `evals/datasets/portfolio_v1.jsonl`
- Test: `tests/unit/quality/test_portfolio_eval.py`

**Interfaces:**
- Produces: `PortfolioEvalCase(case_id, group, message, expected_tool)`。
- Produces: `load_portfolio_cases(path) -> tuple[PortfolioEvalCase, ...]`。
- Produces: `score_selected_tool(outputs, reference_outputs) -> dict[str, object]`。

- [x] **Step 1: Write the failing test**

```python
def test_score_selected_tool_marks_expected_tool_as_passed() -> None:
    assert score_selected_tool(
        {}, {"selected_tool": "get_order"}, {"expected_tool": "get_order"}
    ) == {"key": "tool_route", "score": 1}
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/quality/test_portfolio_eval.py -q`

Expected: FAIL because `customer_service_agent.quality.portfolio_eval` does not exist.

- [x] **Step 3: Write minimal implementation**

```python
def score_selected_tool(
    _inputs: dict[str, object], outputs: dict[str, object], reference_outputs: dict[str, object]
) -> dict[str, object]:
    return {"key": "tool_route", "score": int(outputs["selected_tool"] == reference_outputs["expected_tool"])}
```

`load_portfolio_cases` must reject duplicate case IDs, unknown groups and blank message/tool values.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/quality/test_portfolio_eval.py -q`

Expected: PASS; six cases cover `route`、`rag_hybrid`、`rag_raptor`、`rag_graph`、`sql`、`order` once each.

- [x] **Step 5: Commit**

```bash
git add src/customer_service_agent/quality tests/unit/quality/test_portfolio_eval.py evals/datasets/portfolio_v1.jsonl
git commit -m "test: add portfolio evaluation dataset"
```

### Task 2: LangSmith 数据集与实验适配

**Files:**
- Modify: `src/customer_service_agent/quality/portfolio_eval.py`
- Create: `tests/live/test_langsmith_portfolio_eval.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/unit/quality/test_portfolio_eval.py`

**Interfaces:**
- Consumes: `tuple[PortfolioEvalCase, ...]` 和具有 `has_dataset`、`create_dataset`、`create_examples`、`evaluate` 方法的 LangSmith Client。
- Produces: `sync_portfolio_dataset(client, dataset_name, cases) -> str`。
- Produces: `run_portfolio_experiment(client, dataset_name, target) -> object`。

- [x] **Step 1: Write the failing test**

```python
def test_sync_reuses_existing_dataset_without_creating_examples() -> None:
    client = ExistingDatasetClient()
    assert sync_portfolio_dataset(client, "wang-agent-portfolio-v1", CASES) == "wang-agent-portfolio-v1"
    assert client.created_examples == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/quality/test_portfolio_eval.py -q`

Expected: FAIL because `sync_portfolio_dataset` does not exist.

- [x] **Step 3: Write minimal implementation**

```python
def sync_portfolio_dataset(client: object, dataset_name: str, cases: tuple[PortfolioEvalCase, ...]) -> str:
    if client.has_dataset(dataset_name=dataset_name):
        return dataset_name
    dataset = client.create_dataset(dataset_name, description="wang-agent portfolio evaluation v1")
    client.create_examples(dataset_id=dataset.id, examples=[case.as_example() for case in cases])
    return dataset_name
```

`run_portfolio_experiment` calls `client.evaluate` with `score_selected_tool`, `experiment_prefix="wang-agent-portfolio"`, and `max_concurrency=1`. The live test only runs when both LangSmith and DeepSeek opt-ins/configuration are present, uses the existing bound `echo_test` model call as its target, and creates a trace plus an experiment.

- [x] **Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/unit/quality/test_portfolio_eval.py -q`

Expected: PASS; no unit test initializes a real LangSmith Client.

- [x] **Step 5: Commit**

```bash
git add pyproject.toml README.md src/customer_service_agent/quality tests/unit/quality/test_portfolio_eval.py tests/live/test_langsmith_portfolio_eval.py
git commit -m "feat: add minimal LangSmith portfolio evaluation"
```

## Self-Review

- Spec coverage: only Spec 050 section 1.1 is implemented; production M6 requirements remain deferred and explicitly documented.
- Placeholder scan: this plan contains no unfinished-work markers.
- Type consistency: dataset examples use `inputs.message` and `outputs.expected_tool`; the target returns `selected_tool`, which the scorer compares exactly.
