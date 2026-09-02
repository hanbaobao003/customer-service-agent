# M5 订单预览工具 TDD 记录

- Spec：`docs/specs/030-orders-and-sql.md` 第 5.3 节
- 生产文件：`src/customer_service_agent/commerce/orders.py`
- 测试文件：`tests/contract/test_order_write_tools.py`

## RED

- 工具工厂的首次导入失败仅因符号不存在，不计有效 RED。
- 改为模块级调用后，契约测试以 `AttributeError` 失败，确认 `create_order_preview_tools` 尚未实现。

## GREEN 与边界

- 工厂固定提供 `create_order`、`update_order_contact`、`cancel_order`、`request_return` 四个工具；模型 schema 不含 `customer_id` 或 approval ID。
- 每个工具调用既有 `OrderCommandService.preview_*`，由服务端 generator 产生 approval ID；工具不执行创建、修改、取消或退货申请。
- 模型只获得 operation ID、工具名和审批等待状态；完整 operation、哈希和规范化参数位于 artifact。
- 原子执行、哈希验证与重复批准幂等继续由已验证的 `OperationService.execute_approved` 负责。Agent API/SSE 的 operation approval 编排尚未实现，不能声称当前 Agent 已可通过 `/decisions` 执行订单写入。
