# M2 订单 operation RED–GREEN 记录

- Spec：`docs/specs/030-orders-and-sql.md`
- 需求 ID：`ORD-HITL-001…004`、`ORD-IDEM-001…003`
- 公开行为：写操作预览绑定可信上下文并保存参数哈希；拒绝、过期、越权或被篡改的 operation 不执行；重复批准返回首次结果。
- 测试文件：`tests/unit/commerce/test_order_operations.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_operations.py -q`
- 退出码：`1`
- 失败摘要：6 个测试失败，全部运行到尚未实现的预览服务；不存在导入或外部依赖错误。
- 有效性说明：RED 覆盖预览绑定、参数篡改、重复批准、拒绝后批准以及客户/线程隔离。

## GREEN

- 最小实现：固定键序 JSON 规范化、SHA-256 哈希、常量时间比较、pending/rejected/executed/expired 状态处理，以及首次结果复用。
- 命令：与 RED 相同；补强哈希输入和过期状态后再次运行。
- 结果：`11 passed`。

## REFACTOR

- 改动：operation DTO、端口和领域核心保留在单一 `commerce/orders.py`，未增加自动重试或生产内存 adapter。
- 订单域回归：状态机、读取、operation 和工具契约共 `29 passed`。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，`70 passed`。

## 边界

- 已验证：哈希绑定工具、客户、线程、interrupt、参数和版本；顺序重复批准只执行一次；拒绝和过期不可恢复。
- 未验证：并发批准、进程崩溃恢复，以及领域写入/operation 结果/审计的 PostgreSQL 单事务；这些属于 Task 4。
- 提交：`feat: add immutable order operation approvals`（本记录随该提交保存）。
