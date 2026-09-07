# M2 Agent 应用服务 RED–GREEN 记录

- Spec：`docs/specs/010-agent-api-and-middleware.md`
- 需求 ID：`CTX-003, API-001…004`
- 公开行为：线程首次绑定客户；其他客户不可访问；同线程只允许一个活动运行；不同线程可并行；客户端取消释放锁。
- 测试文件：`tests/unit/agent_api/test_thread_access.py`、`tests/unit/agent_api/test_customer_service.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_thread_access.py tests/unit/agent_api/test_customer_service.py -q`
- 退出码：`1`
- 失败摘要：3 个失败、1 个通过；客户不匹配未拒绝、同线程第二次运行超时而非 `THREAD_BUSY`、空消息到达 Agent。
- 有效性说明：asyncio 守卫缺陷修复后重新运行；pytest 正常执行到目标行为，失败不是导入、fixture、外部连接或事件循环错误。
- 取消 RED：`test_client_cancellation_releases_thread_lock` 退出码 1；取消后锁未释放，第二次运行得到 `THREAD_BUSY`。

## GREEN

- 最小实现：在 `agent_api/service.py` 内定义窄端口、内存绑定与运行锁；`CustomerService` 按消息校验、客户绑定、运行锁、Agent stream 的固定顺序执行，并把内部边界异常映射为稳定 `ServiceError`。
- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_thread_access.py tests/unit/agent_api/test_customer_service.py -q`
- 结果：`5 passed`。

## REFACTOR

- 改动：无文件拆分；锁释放收敛到异步上下文管理器的 `finally`。
- 相关回归：目标测试 5 个通过。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m unit -q`，14 个测试通过。

## 边界

- 已验证：单进程内存绑定、同线程互斥、不同线程并行、取消释放和 Agent 调用前拒绝。
- 未验证：PostgreSQL 持久绑定、多进程互斥、服务重启、真实 Agent 和检查点恢复。
- 提交：`feat: enforce thread ownership and single active run`（本记录随该提交保存）。
