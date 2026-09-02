# M4 Milvus 与 RAPTOR 索引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 在用户既有 Docker Milvus 上实现隔离的常规混合检索与 RAPTOR 离线索引、版本化发布和 L3 集成验证。

**Architecture:** 保留已验证的纯函数融合、small-to-big 与 RAPTOR 路径追踪；在现有 retrieval/hybrid.py、raptor.py、indexing.py 内添加窄的 PyMilvus 适配器，不新增通用数据访问层。每次 L3 运行创建专属数据库 wang_agent_it_<12位十六进制run_id>，其中只创建两个候选 collection：hybrid_candidate_<run_id> 与 raptor_candidate_<run_id>；各自通过 current alias 发布。清理只删除本次验证过名称的数据库。

**Tech Stack:** Python 3.13、Milvus 3.0.0、PyMilvus 3.0.1、BGE-M3（1024 维）、Milvus 内置 BM25、BAAI/bge-reranker-v2-m3、DeepSeek deepseek-v4-flash、pytest。

**Spec:** docs/specs/020-retrieval-and-indexing.md；docs/decisions/001-m0-runtime-and-docker-baseline.md；docs/superpowers/specs/2026-09-02-central-prompt-library-design.md

## Global Constraints

- DG-004：token chunk 500、overlap 100、候选 top-k 20、dense/BM25 权重 0.5/0.5、rerank 3。
- DG-005：L0 叶节点与 L1–L3 三层摘要、余弦确定性层次聚类、seed 42、每父节点目标 8 子节点（尾组为避免单子节点摘要可为 9）、deepseek-v4-flash、temperature 0、上限 300 tokens、raptor-summary-v1。
- BGE-M3 维度固定 1024；Milvus 使用启用 `chinese` analyzer 的 `VARCHAR` + 内置 BM25 产生 sparse field，dense field 使用 COSINE。当前 Docker Milvus 未启用 StorageV3，不能使用 `TEXT`；这不改变官方 BM25 对 `VARCHAR` 的支持。
- 真实 embedding/rerank 必须标记 live_embedding；真实 DeepSeek 摘要必须标记 live_model。两者默认不执行。
- 不读取、打印或提交 .env 值；运行期仅使用环境变量名称。
- 不删除已有 Milvus 数据库或 collection；所有删除都在严格验证本次 run 名称之后执行。

---

### Task 1: 固定 L3 资源名、连接端口与安全清理

**Files:**
- Modify: pyproject.toml
- Modify: uv.lock
- Modify: src/customer_service_agent/retrieval/indexing.py
- Create: tests/integration/milvus/conftest.py
- Create: tests/integration/milvus/test_resource_safety.py

**Interfaces:**
- Produces: MilvusRunResources(run_id: str, database_name: str, hybrid_collection: str, raptor_collection: str, hybrid_alias: str, raptor_alias: str)。
- Produces: make_milvus_run_resources(run_id: str) -> MilvusRunResources。
- Produces: validate_milvus_owned_database(name: str, run_id: str) -> None。

- [x] **Step 1: 写资源名和破坏性清理的失败测试**

    def test_cleanup_rejects_database_not_owned_by_current_run() -> None:
        with pytest.raises(ValueError, match="owned database"):
            validate_milvus_owned_database("default", "a1b2c3d4e5f6")

    def test_run_resources_are_deterministic_and_isolated() -> None:
        resources = make_milvus_run_resources("a1b2c3d4e5f6")
        assert resources.database_name == "wang_agent_it_a1b2c3d4e5f6"
        assert resources.hybrid_collection == "hybrid_candidate_a1b2c3d4e5f6"
        assert resources.raptor_collection == "raptor_candidate_a1b2c3d4e5f6"

- [x] **Step 2: 运行 RED**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/integration/milvus/test_resource_safety.py -q

Expected: FAIL，因为资源 DTO 和验证函数尚未定义；不是 Docker 连接失败。

- [x] **Step 3: 安装已批准依赖并实现最小资源构造与 pytest fixture**

Run: UV_CACHE_DIR=.uv-cache uv add pymilvus==3.0.1

    _MILVUS_DATABASE_RE = re.compile(r"^wang_agent_it_[0-9a-f]{12}$")

    def make_milvus_run_resources(run_id: str) -> MilvusRunResources:
        if not re.fullmatch(r"[0-9a-f]{12}", run_id):
            raise ValueError("run_id must be 12 lowercase hex characters")
        return MilvusRunResources(
            run_id=run_id,
            database_name=f"wang_agent_it_{run_id}",
            hybrid_collection=f"hybrid_candidate_{run_id}",
            raptor_collection=f"raptor_candidate_{run_id}",
            hybrid_alias=f"hybrid_current_{run_id}",
            raptor_alias=f"raptor_current_{run_id}",
        )

集成 fixture 用 uuid4().hex[:12] 创建 database；yield 后先精确比较 database_name 和 run_id，再调用 drop_database(database_name)。fixture 只从 MILVUS_URI 和可选 MILVUS_TOKEN 读取连接信息，缺少 RUN_MILVUS_INTEGRATION=1 时 pytest.skip。

- [x] **Step 4: 运行 GREEN 和真实资源清理验证**

Run: RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_resource_safety.py -q

Expected: PASS；测试结束后以只读 client 确认该 database 不存在，且不查询/删除 default。

- [x] **Step 5: 记录 TDD 证据并提交**

Create: docs/tdd/records/m4-milvus-integration.md

    git add src/customer_service_agent/retrieval/indexing.py \
      tests/integration/milvus/conftest.py tests/integration/milvus/test_resource_safety.py \
      docs/tdd/records/m4-milvus-integration.md
    git commit -m "test: isolate Milvus integration resources"

### Task 2: 常规 RAG Milvus schema、写入和检索端口

**Files:**
- Modify: src/customer_service_agent/retrieval/hybrid.py
- Create: tests/integration/milvus/test_milvus_hybrid.py
- Modify: tests/unit/retrieval/test_hybrid.py

**Interfaces:**
- Produces: HybridIndexDocument(child_id, parent_id, source_id, title, child_text, parent_text, child_locator, parent_locator, data_version, dense_vector)。
- Produces: MilvusHybridIndex(client, collection_name, data_version) implementing HybridSearchPort and ParentStorePort。
- Produces: create_hybrid_collection(client, collection_name) -> None。
- Consumes: FusionConfig(method="weighted_reciprocal_rank", dense_weight=Decimal("0.5"), sparse_weight=Decimal("0.5"), candidate_k=20, final_k=3)。

- [x] **Step 1: 写 schema 参数与映射失败测试**

    def test_hybrid_document_rejects_vector_not_bge_m3_dimension() -> None:
        with pytest.raises(HybridConfigError, match="1024"):
            HybridIndexDocument(
                child_id="child-1", parent_id="parent-1", source_id="faq-1",
                title="配送", child_text="满 99 元包邮", parent_text="配送政策",
                child_locator="c1", parent_locator="p1", data_version="faq-v1",
                dense_vector=(0.0,) * 1023,
            )

集成测试先以固定 1024 维单位向量写两条语料，查询向量同为第一维 1.0；断言 dense 与 BM25 均仅返回 data_version="faq-v1" 的 child，expand_parents() 返回完整 parent text 和原 child locator。

- [x] **Step 2: 运行 RED**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_hybrid.py -q

Expected: FAIL，因为 HybridIndexDocument 与实际 adapter 尚未实现。

- [x] **Step 3: 实现最小 adapter**

使用 custom schema，禁止 dynamic field：child_id 主键、parent_id/source_id/title/child_locator/parent_locator/data_version 为标量、child_text 为启用 `chinese` analyzer 的 `VARCHAR`、dense_vector 为 1024 维 FLOAT_VECTOR、sparse_vector 为 BM25 函数输出。建立 dense_vector/COSINE 与 sparse_vector/BM25 索引。

    async def search_dense(self, query: str, *, limit: int) -> Sequence[SearchHit]:
        return self._search(field="dense_vector", data=[self._embed_query(query)], limit=limit)

    async def search_sparse(self, query: str, *, limit: int) -> Sequence[SearchHit]:
        return self._search(field="sparse_vector", data=[query], limit=limit)

两路搜索都强制表达式 data_version == 当前版本，只返回统一 SearchHit。get(parent_id) 验证只有一个同版本 parent 文本。真实 BGE embedding 与 reranker 保持在独立 live adapter，默认测试只接受测试注入的 1024 维向量。

- [x] **Step 4: 运行 GREEN 与 L3 检索验证**

Run: RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_milvus_hybrid.py -q

Expected: PASS；固定向量、Milvus 内置 BM25、版本过滤、small-to-big 和 artifact 的 dense/sparse/fused 命中均可观察。

- [x] **Step 5: 记录 TDD 证据并提交**

    git add pyproject.toml uv.lock src/customer_service_agent/retrieval/hybrid.py \
      tests/unit/retrieval/test_hybrid.py tests/integration/milvus/test_milvus_hybrid.py \
      docs/tdd/records/m4-hybrid-pure.md docs/tdd/records/m4-milvus-integration.md
    git commit -m "feat: add Milvus hybrid retrieval adapter"

### Task 3: RAPTOR 确定性建树、集中摘要模板与候选写入

**Files:**
- Modify: src/customer_service_agent/retrieval/raptor.py
- Modify: src/customer_service_agent/retrieval/indexing.py
- Modify: tests/unit/retrieval/test_raptor.py
- Modify: tests/unit/retrieval/test_index_pipeline.py
- Create: tests/integration/milvus/test_milvus_raptor.py

**Interfaces:**
- Produces: RaptorBuildConfig(summary_levels=3, cluster_size=8, seed=42, prompt_version="raptor-summary-v1")。
- Produces: build_raptor_tree(leaves, *, config, summarizer) -> tuple[RaptorNode, ...]。
- Produces: MilvusRaptorStore(client, collection_name, data_version) implementing NodeStorePort。
- Consumes: PromptCatalog.render_raptor_summary() from the approved prompt-library plan。

- [x] **Step 1: 写失败测试，锁定层数、组大小与 prompt 版本**

    @pytest.mark.asyncio
    async def test_build_tree_creates_exactly_three_summary_levels() -> None:
        nodes = await build_raptor_tree(
            leaves(512),
            config=RaptorBuildConfig(),
            summarizer=DeterministicSummarizer(),
        )
        assert {node.level for node in nodes} == {0, 1, 2, 3}
        assert DeterministicSummarizer.calls[0].prompt_version == "raptor-summary-v1"

同时测试 9 个 sibling 分组为一个 9-child summary 而不是产生单子节点摘要；seed、cluster_size、summary_levels 任一偏离批准值均被拒绝。

- [x] **Step 2: 运行 RED**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_raptor.py tests/unit/retrieval/test_index_pipeline.py -q

Expected: FAIL，因为真实 build config 和离线 tree builder 尚未存在；不是模型调用失败。

- [x] **Step 3: 实现确定性建树与摘要 port**

    @dataclass(frozen=True)
    class RaptorBuildConfig:
        summary_levels: int = 3
        cluster_size: int = 8
        seed: int = 42
        prompt_version: str = "raptor-summary-v1"

每层先按 (document_id, source_id, node_id) 排序；使用预先注入的 1024 维叶向量计算 cosine，再以稳定 tie-breaker 执行层次聚类。每个 cluster 传给 SummarizerPort.summarize(texts, level=...)；DeepSeek 实现从 PromptCatalog 渲染 prompt，固定 model="deepseek-v4-flash"、temperature=0、max_tokens=300。默认单元测试只能用 DeterministicSummarizer。

将节点和节点向量写入独立 raptor_candidate_<run_id> collection，查询只读取匹配 data_version 的 root，随后使用既有 descend_hits()；artifact 保留 root、node path、leaf source 和 locator。

- [x] **Step 4: 运行 GREEN 与分层 L3 验证**

Run: RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_milvus_raptor.py -q

Expected: PASS；独立 raptor candidate collection 被创建、同 document/data_version 约束被保存、root 命中可下钻到实际 L0 来源，清理不会影响 hybrid candidate collection。

- [x] **Step 5: 记录 TDD 证据并提交**

    git add src/customer_service_agent/retrieval/raptor.py \
      src/customer_service_agent/retrieval/indexing.py tests/unit/retrieval/test_raptor.py \
      tests/unit/retrieval/test_index_pipeline.py tests/integration/milvus/test_milvus_raptor.py \
      docs/tdd/records/m4-raptor-pure.md docs/tdd/records/m4-milvus-integration.md
    git commit -m "feat: build traceable RAPTOR indexes in Milvus"

### Task 4: 候选索引发布、离线 CLI 和 live 验证隔离

**Files:**
- Modify: src/customer_service_agent/retrieval/indexing.py
- Modify: src/customer_service_agent/cli.py
- Modify: tests/unit/retrieval/test_index_pipeline.py
- Modify: tests/contract/test_cli_contract.py
- Create: tests/live/test_raptor_deepseek.py

**Interfaces:**
- Produces: MilvusIndexPublisher.publish(candidate: CandidateIndex) -> PublishedIndexRef。
- Produces: CLI command index-build --kind hybrid|raptor --data-version <value>。
- Produces: DeepSeekRaptorSummarizer only when RUN_LIVE_MODEL=1。

- [ ] **Step 1: 写失败测试，证明发布前检查与 CLI 只走离线端口**

    def test_cli_index_build_rejects_unknown_kind_before_client_creation() -> None:
        with pytest.raises(SystemExit, match="2"):
            parse_index_build_request(["index-build", "--kind", "unknown", "--data-version", "v1"])

    def test_publisher_keeps_previous_ref_when_milvus_smoke_fails() -> None:
        publisher = MilvusIndexPublisher(fake_client_with_failed_smoke())
        assert publisher.current_ref("hybrid") == "published/v1"
        with pytest.raises(IndexBuildFailed, match="validation"):
            publisher.publish(candidate("candidate/v2"))
        assert publisher.current_ref("hybrid") == "published/v1"

- [ ] **Step 2: 运行 RED**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_index_pipeline.py tests/contract/test_cli_contract.py -q

Expected: FAIL，因为 Milvus publisher 与 index-build CLI 解析尚未定义。

- [ ] **Step 3: 实现最小发布器和 CLI**

index-build 只接受 hybrid、raptor 与非空 data version，生成候选 collection 引用、执行固定 smoke query、写脱敏 IndexBuildReport，最后原子写入相应 kind 的当前 published ref。任何异常仅记稳定异常类型，不写 URI、token、模型响应或原始文档。在线查询工具不接受 build、publish 或 delete 参数。

tests/live/test_raptor_deepseek.py 在 RUN_LIVE_MODEL != "1" 时 skipped；启用时只发送版本化的短政策 fixture，并报告模型 ID、prompt version、数据版本和提交号，不输出请求/响应全文。

- [ ] **Step 4: 运行 GREEN、L3 回归与可选 live 命令**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_index_pipeline.py tests/contract/test_cli_contract.py -q

Run: RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus -q

Optional, only after explicit user cost approval: RUN_LIVE_MODEL=1 UV_CACHE_DIR=.uv-cache uv run pytest -m live_model tests/live/test_raptor_deepseek.py -q

Expected: 默认单元/契约与 L3 PASS；未获成本授权时 live_model 为 skipped，不能记为通过。

- [ ] **Step 5: 更新状态、TDD 记录并提交**

Update: docs/specs/020-retrieval-and-indexing.md、docs/superpowers/plans/2026-08-31-retrieval-and-indexing.md、docs/tdd/records/m4-index-publication.md。

将 M4 Milvus 实施状态标记为完成；继续明确区分 Milvus L3 已验证 与 DeepSeek/BGE live 尚未 opt-in。DG-004、DG-005 的批准记录已存在于 docs/decisions/002-m4-retrieval-parameters.md。

    git add src/customer_service_agent/retrieval/indexing.py src/customer_service_agent/cli.py \
      tests/unit/retrieval/test_index_pipeline.py tests/contract/test_cli_contract.py \
      tests/live/test_raptor_deepseek.py docs/specs/020-retrieval-and-indexing.md \
      docs/superpowers/plans/2026-08-31-retrieval-and-indexing.md \
      docs/tdd/records/m4-index-publication.md docs/tdd/records/m4-milvus-integration.md
    git commit -m "feat: publish versioned Milvus retrieval indexes"

## Requirement Coverage

| Requirement | Task |
| --- | --- |
| RET-HYB-001…004 | Task 2 |
| RET-RAP-001…004 | Task 3 |
| IDX-001…006 | Tasks 1 and 4 |
| CIT-001…004 | Tasks 2 and 3，复用已验证 artifact 契约 |
| TDD-001…006 | Every task: valid RED, GREEN, record and narrow commit |

## Plan Verification

    UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval tests/contract/test_retrieval_tools.py -q
    RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus -q
    UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q
    git diff --check

live-model and live-embedding commands are deliberately excluded from default verification.
