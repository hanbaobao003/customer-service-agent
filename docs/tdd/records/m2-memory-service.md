# M2 长期记忆服务 RED–GREEN 记录

- Spec：`docs/specs/040-long-term-memory.md`
- 需求 ID：`MEM-WRITE-001…005`、`MEM-RECALL-001…005`、`MEM-LIST-001…002`、`MEM-DELETE-001…004`
- 公开行为：按策略、工具证据、归一化、客户作用域存储的固定顺序保存；召回、列出和删除均使用可信客户身份。
- 测试文件：`tests/unit/memory/test_service.py`、`tests/contract/test_memory_tools.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_service.py -q`
- 退出码：`1`
- 失败摘要：5 个服务场景全部运行到未实现方法并失败，覆盖普通对话、工具证据、明确保存、跨客户删除和召回降级。

## GREEN

- 最小实现：`MemoryService.remember/recall/list/forget`、工具证据端口、无正文审计端口、单事实空白归一化和非指令召回上下文。
- 目标与契约：`9 passed`。
- 全量快速测试：`91 passed`。

## 边界

- 已验证：普通对话不写入、verified fact 缺证据拒绝、客户隔离列表/删除、重复删除不可观察、存储不可用安全降级。
- 未验证：Mem0/pgvector 持久化、跨进程事务和冲突替换；分别属于 M3 Task 5 与下一 Task 4。
- 提交：`feat: add explicit customer-scoped memory operations`（本记录随该提交保存）。
