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

- 本地：`230 passed, 20 deselected`（unit/contract），`ruff check src tests` 和 `git diff --check` 通过。
- Live：`RUN_LIVE_LANGSMITH_TESTS=1 RUN_LIVE_MODEL_TESTS=1` 已执行；当前进程缺少 LangSmith 或 DeepSeek 配置，按设计 `skipped`，没有把它记为通过。
- live 测试在配置齐备时只上传六条模拟样本，并复用真实 DeepSeek `echo_test` 工具调用生成 trace；不上传密钥、真实客户数据或 chain-of-thought。
