# M2 API 与 CLI 适配器 RED–GREEN 记录

- Spec：`docs/specs/010-agent-api-and-middleware.md`
- 需求 ID：`CTX-005, API-001, HITL-002, HITL-005, API-HEALTH-001…002`
- 公开行为：HTTP 从可信 provider 取得客户身份；消息和 decision 使用 SSE；CLI 身份不拼入消息；liveness 不访问依赖；readiness 只返回布尔状态。
- 测试文件：`tests/contract/test_message_api.py`、`test_decision_api.py`、`test_health_api.py`、`test_cli_contract.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_customer_service.py::test_resume_decision_uses_same_thread_authorization_and_lock tests/contract/test_message_api.py tests/contract/test_decision_api.py tests/contract/test_health_api.py tests/contract/test_cli_contract.py -q`
- 退出码：`1`
- 失败摘要：10 个失败；应用服务无 resume、HTTP 路由均为 404、CLI 返回占位身份。
- 输入上限 RED：用户批准 4,000 Unicode 字符后，4,001 字符测试得到 200 而非 422；4,000 字符边界通过。
- 有效性说明：使用进程内 `httpx.ASGITransport`，失败发生在公开路由、schema 或适配行为；没有监听端口或访问外网。

## GREEN

- 最小实现：增加 FastAPI 消息、decision、健康路由；严格 Pydantic 请求 schema；可信上下文 provider；CLI `argparse` 适配；应用服务 resume 使用相同线程绑定和运行锁。
- 输入上限：去除首尾空白后最多 4,000 个 Unicode 字符，超过返回 422 且不调用应用服务。
- 命令：与 RED 相同。
- 结果：`12 passed`。

## REFACTOR

- 改动：消息与 resume 复用应用服务的绑定和锁私有流程；测试从已弃用兼容层切换到直接 ASGI transport，避免屏蔽警告。
- 相关回归：`UV_CACHE_DIR=.uv-cache uv run pytest -m contract -q`，17 个测试通过。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，35 个测试通过。

## 边界

- 已验证：请求校验、可信身份来源、SSE response、decision 枚举/reject reason、CLI 上下文及健康响应脱敏。
- 未验证：重复 decision 的 PostgreSQL/checkpointer 幂等、真实断线、跨进程运行锁、外部依赖 readiness 探针和生产认证。
- 提交：`feat: add streaming API and decision contracts`（本记录随该提交保存）。
