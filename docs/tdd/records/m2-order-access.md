# M2 订单读取边界 RED–GREEN 记录

- Spec：`docs/specs/030-orders-and-sql.md`
- 需求 ID：`ORD-READ-001…003`、`ORD-AUTH-001`、`ORD-AUTH-002`、`ORD-AUTH-004`
- 公开行为：订单读取强制使用可信客户身份；缺失与越权返回相同错误；模型只看到订单 ID 参数和必要摘要。
- 测试文件：`tests/unit/commerce/test_order_access.py`、`tests/contract/test_order_tools.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/commerce/test_order_access.py tests/contract/test_order_tools.py -q`
- 退出码：`1`
- 失败摘要：5 个失败；4 个来自未实现的读取服务，1 个暴露测试误用了 LangChain 内部 `args_schema`。
- 测试修正：模型实际可见的是 `tool_call_schema`；修正后仍由 4 个服务行为失败维持有效 RED。
- 有效性说明：服务已可导入，失败发生在客户作用域查询、摘要和脱敏行为，不涉及网络或外部服务。

## GREEN

- 最小实现：客户作用域 repository 协议、可信上下文读取、统一 `ORDER_NOT_FOUND`、查询时间摘要、电话/地址/姓名脱敏 artifact，以及 `ToolRuntime` 注入工具。
- 命令：与 RED 相同。
- 结果：`5 passed`。

## REFACTOR

- 改动：只保留一个 `orders.py`；未添加生产内存 repository 或额外 mapper 文件。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，`59 passed`。

## 边界

- 已验证：模型 schema 无 `customer_id`、repository 明确接收客户 ID、越权不可观察、状态/版本/查询时间和 artifact 脱敏。
- 未验证：PostgreSQL 行级查询与安全审计；由后续 repository 集成任务验证。
- 提交：`feat: isolate order reads by trusted customer`（本记录随该提交保存）。
