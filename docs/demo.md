# 本地 Demo 验收手册

本项目有两种演示：真实 MVP 浏览器版用于作品集展示；确定性 mock Agent 用于离线 SSE 契约验证。两者的接口一致，但数据源和副作用不同。

## 真实 MVP 浏览器版

在项目 worktree 中执行：

```bash
PYTHONPATH=src uv run --env-file /Users/danny/Documents/wang-agent/.env \
  python -m customer_service_agent.mvp.seed
PYTHONPATH=src uv run --env-file /Users/danny/Documents/wang-agent/.env \
  python -m uvicorn customer_service_agent.mvp.app:create_mvp_app_from_environment \
  --factory --host 127.0.0.1 --port 8001
```

在浏览器打开 `http://127.0.0.1:8001/`，选择“客户 A”，依次输入：

| 输入 | 展示能力 |
|---|---|
| `云端降噪耳机保修多久？` | DeepSeek → `search_product_faq` → Milvus 混合检索与引用。 |
| `已付款订单可以取消吗？` | `search_policy_raptor` → RAPTOR 政策证据。 |
| `耳机是什么品牌？` | `search_commerce_graph` → Neo4j 商品关系。 |
| `我的订单有多少个？` | `query_business_data` → 受控只读 SQL。 |
| `查询订单 MVP-ORDER-1001` | `get_order` → 客户范围订单查询。 |
| `取消订单 MVP-ORDER-1001，原因是不想要了` | `cancel_order` 预览 → 页面确认 → PostgreSQL 幂等执行。 |
| `请长期记住我偏好简洁中文回答` | `remember_preference` → 本地 Mem0 PGVector。 |
| `查看我的记忆` | `list_memories` → 客户隔离的记忆列表。 |

订单写操作只会先显示确认按钮；未确认不会改变订单。为重复演示取消订单，可重新执行 seed 或改用创建订单、修改收货信息、退货申请场景。

## 确定性 mock 演示

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
