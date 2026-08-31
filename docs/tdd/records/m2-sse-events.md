# M2 SSE 事件 RED–GREEN 记录

- Spec：`docs/specs/010-agent-api-and-middleware.md`
- 需求 ID：`SSE-001…004`
- 公开行为：应用事件序号从 1 递增；固定终止事件结束流；敏感 payload 被拒绝；SSE 帧包含序号 id、事件名和公开 envelope。
- 测试文件：`tests/unit/agent_api/test_events.py`、`tests/contract/test_sse_contract.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_events.py tests/contract/test_sse_contract.py -q`
- 退出码：`1`
- 失败摘要：9 个失败；序号恒为 0、终止后仍可 emit、编码为空、敏感字段未拒绝、严格 envelope 未实现。
- 有效性说明：pytest 完成收集和执行；所有失败都发生在目标事件或序列化行为，不涉及外部服务。

## GREEN

- 最小实现：在 `shared/models.py` 增加八种 `EventType`、严格 `AppEvent` 和 `EventSequencer`；在 `agent_api/api.py` 增加单行 JSON SSE 编码。
- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_events.py tests/contract/test_sse_contract.py -q`
- 结果：`9 passed`。

## REFACTOR

- 改动：`CustomerService` 与 `AgentRunner` 的返回注解从通用对象收窄为 `AppEvent`；未拆分新模块。
- 相关回归：目标测试 9 个通过。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，23 个测试通过。

## 边界

- 已验证：应用层事件序号、终止语义、递归敏感键拒绝、严格 envelope 和 SSE 编码。
- 计划修正：子计划原“只输出 event/data”与 Spec 的 SSE id 要求冲突；按主计划以 Spec 为准，已修正为 `id/event/data`。
- 未验证：FastAPI 流式响应、客户端断开、真实 LangChain stream 和多进程事件生成。
- 提交：`feat: define stable customer service event stream`（本记录随该提交保存）。
