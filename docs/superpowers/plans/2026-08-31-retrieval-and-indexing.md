# 检索与索引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现统一可引用检索契约，以及常规混合 RAG、RAPTOR、GraphRAG、受控联网搜索和原子索引发布流程。

**Architecture:** 目录遵循 `docs/architecture/code-layout.md`，Spec 020 的模型、算法、外部适配和工具全部位于 `retrieval/`。在线检索返回“有限模型证据 + 完整 artifact”；常规 RAG 与 RAPTOR 使用隔离的 Milvus collection，GraphRAG 只执行注册的 Neo4j 查询模板，联网搜索只处理外部时效信息。索引构建与在线查询进程分离，通过版本指针原子发布。

**Tech Stack:** Python 3.13.15、Milvus 3.0.0/PyMilvus 3.0.1、BGE-M3、Neo4j 2026.07.1、neo4j-graphrag 1.19.0、Tavily、Pydantic v2、pytest。

**Spec:** `docs/specs/020-retrieval-and-indexing.md`

## Global Constraints

- DG-003 已批准 BGE-M3 1024 维和 `BAAI/bge-reranker-v2-m3`。DG-004、DG-005 未批准时，不创建或发布真实 Milvus schema、检索默认参数或 RAPTOR 索引。
- 在线工具只读；构建和发布只能由离线 CLI 触发。
- 每个知识结论必须绑定实际 artifact 中存在的 citation。
- GraphRAG 只允许白名单模板，禁止执行模型生成 Cypher。
- Tavily 不得用于内部商品、政策、订单或退货规则。
- Milvus 不承担 Mem0 长期记忆；Mem0 使用 PostgreSQL/pgvector。

## File Structure

```text
src/customer_service_agent/retrieval/models.py   # 证据、citation、artifact 与校验
src/customer_service_agent/retrieval/hybrid.py   # dense/BM25、Milvus、small-to-big
src/customer_service_agent/retrieval/raptor.py   # 树节点、Milvus 查询与下钻
src/customer_service_agent/retrieval/graph.py    # Neo4j 白名单与路径证据
src/customer_service_agent/retrieval/indexing.py # 构建报告、校验、发布状态机
src/customer_service_agent/retrieval/tools.py    # 四个工具与 Tavily 边界
tests/unit/retrieval/
tests/contract/test_retrieval_tools.py
tests/integration/milvus/
tests/integration/neo4j/
```

### Task 1: 统一证据、artifact 与引用校验

**Files:**
- Create: `src/customer_service_agent/retrieval/models.py`
- Test: `tests/unit/retrieval/test_models.py`
- Test: `tests/unit/retrieval/test_citations.py`
- Test: `tests/contract/test_retrieval_tools.py`

**Interfaces:**
- Produces: `Citation(id, source_id, locator, title, url)`。
- Produces: `Evidence(citation_id, text)` 与 `RetrievalArtifact(hits, query, index_version, warnings)`。
- Produces: `RetrievalResult(answerable, evidence, citations, artifact)`。

- [x] **Step 1: 写伪引用、无界证据和未知字段 RED**

  ```python
  def test_rejects_evidence_with_unknown_citation():
      with pytest.raises(CitationIntegrityError):
          validate_citations(
              evidence=[Evidence(citation_id="missing", text="支持七天退货")],
              citations=[],
          )
  ```

  契约测试对 DTO 使用 `extra="forbid"`，并断言模型可见序列化不含完整原文、原始分数数组和内部连接信息。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_models.py tests/unit/retrieval/test_citations.py tests/contract/test_retrieval_tools.py -q`

  Expected: 引用完整性或长度边界尚未实现导致断言失败。

- [x] **Step 3: 实现最小 DTO 和校验器**

  ```python
  def validate_citations(*, evidence: Sequence[Evidence], citations: Sequence[Citation]) -> None:
      known = {item.id for item in citations}
      missing = {item.citation_id for item in evidence} - known
      if missing:
          raise CitationIntegrityError(sorted(missing))
  ```

  `RetrievalResult` 在无命中、冲突或低置信度时显式 `answerable=False`；长度裁剪产生 warning，不静默丢失来源。

- [x] **Step 4: 运行 GREEN**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_models.py tests/unit/retrieval/test_citations.py tests/contract/test_retrieval_tools.py -q`

  Expected: DTO、伪引用拒绝、模型内容/artifact 分离通过。

- [x] **Step 5: 提交统一检索契约**

  Commit: `feat: define traceable retrieval evidence contracts`

### Task 2: 常规混合检索与 small-to-big

**Files:**
- Create: `src/customer_service_agent/retrieval/hybrid.py`
- Test: `tests/unit/retrieval/test_hybrid.py`
- Test: `tests/integration/milvus/test_hybrid.py`

**Interfaces:**
- Produces: `HybridSearchPort.search_dense/search_sparse`。
- Produces: `fuse_hits(dense, sparse, config) -> list[FusedHit]`。
- Produces: `expand_parents(hits, parent_store) -> list[Evidence]`。

- [x] **Step 1: 写可复现融合与父块展开 RED**

  ```python
  def test_small_to_big_returns_parent_and_preserves_child_locator():
      result = expand_parents(
          [hit(child_id="c-2", parent_id="p-1", score=0.9)],
          FakeParentStore({"p-1": "完整商品说明"}),
      )
      assert result[0].text == "完整商品说明"
      assert result[0].locator.child_id == "c-2"
  ```

  固定 dense/BM25 输入顺序，断言相同配置产生相同排名，并在 artifact 保留两路原始命中。

- [x] **Step 2: 运行单元 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_hybrid.py -q`

  Expected: 融合/父块展开行为缺失导致断言失败。

- [x] **Step 3: 实现纯函数，不选择未批准参数**

  ```python
  @dataclass(frozen=True)
  class FusionConfig:
      dense_weight: Decimal
      sparse_weight: Decimal
      candidate_k: int
      final_k: int
  ```

  所有参数必须由调用者显式传入；DG-004 未批准时构建真实 adapter 返回 `CONFIG_NOT_APPROVED`，纯函数单元测试继续运行。

- [ ] **Step 4: 运行 GREEN；批准参数后执行 Milvus 集成 RED→GREEN**

  单元 GREEN 已完成；Milvus schema、真实 adapter 与集成测试等待 DG-004，不计为完成。

  Unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_hybrid.py -q`

  Integration 在测试 collection 中写入固定 child/parent 语料，验证 BGE-M3 dense、Milvus BM25、metadata/version 过滤和 parent 返回：

  `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_hybrid.py -q`

- [ ] **Step 5: 提交混合检索切片**

  纯函数子切片单独提交；完整 Task 2 仍以 Step 4 的 Milvus 集成通过为完成门。

  Commit: `feat: add reproducible hybrid retrieval`

### Task 3: RAPTOR 树构建与查询

**Files:**
- Create: `src/customer_service_agent/retrieval/raptor.py`
- Test: `tests/unit/retrieval/test_raptor.py`
- Test: `tests/integration/milvus/test_raptor.py`

**Interfaces:**
- Produces: `RaptorNode(node_id, level, text, child_ids, source_ids, data_version)`。
- Produces: `validate_tree(nodes)` 与 `descend_hits(root_hits, node_store)`。

- [x] **Step 1: 写孤立摘要与下钻路径 RED**

  ```python
  def test_summary_node_without_children_is_rejected():
      with pytest.raises(RaptorIntegrityError, match="children"):
          validate_tree([RaptorNode.summary("n-1", level=1, child_ids=[])])
  ```

  查询测试断言 artifact 同时包含命中层级、每次下钻 node ID 和最终叶子 source ID。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_raptor.py -q`

- [x] **Step 3: 实现确定性 ID、完整性检查和下钻**

  节点 ID 由 `data_version + level + sorted(child_ids) + content_hash` 计算；摘要模型只通过 `SummarizerPort` 注入。DG-005 未批准时只用 deterministic fake 验证树算法，不发布真实树。

- [ ] **Step 4: 运行 GREEN；获批后运行 Milvus RAPTOR 集成**

  Unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_raptor.py -q`

  Integration: `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_raptor.py -q`

  当前单元部分已通过；DG-005 未批准，因此未创建或运行真实 RAPTOR 索引与 Milvus 集成。

- [x] **Step 5: 提交 RAPTOR 纯函数切片**

  Commit: `feat: add traceable RAPTOR tree retrieval`

### Task 4: GraphRAG 白名单与联网边界

**Files:**
- Create: `src/customer_service_agent/retrieval/graph.py`
- Create: `src/customer_service_agent/retrieval/tools.py`
- Test: `tests/unit/retrieval/test_graph.py`
- Test: `tests/unit/retrieval/test_web_policy.py`
- Test: `tests/integration/neo4j/test_neo4j_graph.py`

**Interfaces:**
- Produces: `GraphQueryRegistry.execute(template_id, parameters)`。
- Produces: `WebSearchPolicy.authorize(domain, intent)`。

- [x] **Step 1: 写任意 Cypher 和内部政策联网 RED**

  ```python
  def test_unknown_graph_template_is_rejected_without_driver_call():
      registry = GraphQueryRegistry(driver=FailingIfCalledDriver(), templates={})
      with pytest.raises(GraphQueryRejected):
          registry.execute("model_generated", {})
  ```

  另测 `intent="internal_return_policy"` 必须拒绝 Tavily，`intent="public_logistics_disruption"` 才允许。

- [x] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_graph.py tests/unit/retrieval/test_web_policy.py -q`

- [x] **Step 3: 实现注册表、固定 schema 和网页结果 DTO**

  模板参数逐字段验证，Cypher 仅存在版本控制模块中。网页结果固定 `title/url/fetched_at/summary`，不进入内部知识索引。

- [x] **Step 4: 运行 GREEN 与 Neo4j 集成**

  Unit: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_graph.py tests/unit/retrieval/test_web_policy.py -q`

  Integration: `UV_CACHE_DIR=.uv-cache uv run pytest -m integration_neo4j tests/integration/neo4j/test_neo4j_graph.py -q`

  实际 Tavily 仅在 `live_web` marker 和显式开关下运行。

- [x] **Step 5: 提交图与联网边界**

  Commit: `feat: enforce graph and web retrieval boundaries`

### Task 5: 离线构建、报告与原子发布

**Files:**
- Create: `src/customer_service_agent/retrieval/indexing.py`
- Modify: `src/customer_service_agent/retrieval/tools.py`
- Test: `tests/unit/retrieval/test_index_pipeline.py`
- Test: `tests/contract/test_retrieval_tools.py`

**Interfaces:**
- Produces: `IndexBuildReport`、`PublishedIndexRef`。
- Produces: `IndexPipeline.build_candidate/validate/publish`。
- Produces: `search_product_faq`、`search_policy_raptor`、`search_commerce_graph`、`web_search`。

- [ ] **Step 1: 写失败构建不切换版本 RED**

  ```python
  def test_failed_candidate_never_replaces_published_version():
      publisher = InMemoryPublisher(current="v1")
      pipeline = IndexPipeline(builder=InvalidCandidateBuilder(), publisher=publisher)
      with pytest.raises(IndexBuildFailed):
          pipeline.run(data_version="catalog-2026-08")
      assert publisher.current == "v1"
  ```

- [ ] **Step 2: 运行 RED**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_index_pipeline.py -q`

- [ ] **Step 3: 实现显式状态机和脱敏报告**

  状态只允许 `candidate_built -> validated -> published`；任何失败写报告但不调用 publisher。报告包含输入哈希、数量、配置引用、耗时和 smoke 结果，不包含密钥/连接串/供应商原文。

- [ ] **Step 4: 运行 GREEN 和工具契约回归**

  Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_index_pipeline.py tests/contract/test_retrieval_tools.py -q`

- [ ] **Step 5: 提交索引发布切片**

  Commit: `feat: add versioned offline index publication`

## Requirement Coverage

| Spec requirements | Plan task |
|---|---|
| RET-COM-001…004 | Task 1 统一结果、artifact、answerable 和只读边界 |
| RET-HYB-001…004 | Task 2 双路融合、collection、small-to-big 和决策门 |
| RET-RAP-001…004 | Task 3 树完整性、路径追踪和 DG-005 |
| RET-GRA-001…004 | Task 4 固定 schema、白名单模板和路径证据 |
| RET-WEB-001…005 | Task 4 外部时效意图、来源和降级 |
| CIT-001…004 | Tasks 1、4 引用存在性、支持关系和订单例外 |
| IDX-001…006 | Task 5 离线构建、报告、原子发布和安全清理边界 |
| RET-SCN-001…005 | Tasks 2…5 的单元与集成验收场景 |

## Plan Verification

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval tests/contract/test_retrieval_tools.py -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_neo4j -q
git diff --check
```

报告分别列出：纯检索算法、Milvus、Neo4j、Tavily live 和受决策门阻塞的参数范围。
