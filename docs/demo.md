# 本地 Demo 验收手册

这个 Demo 用确定性 mock Agent 展示已有的 FastAPI、SSE、可信运行时上下文、工具事件、引用和 HITL 恢复契约。它不读取 `.env`、不连接 Docker、不会调用模型或写入真实订单。

## 启动

```bash
uv run uvicorn customer_service_agent.demo:app --app-dir src --reload --port 8000
```

另开一个终端，健康检查应返回 `ready`：

```bash
curl http://127.0.0.1:8000/health/ready
```

## 演示用例

所有消息接口都返回 `text/event-stream`。`X-Demo-Customer` 只用于本地演示身份上下文；生产系统必须由认证层注入身份。

### 1. 订单查询：工具事件与回答

```bash
curl -N -X POST http://127.0.0.1:8000/v1/threads/demo-order/messages:stream \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-Customer: demo-customer-a' \
  -d '{"message":"查询订单"}'
```

验收：依次出现 `tool.started`、`tool.completed`、`message.delta`、`message.completed`；最终文本包含 `DEMO-1001`。

### 2. FAQ：检索工具与引用

```bash
curl -N -X POST http://127.0.0.1:8000/v1/threads/demo-faq/messages:stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"商品保修多久"}'
```

验收：出现 `search_product_faq` 工具事件和 `citation` 事件，引用 ID 为 `faq-warranty-v1`。

### 3. 取消订单：HITL 预览与批准恢复

先发起取消：

```bash
curl -N -X POST http://127.0.0.1:8000/v1/threads/demo-cancel/messages:stream \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-Customer: demo-customer-a' \
  -d '{"message":"取消订单"}'
```

验收：只出现 `approval.required`，其中包含动态 `interrupt_id` 与 `DEMO-1001` 预览；此时没有“已取消”消息。

复制返回 JSON 中的 `interrupt_id`，再批准：

```bash
curl -N -X POST http://127.0.0.1:8000/v1/threads/demo-cancel/decisions \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-Customer: demo-customer-a' \
  -d '{"interrupt_id":"替换为上一步的值","decision":"approve"}'
```

验收：返回 `message.completed`，文本为“模拟订单 DEMO-1001 已取消”。把 `decision` 改为 `reject` 并附带 `reason`，可演示拒绝路径。

### 4. 默认客服引导

```bash
curl -N -X POST http://127.0.0.1:8000/v1/threads/demo-help/messages:stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"你好"}'
```

验收：返回支持的三类演示指令。

## Mock 数据

| 名称 | 值 | 用途 |
|---|---|---|
| 客户 | `demo-customer-a` | 可信 runtime-context 演示身份 |
| 订单 | `DEMO-1001` | 查询与取消预览 |
| FAQ | 12 个月有限保修 | RAG 回答文本 |
| 引用 | `faq-warranty-v1` | citation 事件 |

数据只保存在进程内：重启服务会重置线程绑定和待审批操作。它是面试演示数据，不是数据库种子，也不与 PostgreSQL/Milvus/Neo4j 测试数据混用。

## 自动化验收

```bash
uv run pytest tests/contract/test_demo_api.py -q
```

预期：3 项通过，分别覆盖订单工具事件、FAQ 引用、HITL 批准恢复。
