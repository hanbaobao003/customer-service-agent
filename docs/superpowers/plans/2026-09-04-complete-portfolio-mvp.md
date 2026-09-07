# Complete Portfolio MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one local browser demo in which a real DeepSeek Agent calls every customer-service tool against seeded data and emits LangSmith traces.

**Architecture:** Existing domain modules remain authoritative. A new `mvp` package owns only configuration, idempotent seed data, concrete adapters, and the FastAPI composition root. FastAPI serves a dependency-free page that consumes the existing SSE events and submits HITL decisions.

**Tech Stack:** Python 3.13, FastAPI, LangChain `create_agent`, LangGraph PostgreSQL checkpointer, PostgreSQL/pgvector, Milvus, Neo4j, Mem0, Tavily, vanilla HTML/CSS/JavaScript, pytest, LangSmith.

**Spec:** `docs/specs/010-agent-api-and-middleware.md`, `docs/specs/020-retrieval-and-indexing.md`, `docs/specs/030-orders-and-sql.md`, `docs/specs/040-long-term-memory.md`, `docs/specs/050-evaluation-and-observability.md`

## Global Constraints

- Use exactly one named `customer_service_agent` graph and `deepseek-v4-flash`.
- `customer_id` stays in trusted runtime context and never appears in model-visible schemas.
- Every order write is previewed, approved/rejected, then executed at most once.
- Seed data may touch only resources whose names begin `wang_agent_mvp_`.
- UI never renders secrets, raw SQL, ToolMessage artifacts, chain-of-thought, hidden prompts, or full PII.
- Unit and contract tests have no network or Docker dependency; Docker/live tests are explicit opt-ins.
- Do not add React, production authentication, payments, task queues, or a 60-case evaluation gate.

## File Structure

- Create `src/customer_service_agent/mvp/settings.py`: validated environment references and owned resource names.
- Create `src/customer_service_agent/mvp/seed.py`: idempotent PostgreSQL, Milvus, Neo4j, and Mem0 demo seed data.
- Create `src/customer_service_agent/mvp/services.py`: concrete retrieval, SQL, order, memory, and Tavily ports.
- Create `src/customer_service_agent/mvp/app.py`: real Agent composition, readiness, and root page route.
- Create `src/customer_service_agent/mvp/static/index.html`: single-page chat interface.
- Modify `src/customer_service_agent/agent_api/service.py`: safe citation/approval/result SSE mapping.
- Modify `src/customer_service_agent/commerce/orders.py`: preview then execute-once coordinator.
- Modify `src/customer_service_agent/memory/service.py`: LangChain memory tool factory.
- Modify `src/customer_service_agent/retrieval/tools.py`: safe retrieval citation metadata.
- Create `tests/unit/mvp/`, `tests/contract/test_mvp_*.py`, `tests/integration/mvp/test_seeded_demo.py`, and `tests/live/test_mvp_deepseek.py`.
- Modify `README.md`, `docs/demo.md`, and add the dated validation report.

---

### Task 1: Validated settings and owned seed resources

**Files:**
- Create: `src/customer_service_agent/mvp/__init__.py`
- Create: `src/customer_service_agent/mvp/settings.py`
- Test: `tests/unit/mvp/test_settings.py`

**Interfaces:**
- Produces `MvpSettings.from_environment(env: Mapping[str, str]) -> MvpSettings`.
- Produces `validate_owned_resource(name: str, prefix: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_settings_requires_deepseek_reference() -> None:
    with pytest.raises(ValueError, match="Deepseek_API_KEY"):
        MvpSettings.from_environment({})

def test_rejects_unowned_resource() -> None:
    with pytest.raises(ValueError, match="mvp resource"):
        validate_owned_resource("orders", "wang_agent_mvp_")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/unit/mvp/test_settings.py -q`

Expected: FAIL because the MVP package does not exist.

- [ ] **Step 3: Implement the minimum**

Require named values only at composition; do not log them. Require owned database/collection/graph names before any seed write or cleanup.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/unit/mvp/test_settings.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/customer_service_agent/mvp tests/unit/mvp
git commit -m "feat: add validated mvp settings"
```

### Task 2: Persistent idempotent seed data

**Files:**
- Create: `src/customer_service_agent/mvp/seed.py`
- Test: `tests/integration/mvp/test_seeded_demo.py`

**Interfaces:**
- Produces `async def seed_mvp(settings: MvpSettings) -> SeedReport`.
- Produces CLI command `uv run python -m customer_service_agent.mvp.seed`.

- [ ] **Step 1: Write failing integration tests**

```python
async def test_seed_is_idempotent_and_customer_scoped(mvp_settings) -> None:
    first = await seed_mvp(mvp_settings)
    second = await seed_mvp(mvp_settings)
    assert first.orders == second.orders == ("MVP-ORDER-1001", "MVP-ORDER-2001")
```

Assert a FAQ collection, RAPTOR policy collection, Neo4j product graph, and persistent `customer_memories_v1` record exist. Assert customer B cannot read customer A's order or memory.

- [ ] **Step 2: Run RED**

Run: `RUN_MVP_INTEGRATION=1 uv run --env-file .env pytest tests/integration/mvp/test_seeded_demo.py -q`

Expected: FAIL because `seed_mvp` is absent.

- [ ] **Step 3: Implement the minimum**

Use only upserts/MERGE. Seed two customers, three products, two orders, FAQ warranty/delivery documents, return-policy RAPTOR leaves/summaries, product-brand-category-promotion relationships, and a non-sensitive language preference. Normal seed execution never drops resources.

- [ ] **Step 4: Run GREEN**

Run: `RUN_MVP_INTEGRATION=1 uv run --env-file .env pytest tests/integration/mvp/test_seeded_demo.py -q`

Expected: PASS on two consecutive runs.

- [ ] **Step 5: Commit**

```bash
git add src/customer_service_agent/mvp/seed.py tests/integration/mvp
git commit -m "feat: seed persistent mvp demo data"
```

### Task 3: Concrete services for every fixed tool

**Files:**
- Create: `src/customer_service_agent/mvp/services.py`
- Modify: `src/customer_service_agent/retrieval/tools.py`
- Modify: `src/customer_service_agent/memory/service.py`
- Test: `tests/unit/mvp/test_services.py`
- Test: `tests/contract/test_memory_agent_tools.py`

**Interfaces:**
- Produces concrete existing ports for `web_search`, three internal retrieval tools, `query_business_data`, five order tools, and three memory tools.
- Produces `create_memory_tools(service: MemoryService) -> tuple[BaseTool, ...]`.

- [ ] **Step 1: Write failing tests**

```python
async def test_memory_schema_hides_customer_id() -> None:
    tools = {item.name: item for item in create_memory_tools(memory_service)}
    assert "customer_id" not in json.dumps(
        tools["remember_preference"].args_schema.model_json_schema()
    )

async def test_faq_returns_parent_evidence_and_citation() -> None:
    result = await service.search_product_faq("保修多久")
    assert result.artifact.retriever == "hybrid"
    assert result.citations[0].id in {item.citation_id for item in result.evidence}
```

Add one contract assertion for each fixed tool name and customer scope.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/unit/mvp/test_services.py tests/contract/test_memory_agent_tools.py -q`

Expected: FAIL because concrete MVP services do not exist.

- [ ] **Step 3: Implement the minimum**

Reuse existing hybrid/RAPTOR/graph/SQL/domain rules. Use live Tavily only when configured; otherwise return a safe unavailable result. Preserve full retrieval output in artifacts and emit only validated citation IDs to application events.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/unit/mvp/test_services.py tests/contract/test_memory_agent_tools.py tests/contract/test_retrieval_tools.py tests/contract/test_order_tools.py tests/contract/test_order_write_tools.py tests/contract/test_sql_tool.py -q`

Expected: PASS without network access.

- [ ] **Step 5: Commit**

```bash
git add src/customer_service_agent/mvp/services.py src/customer_service_agent/retrieval/tools.py src/customer_service_agent/memory/service.py tests
git commit -m "feat: wire mvp agent tools"
```

### Task 4: Preview, approval, and one-time order execution

**Files:**
- Modify: `src/customer_service_agent/commerce/orders.py`
- Modify: `src/customer_service_agent/agent_api/service.py`
- Test: `tests/contract/test_mvp_order_approval.py`
- Test: `tests/integration/postgres/test_orders.py`

**Interfaces:**
- Produces `OrderApprovalCoordinator.preview(...) -> OperationPreview`.
- Produces `OrderApprovalCoordinator.execute_approved(...) -> dict[str, object]`.

- [ ] **Step 1: Write failing tests**

```python
async def test_cancel_changes_state_only_after_one_approval() -> None:
    preview = await coordinator.preview(context, request)
    assert (await store.get("MVP-ORDER-1001", context.customer_id)).status is OrderStatus.PAID
    first = await coordinator.execute_approved(context, operation_id=preview.operation_id)
    second = await coordinator.execute_approved(context, operation_id=preview.operation_id)
    assert first == second
    assert (await store.get("MVP-ORDER-1001", context.customer_id)).status is OrderStatus.CANCELLED
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/contract/test_mvp_order_approval.py -q`

Expected: FAIL because the coordinator is absent.

- [ ] **Step 3: Implement the minimum**

Persist the preview before emitting `approval.required`. On `approve`, verify matching checkpoint/thread/customer and call `OperationService.execute_approved` once. On `reject`, preserve the order. Do not use HITL middleware to rerun a preview factory.

- [ ] **Step 4: Run GREEN**

Run: `RUN_MVP_INTEGRATION=1 uv run --env-file .env pytest tests/contract/test_mvp_order_approval.py tests/integration/postgres/test_orders.py tests/integration/postgres/test_checkpoint_resume.py -q`

Expected: PASS; duplicate approval returns the saved first result.

- [ ] **Step 5: Commit**

```bash
git add src/customer_service_agent/commerce/orders.py src/customer_service_agent/agent_api/service.py tests
git commit -m "feat: execute approved mvp order operations"
```

### Task 5: Real Agent composition and SSE observability

**Files:**
- Create: `src/customer_service_agent/mvp/app.py`
- Modify: `src/customer_service_agent/agent_api/service.py`
- Test: `tests/contract/test_mvp_app.py`
- Test: `tests/live/test_mvp_deepseek.py`

**Interfaces:**
- Produces `create_mvp_app(settings: MvpSettings) -> FastAPI` and `app`.
- Keeps the existing messages and decisions endpoints unchanged.

- [ ] **Step 1: Write failing tests**

```python
async def test_real_composition_exposes_every_tool() -> None:
    app = create_mvp_app(fake_settings)
    assert app.state.tool_names == EXPECTED_TOOL_NAMES
```

Add a live test that sends a warranty request and expects a completed response plus `search_product_faq` tool activity.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/contract/test_mvp_app.py -q`

Expected: FAIL because `create_mvp_app` is undefined.

- [ ] **Step 3: Implement the minimum**

Create one Agent with the existing PostgreSQL checkpointer, async middleware, central YAML prompt, concrete tool tuple, and opt-in LangSmith tracing. Readiness checks only local dependencies. Turn safe artifact citations into deduplicated public `citation` events; never expose score, graph path, raw SQL, or PII.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/contract/test_mvp_app.py -q`

Run: `RUN_MVP_LIVE=1 uv run --env-file .env pytest tests/live/test_mvp_deepseek.py -q -rs`

Expected: contract PASS; live PASS or an explicit skipped missing-variable message.

- [ ] **Step 5: Commit**

```bash
git add src/customer_service_agent/mvp/app.py src/customer_service_agent/agent_api/service.py tests
git commit -m "feat: compose real local mvp agent"
```

### Task 6: One-page browser demonstration and final verification

**Files:**
- Create: `src/customer_service_agent/mvp/static/index.html`
- Modify: `src/customer_service_agent/mvp/app.py`
- Test: `tests/contract/test_mvp_page.py`
- Modify: `README.md`, `docs/demo.md`
- Create: `docs/reports/2026-09-04-mvp-validation-report.md`

**Interfaces:**
- `GET /` serves the page.
- It posts JSON messages, consumes SSE, and posts approve/reject decisions using one thread ID.

- [ ] **Step 1: Write failing page contract**

```python
async def test_root_has_message_tools_citations_and_approval_panels() -> None:
    response = await client.get("/")
    assert response.status_code == 200
    for element in ("message-form", "tool-events", "citations", "approval"):
        assert f'id="{element}"' in response.text
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/contract/test_mvp_page.py -q`

Expected: FAIL because root route is absent.

- [ ] **Step 3: Implement the minimum**

Use `fetch` plus `ReadableStream` to parse SSE. Render user/assistant messages, compact tool timeline, citations, and approval buttons. Label the selector “本地演示身份”; it is a trusted API header, not a model message. Document seed/start commands and all-tool prompts.

- [ ] **Step 4: Run GREEN and verification**

Run: `uv run pytest -m 'unit or contract' -q`

Run: `uv run ruff check src tests`

Run: `git diff --check`

Run Docker and live tests from Tasks 2, 4, and 5 with explicit gates. Record actual totals and LangSmith trace IDs without secrets.

- [ ] **Step 5: Commit**

```bash
git add src/customer_service_agent/mvp README.md docs tests
git commit -m "feat: add complete mvp browser demo"
```

## Plan Self-Review

- Tasks 2–5 cover all fixed tool names, trusted context, local dependency isolation, safe citations, checkpoints, and order state changes.
- Task 6 provides the interview flow; no separate frontend system is introduced.
- Production authentication, payment, task queues, deployment, Langfuse, and the full 60-case judge remain intentionally excluded.
- No placeholders or deferred implementation steps remain.
