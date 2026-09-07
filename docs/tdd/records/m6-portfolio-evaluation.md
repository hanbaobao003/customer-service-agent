# M6 作品集评测与 LangSmith TDD 记录

**范围：** Spec 050 第 1.1 节的作品集最小验证档案，不替代完整 60 条生产质量门禁。

## 1. 工具路由评分器

- **RED：** `uv run pytest tests/unit/quality/test_portfolio_eval.py -q`
- **失败原因：** `ModuleNotFoundError: No module named 'customer_service_agent.quality'`，评分器模块尚不存在。
- **GREEN：** 新增 `score_selected_tool`，相同命令通过。

## 2. 固定数据集

- **RED：** 同一测试在导入 `load_portfolio_cases` 时失败，原因是该函数尚不存在。
- **GREEN：** 新增严格 JSONL 加载器与 `portfolio_v1.jsonl`；六个 group 恰好覆盖 route、三类 RAG、SQL、订单，重复 ID 或缺组会拒绝。

## 3. LangSmith 适配

- **RED：** 同一测试在导入 `sync_portfolio_dataset`、`run_portfolio_experiment` 时分别失败，原因是两个适配函数尚不存在。
- **GREEN：** 单元测试通过内存 Client 验证已有数据集不重复上传、实验固定使用 `wang-agent-portfolio` 前缀、串行执行与确定性 `tool_route` 评分器。

## 4. 验证边界

- 本地：`231 passed, 20 deselected`（unit/contract），`ruff check src tests` 和 `git diff --check` 通过。
- Docker：PostgreSQL、Milvus、Neo4j 完整回归与本地测试合计 `248 passed, 3 deselected`。
- Live：使用 `.env` 执行 `RUN_LIVE_LANGSMITH_TESTS=1 RUN_LIVE_MODEL_TESTS=1`，真实 LangSmith 测试通过；DeepSeek live_model 为 `2 passed, 1 skipped`，跳过项未开启 LangSmith 开关时按设计跳过。
- live 测试在配置齐备时只上传六条模拟样本，并复用真实 DeepSeek `echo_test` 工具调用生成 trace；不上传密钥、真实客户数据或 chain-of-thought。

## 5. LangSmith evaluator 参数兼容性修复

- **真实 RED：** 启用 `.env` 后，`test_langsmith_records_portfolio_experiment_and_model_trace` 在 LangSmith 注册 evaluator 时失败；SDK 报告 `_inputs` 不是受支持的参数名。
- **最小本地 RED：** 新增签名测试，断言参数依次为 `inputs`、`outputs`、`reference_outputs`；修复前第一项为 `_inputs`，断言失败。
- **GREEN：** 仅将参数改为 `inputs`，保持评分逻辑不变；本地 M6 5 项测试通过，真实 LangSmith live 测试通过。
