# M4 索引发布状态机 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 计划：检索与索引 Task 5
- 生产文件：`src/customer_service_agent/retrieval/indexing.py`、`src/customer_service_agent/retrieval/tools.py`
- 测试文件：`tests/unit/retrieval/test_index_pipeline.py`、`tests/contract/test_retrieval_tools.py`

## RED

- 索引测试先于 `indexing.py` 编写；首次运行是模块缺失的 collection error，不计有效 RED。
- 添加无行为状态机骨架后得到 `3 failed`，覆盖验证失败不发布、成功严格状态顺序、构建异常报告不复制异常消息。
- 增加验证器抛出异常的测试后得到 `1 failed, 3 passed`；旧实现直接泄漏异常控制流且没有报告，随后补充脱敏失败报告与稳定 `IndexBuildFailed`。
- 检索工具契约先得到 `AttributeError`；实现四个固定工具名和仅 `query` 的 schema 后变为 GREEN。

## GREEN 与边界

- `IndexPipeline` 只允许候选构建、验证、发布的顺序；验证失败或异常不会调用 publisher，也不会改变已有发布引用。
- `IndexBuildReport` 只记录 data version、输入哈希、计数、配置引用、耗时、smoke 标识与稳定失败类型；不保存异常消息、连接串、密钥或供应商原文。
- `create_retrieval_tools` 固定暴露 `search_product_faq`、`search_policy_raptor`、`search_commerce_graph`、`web_search`；模型输入仅有 `query`，有限模型内容与完整 artifact 分离。
- Task 5 单元与工具契约：`6 passed`；检索单元：`49 passed`。
- 包含 PostgreSQL、Neo4j 集成层的全量回归：`195 passed`。

## 未验证边界

- 当前 builder、validator、publisher 均为端口；未创建 Milvus database/collection、候选索引、版本别名或真实发布。
- DG-004、DG-005 未批准，故不能配置或发布常规 RAG、RAPTOR 的真实检索参数和索引。
- Tavily live、Milvus L3 与真实工具 service 装配仍需相应 explicit opt-in/决策门。
