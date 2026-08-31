# M2 长期记忆 DTO 与端口 RED–GREEN 记录

- Spec：`docs/specs/040-long-term-memory.md`
- 需求 ID：`MEM-DATA-001…004`、`MEM-LIST-002`
- 公开行为：应用 DTO 隔离供应商字段；模型请求无客户身份；verified fact 来源必须可审计。
- 测试文件：`tests/unit/memory/test_memory_models.py`、`tests/contract/test_memory_tools.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_memory_models.py tests/contract/test_memory_tools.py -q`
- 退出码：`1`
- 失败摘要：摘要映射未实现，verified tool 来源未强制工具名和验证时间；其余 schema 安全断言已执行。

## GREEN

- 最小实现：冻结严格 DTO、安全摘要、来源校验、四个客户作用域存储端口和不含 `customer_id` 的请求 DTO。
- 结果：目标测试 `7 passed`；全量快速测试 `86 passed`。

## 调试记录

- 现象：新增 `test_models.py` 与检索目录同名测试在 pytest 默认导入模式下冲突。
- 根因：两个非包目录的文件都注册为顶层 `test_models` 模块。
- 修复：仅将新文件改为 `test_memory_models.py`；未修改全局导入模式，因为验证发现 `importlib` 模式会破坏现有 `conftest` 导入约定。

## 边界

- 已验证：DTO 严格性、模型 schema 客户隔离、内部向量/分数不进入摘要、verified 来源证据字段。
- 未验证：服务操作与真实 Mem0 响应映射；分别属于 Tasks 3 和 5。
- 提交：`feat: define isolated memory contracts`（本记录随该提交保存）。
