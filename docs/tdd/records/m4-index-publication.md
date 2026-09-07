# M4 索引发布状态机 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 计划：`docs/superpowers/plans/2026-09-02-m4-milvus-raptor-indexing.md` Task 4
- 生产文件：`src/customer_service_agent/retrieval/indexing.py`、`src/customer_service_agent/retrieval/raptor.py`、`src/customer_service_agent/cli.py`
- 测试文件：`tests/unit/retrieval/test_index_pipeline.py`、`tests/contract/test_cli_contract.py`、`tests/live/test_raptor_deepseek.py`

## RED

- 索引测试先于 `indexing.py` 编写；首次运行是模块缺失的 collection error，不计有效 RED。
- 添加无行为状态机骨架后得到 `3 failed`，覆盖验证失败不发布、成功严格状态顺序、构建异常报告不复制异常消息。
- 增加验证器抛出异常的测试后得到 `1 failed, 3 passed`；旧实现直接泄漏异常控制流且没有报告，随后补充脱敏失败报告与稳定 `IndexBuildFailed`。
- 检索工具契约先得到 `AttributeError`；实现四个固定工具名和仅 `query` 的 schema 后变为 GREEN。
- 新增发布器与 CLI 测试的第一次运行是 CLI 直接 import 缺失的 collection error，不计有效 RED；改为模块属性访问后得到 `4 failed, 5 passed`，均为 `MilvusIndexPublisher` 或 `parse_index_build_request` 缺失。
- 新增摘要器单元测试先得到 `AttributeError`，确认集中提示词 adapter 尚未实现；实现后转绿。

## GREEN 与边界

- `IndexPipeline` 只允许候选构建、验证、发布的顺序；验证失败或异常不会调用 publisher，也不会改变已有发布引用。
- `IndexBuildReport` 只记录 data version、输入哈希、计数、配置引用、耗时、smoke 标识与稳定失败类型；不保存异常消息、连接串、密钥或供应商原文。
- `create_retrieval_tools` 固定暴露 `search_product_faq`、`search_policy_raptor`、`search_commerce_graph`、`web_search`；模型输入仅有 `query`，有限模型内容与完整 artifact 分离。
- `MilvusIndexPublisher` 先执行候选 collection 的 smoke，再切换 `{kind}_current` alias；smoke 失败时不调用 alias 操作，也不改变原 published ref。
- `index-build` 解析只接受 `hybrid|raptor` 与非空 data version；它只产出离线请求，不读取环境变量或创建网络客户端。
- `DeepSeekRaptorSummarizer` 只通过 `PromptCatalog` 渲染 `raptor-summary-v1`，固定 `deepseek-v4-flash`、temperature 0、max_tokens 300；空模型输出被拒绝。
- 本切片单元/契约：`25 passed`；默认 live test：`1 skipped`；经用户授权的真实短政策摘要验证：`1 passed`，耗时约 3.39 秒。

## 未验证边界

- `MilvusIndexPublisher` 的 alias 边界在单元测试中通过具体 fake 验证；尚未把真实候选 collection 接到业务数据导入，也没有切换用户的既有 collection alias。
- DG-004、DG-005 的值见 `docs/decisions/002-m4-retrieval-parameters.md`。真实 BGE embedding/reranker 仍需 `live_embedding` opt-in；本次真实调用仅验证 DeepSeek 摘要。
- Tavily live、真实工具 service 装配和端到端离线数据导入仍需各自实施切片。
