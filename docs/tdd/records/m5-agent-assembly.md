# M5 Agent 组装与持久 HITL TDD 记录

- Spec：`docs/specs/010-agent-api-and-middleware.md`
- Plan：`docs/superpowers/plans/2026-08-31-agent-api-and-middleware.md` Task 5
- 生产文件：`src/customer_service_agent/agent_api/service.py`、`src/customer_service_agent/agent_api/middleware.py`
- 测试文件：`tests/unit/agent_api/test_middleware.py`、`tests/unit/agent_api/test_handoff.py`、`tests/integration/postgres/test_checkpoint_resume.py`、`tests/live/test_agent_deepseek.py`

## RED

- 异步治理中间件、Agent 工厂、PostgreSQL runtime adapter 和 HITL resume 的测试分别先因公开对象缺失而失败；随后以最小实现转绿。
- DeepSeek 模型工厂测试先失败于 `service.ChatOpenAI` 不存在；添加官方 OpenAI-compatible LangChain adapter 与固定 `deepseek-v4-flash` 工厂后转绿。
- 图流适配器测试先失败于 `LangGraphAgentRunner` 不存在；最小 adapter 将真实模型/工具 update 映射为公开事件后转绿。
- HITL 公开事件测试先失败：首次暂停只输出 `tool.started`，没有 `approval.required`。实现后公开事件包含 interrupt ID、操作 ID 和固定 `approve/reject`，但省略原始工具参数。
- 同线程恢复测试先失败于 runner 缺少 `resume`；补充检查点 interrupt ID 校验和 `Command(resume={"decisions": [...]})` 后转绿。

## GREEN 与验证边界

- `GovernanceMiddleware.awrap_tool_call` 等待异步 handler；只读工具可进行有限的连接/超时重试，写工具零重试；工具上限受控。
- `build_customer_service_agent` 只创建一个固定名为 `customer_service_agent` 的 Agent。`build_deepseek_agent_model` 只接受运行时注入的 key 和 base URL，不读取、写入或输出密钥。
- `LangGraphAgentRunner` 将 tool call、ToolMessage、最终 AI 文本和 HITL interrupt 映射为固定 `AppEvent`。恢复 SSE 流会再次收到 `tool.started`，这是 LangGraph 对待执行 action 的重放；工具仍只执行一次。
- 真实 PostgreSQL 集成验证了关闭并重建 `AsyncPostgresSaver` 后的 HITL 恢复；其测试数据库由既有隔离 fixture 创建和清理，不能替代生产数据库运维验证。
- 真实 DeepSeek 验证仅确认一次固定的无业务数据 tool call；不证明完整业务工具选择、流式延迟、成本或模型质量。

## 当前未完成边界

- 可信上下文/审计、模型调用上限、会话摘要和标准化 `handoff.required` 尚未拆分为计划所列的独立中间件。
- `INTERRUPT_NOT_FOUND`、重复 decision 的持久幂等结果和完整 API HTTP 错误映射仍待后续 M5 切片实现。
