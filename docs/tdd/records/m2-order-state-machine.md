# M2 订单状态机 RED–GREEN 记录

- Spec：`docs/specs/030-orders-and-sql.md`
- 需求 ID：`ORD-STATE-001`、`ORD-STATE-002`
- 公开行为：只允许显式订单状态转换；成功返回版本加一的新对象；失败返回稳定业务错误且原对象不变。
- 测试文件：`tests/unit/commerce/test_order_state_machine.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_state_machine.py -q`
- 退出码：`1`
- 失败摘要：13 个场景中 8 个允许转换失败；测试已正常运行到尚未实现的领域函数，5 个禁止转换通过。
- 有效性说明：失败来自缺少转换矩阵，不是导入、依赖或外部服务错误。

## GREEN

- 最小实现：不可变订单 DTO、显式允许转换矩阵和不可变 `model_copy`；成功版本加一。
- 命令：与 RED 相同。
- 结果：`13 passed`。

## REFACTOR

- 改动：未增加 repository、工具或数据库抽象；后台 `returned/refunded` 状态不开放给客服转换函数。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，`54 passed`。

## 边界

- 已验证：客服可达状态转换、发货后禁止取消、非法跳转、版本递增和失败不可变性。
- 未验证：PostgreSQL 事务、乐观锁和审计原子性；这些属于后续 Tasks 4 和集成测试。
- 提交：`feat: define explicit order lifecycle`（本记录随该提交保存）。
