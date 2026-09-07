# MVP 验证报告（2026-09-05）

## 范围

本报告覆盖作品集 MVP 的本地 seed、浏览器应用契约、并发工具治理与一次真实 DeepSeek FAQ 调用。未把结果外推为生产容量、真实支付或完整评测门槛。

## 已验证结果

| 层级 | 命令 | 结果 | 证据边界 |
|---|---|---|---|
| 离线中间件 | `uv run pytest tests/unit/agent_api/test_middleware.py -q` | 15 passed | 验证并发工具批次不会争用预算状态。 |
| MVP 应用与页面 | `uv run pytest tests/contract/test_mvp_app.py tests/contract/test_mvp_page.py -q` | 4 passed | 验证离线组合、13 工具名称和页面区域；不连接外部服务。 |
| 本地 seed | `PYTHONPATH=src uv run --env-file ... python -m customer_service_agent.mvp.seed` | 成功 | 两个订单、FAQ、RAPTOR、图谱和一条 Mem0 偏好已幂等就绪。 |
| 真实模型 | `RUN_LIVE_MODEL_TESTS=1 ... pytest tests/live/test_mvp_deepseek.py -q -rs` | 1 passed，约 6.35 秒 | 验证 DeepSeek 发起 FAQ 工具调用并完成 SSE；不代表全部工具的模型路由质量。 |

## 已知边界

- 真实 web 搜索仍只有安全的不可用返回；未执行 Tavily live 调用。
- 浏览器 UI 是单页 MVP，不包含登录、支付、生产审计或部署配置。
- Mem0 数据保存于本地 PostgreSQL `wang_agent_mvp_mem0`，不会显示在 Mem0 Cloud 控制台。
- 使用全新 `thread_id` 进行 live 验收；若中断发生在工具调用与结果之间，旧会话可能保留不完整检查点，不应直接用于重跑。
