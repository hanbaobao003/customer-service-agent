# M1 Agent 上下文 RED–GREEN 记录

- Spec：`docs/specs/010-agent-api-and-middleware.md`
- 需求 ID：`CTX-001, ERR-001, TDD-001…004`
- 公开行为：可信适配器构造的运行时上下文拒绝空身份标识、规范化标识并保持不可变；公开错误不泄露内部异常。
- 测试文件：`tests/unit/agent_api/test_context.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_context.py -q`
- 退出码：`1`
- 失败摘要：5 个测试失败；`RuntimeContext.trusted` 尚不存在，`ServiceError` 尚不接受受控字段。
- 有效性说明：pytest 完成依赖加载、收集和测试执行；失败由目标公开行为缺失造成，不是导入、语法、连接或 fixture 错误。

## GREEN

- 最小实现：增加不可变 `RuntimeContext` 的可信构造和标识校验；增加带日志专用 `cause` 的 `ServiceError.to_public_dict()`。
- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_context.py -q`
- 结果：`5 passed`。

## REFACTOR

- 改动：无重构；仅修正 `slots` 数据类初始化 `Exception` 的方式。
- 相关回归：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_context.py -q`，5 个测试通过。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，5 个测试通过。

## 边界

- 已验证：纯内存运行时上下文校验、不可变性和公开错误序列化。
- 未验证：FastAPI/CLI 身份注入、PostgreSQL 线程绑定、Docker、真实模型和外部服务。
- 提交：`test: establish trusted runtime context`（本记录随该提交保存）。
