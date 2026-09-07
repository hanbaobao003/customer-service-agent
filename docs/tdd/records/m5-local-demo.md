# M5 本地 Mock Demo TDD 记录

**范围：** 仅为简历演示增加确定性 FastAPI/SSE 入口；不装配生产 Agent、不连接 Docker、不调用模型。

## 1. 订单查询与 HITL

- **RED：** `uv run --env-file .env pytest tests/contract/test_demo_api.py -q`
- **失败原因：** `ModuleNotFoundError: No module named 'customer_service_agent.demo'`，本地 Demo 入口尚不存在。
- **GREEN：** `DemoAgent` 返回订单工具事件与 mock `DEMO-1001`，取消操作先发出 `approval.required`，批准后返回完成事件；两个契约测试通过。

## 2. FAQ 引用

- **RED：** 临时移除 FAQ 分支后，`test_demo_warranty_streams_a_citation` 得到普通消息事件，而非期望的工具/引用事件。
- **GREEN：** 恢复最小 FAQ mock 分支，返回 `search_product_faq`、`citation` 和 `faq-warranty-v1`；完整 Demo 契约测试 `3 passed`。

## 3. 手工 HTTP 验收

- 首次 Uvicorn 直接启动失败，根因是独立进程未继承 pytest 的 `src/` 导入路径。
- 使用 `uvicorn --app-dir src` 成功启动。实际请求验证了 `/health/ready`、订单 SSE、FAQ citation、取消预览及批准恢复；服务随后已停止。
