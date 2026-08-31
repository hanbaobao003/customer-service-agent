# M2 检索证据契约 RED–GREEN 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 需求 ID：`RET-COM-001…004`
- 公开行为：模型只接收限长证据；完整命中保留在 artifact；伪引用和未知 DTO 字段被拒绝；无证据不得标记可回答。
- 测试文件：`tests/unit/retrieval/test_models.py`、`test_citations.py`、`tests/contract/test_retrieval_tools.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/retrieval/test_models.py tests/unit/retrieval/test_citations.py tests/contract/test_retrieval_tools.py -q`
- 退出码：`1`
- 失败摘要：5 个失败；证据未截断、空证据仍可回答、未知字段被接受、伪引用未拒绝、模型 content 泄露 artifact/citations。
- 参数 RED：`max_evidence_chars=0` 未被拒绝；补充最小测试后退出码 1。
- 有效性说明：测试正常执行到 DTO、引用和 ToolMessage 公开行为；不涉及 Milvus、Neo4j、模型或外网。

## GREEN

- 最小实现：严格冻结 Pydantic DTO；显式正数限长；截断 warning；引用完整性校验；`to_model_dict()` 与 `ToolMessage.artifact` 分离。
- 命令：与 RED 相同。
- 结果：`6 passed`。

## REFACTOR

- 改动：共享 `_StrictRetrievalModel` 只集中 `extra=forbid` 与冻结配置；所有实现保留在单一 `retrieval/models.py`。
- 相关回归：目标测试 6 个通过。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，41 个测试通过。

## 边界

- 已验证：有限模型证据、完整 artifact、引用完整性、无命中语义和未知字段拒绝。
- 未验证：Milvus/RAPTOR/Neo4j/Tavily 真实召回、默认证据长度、Hit@5 和检索延迟；生产长度值仍由后续显式配置提供。
- 提交：`feat: define traceable retrieval evidence contracts`（本记录随该提交保存）。
