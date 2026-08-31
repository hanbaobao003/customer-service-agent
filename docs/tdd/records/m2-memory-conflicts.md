# M2 长期记忆冲突 RED–GREEN 记录

- Spec：`docs/specs/040-long-term-memory.md`
- 需求 ID：`MEM-CONFLICT-001…003`
- 公开行为：只在同客户、同类型、同类别内比较；重复刷新来源时间；不同内容要求明确替换；失败保留旧记录。
- 测试文件：`tests/unit/memory/test_conflicts.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_conflicts.py -q`
- 退出码：`1`
- 失败摘要：5 个失败，分别证明检测器缺失、冲突被覆盖、重复生成新 ID、替换未走原子端口和失败未保持旧记录。

## GREEN

- 最小实现：`ConflictDetector`、安全 `MemoryConflict` 摘要、重复刷新、明确替换词门控和原子 store `replace` 契约。
- 结果：目标 `5 passed`；长期记忆域与契约 `26 passed`；全量快速测试 `96 passed`。

## 计划修正

- 原计划的独立 delete + add 无法同时保证任何失败保留旧记录。
- M2 改为原子 `replace` 存储端口；M3 适配器必须在数据库事务中实现。成功后仍分别发出删除和创建审计语义。

## 边界

- 已验证：客户/类别边界、重复不新增展示项、未授权冲突不覆盖、明确替换和替换失败保持旧记录。
- 未验证：真实 Mem0 并发事务与审计原子性；属于 M3 Task 5。
- 提交：`feat: preserve memory conflicts until explicit replacement`（本记录随该提交保存）。
