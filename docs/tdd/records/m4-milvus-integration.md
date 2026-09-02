# M4 Milvus 集成资源隔离 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- Plan：`docs/superpowers/plans/2026-09-02-m4-milvus-raptor-indexing.md`
- 生产文件：`src/customer_service_agent/retrieval/indexing.py`、`src/customer_service_agent/retrieval/hybrid.py`
- 测试文件：`tests/integration/milvus/test_resource_safety.py`、`tests/integration/milvus/test_milvus_hybrid.py`

## RED

- 初次测试直接导入尚未存在的函数，得到 collection `ImportError`；按 TDD 规则不计为有效 RED，已改为导入现有 `indexing` 模块。
- 修正后运行 `UV_CACHE_DIR=.uv-cache uv run pytest tests/integration/milvus/test_resource_safety.py -q`，得到 `3 failed`，原因为资源函数公开入口缺失，非 Docker 或网络错误。
- 首次完整 unit/contract 回归发现嵌套 `tests/integration/milvus/conftest.py` 遮蔽根 `conftest` 模块，导致质量测试 collection error。根因确认后，fixture 移入既有 `tests/conftest.py`；完整回归恢复为绿色。
- 新增 1024 维约束测试后，`HybridIndexDocument` 尚不存在，得到预期的 `AttributeError`；实现后该测试转绿。

## GREEN 与 REFACTOR

- `make_milvus_run_resources()` 只接受 12 位小写十六进制 run ID，生成唯一的 `wang_agent_it_<run_id>` database、两个候选 collection 名及两个 run-scoped alias 名。
- `validate_milvus_owned_database()` 要求名称同时符合安全前缀与当前 run ID；`default` 或其他 run 的 database 一律拒绝。
- PyMilvus 3.0.1 已作为直接依赖锁定。测试 fixture 位于根 `tests/conftest.py`，仅在 `RUN_MILVUS_INTEGRATION=1` 时连接 `MILVUS_URI`；测试结束先校验归属、删除本次 database，再确认数据库不再存在。
- 无重构：资源命名和验证留在既有 `retrieval/indexing.py`，测试生命周期留在 Milvus 集成 fixture。
- hybrid adapter 使用 custom schema（dynamic field 关闭），由 `child_text` 的内置 BM25 函数生成 sparse vector；dense 使用 COSINE，所有读取均强制 `data_version` filter，`get()` 对同一父文档的冲突值拒绝返回。
- 当前 Docker Milvus 未启用 StorageV3：首次 `TEXT` schema 真实创建失败，报错要求启用 `common.storage.useLoonFFI`。按 Milvus BM25 的 `VARCHAR` 支持改为带 analyzer 的 `VARCHAR` 后，schema 成功创建。
- 默认 analyzer 不能召回中文词；用户批准仅支持中文字符检索后，切换为内置 `chinese` analyzer。写入后的首次查询为空，诊断确认是可见性边界，adapter 在 insert 后显式 `flush()`；随后 dense、BM25、版本过滤与 parent 查询均通过。

## 验证

- 资源命名与索引状态机回归：`7 passed`。
- Docker L3：`RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_resource_safety.py -q`，结果 `4 passed`。
- 完整 unit/contract 回归：`192 passed, 13 deselected`。
- hybrid Docker L3：`RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_milvus_hybrid.py -q`，结果 `1 passed`；连同资源隔离测试为 `5 passed`。
- 当前完整 unit/contract 回归：`193 passed, 14 deselected`。
- 已验证固定 1024 维测试向量下的 collection 创建、写入、flush、dense 搜索、Milvus BM25、版本过滤与 parent 读取。尚未调用 BGE embedding、reranker 或 DeepSeek；它们仍分别需要 `live_embedding`、`live_model` opt-in。
