# M5 Agent 模型与检查点决策

**状态：** 已批准并完成当前验证范围

**日期：** 2026-09-02

**关联决策门：** DG-002

## 已批准值

| 项目 | 值 | 影响范围 |
| --- | --- | --- |
| Agent 模型 | `deepseek-v4-flash` | 单 LangChain `create_agent` 的默认模型 |
| Agent 模型适配 | `langchain-openai==1.6.0` 的 `ChatOpenAI` | 使用现有 OpenAI-compatible DeepSeek endpoint；凭据仅在组装边界注入 |
| 持久检查点 | `langgraph-checkpoint-postgres==3.1.2` | PostgreSQL `AsyncPostgresSaver` 的 setup、暂停与同线程恢复 |

## 已验证证据

- `RUN_LIVE_MODEL_TESTS=1` 的真实模型测试通过：`deepseek-v4-flash` 可绑定并返回固定的 `echo_test(text="ping")` 工具调用。测试不记录凭据、请求全文或响应全文。
- PostgreSQL 集成测试通过：HITL 写工具暂停后关闭 saver，重建 saver 与 Agent，再以同一 `thread_id` 批准恢复；工具只执行一次。
- 单元测试通过：写工具在批准前不执行，公开 `approval.required` 事件不泄露工具原始参数，恢复前校验检查点中的 interrupt ID。

## 保留边界

- 本决策不批准或验证 DeepSeek 的 chain-of-thought 展示、推理模型兼容层、结构化输出或生产吞吐/成本。
- `ChatOpenAI` 只用于本首版的非推理 `deepseek-v4-flash`；若切换 reasoning 模型，须重新评审支持 `reasoning_content` 的适配方案。
- 真实模型调用仍必须使用 `RUN_LIVE_MODEL_TESTS=1` 明确 opt-in；默认单元、契约和 PostgreSQL 集成测试使用 scripted model。
