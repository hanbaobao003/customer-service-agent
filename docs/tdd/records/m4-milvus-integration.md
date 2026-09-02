# M4 Milvus 集成资源隔离 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- Plan：`docs/superpowers/plans/2026-09-02-m4-milvus-raptor-indexing.md`
- 生产文件：`src/customer_service_agent/retrieval/indexing.py`
- 测试文件：`tests/integration/milvus/test_resource_safety.py`

## RED

- 初次测试直接导入尚未存在的函数，得到 collection `ImportError`；按 TDD 规则不计为有效 RED，已改为导入现有 `indexing` 模块。
- 修正后运行 `UV_CACHE_DIR=.uv-cache uv run pytest tests/integration/milvus/test_resource_safety.py -q`，得到 `3 failed`，原因为资源函数公开入口缺失，非 Docker 或网络错误。
- 首次完整 unit/contract 回归发现嵌套 `tests/integration/milvus/conftest.py` 遮蔽根 `conftest` 模块，导致质量测试 collection error。根因确认后，fixture 移入既有 `tests/conftest.py`；完整回归恢复为绿色。

## GREEN 与 REFACTOR

- `make_milvus_run_resources()` 只接受 12 位小写十六进制 run ID，生成唯一的 `wang_agent_it_<run_id>` database、两个候选 collection 名及两个 run-scoped alias 名。
- `validate_milvus_owned_database()` 要求名称同时符合安全前缀与当前 run ID；`default` 或其他 run 的 database 一律拒绝。
- PyMilvus 3.0.1 已作为直接依赖锁定。测试 fixture 位于根 `tests/conftest.py`，仅在 `RUN_MILVUS_INTEGRATION=1` 时连接 `MILVUS_URI`；测试结束先校验归属、删除本次 database，再确认数据库不再存在。
- 无重构：资源命名和验证留在既有 `retrieval/indexing.py`，测试生命周期留在 Milvus 集成 fixture。

## 验证

- 资源命名与索引状态机回归：`7 passed`。
- Docker L3：`RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_resource_safety.py -q`，结果 `4 passed`。
- 完整 unit/contract 回归：`192 passed, 13 deselected`。
- 本切片不创建 hybrid/RAPTOR schema，不写入文档，不执行 BM25、dense 检索、BGE、reranker 或 DeepSeek。
