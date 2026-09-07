# 智能客服 Agent

一个面向电商场景的智能客服 Agent：用户可以咨询商品和政策、查询订单、管理长期偏好，并在需要修改订单时通过人工确认完成操作。

系统将大模型的自然语言理解能力与可控的业务工具结合起来，支持本地部署、流式对话、知识引用、客户隔离和 LangSmith 观测。

## 核心能力

| 场景 | 实现 | 用户可见效果 |
| --- | --- | --- |
| 商品与政策问答 | 常规 RAG、RAPTOR、GraphRAG | 回答附带来源，减少无依据回答 |
| 订单查询 | 受控 NL2SQL、订单工具 | 只能查询当前客户范围内的数据 |
| 订单操作 | 预览 + HITL 审批 + 幂等执行 | 未确认不会修改订单 |
| 长期记忆 | Mem0 + PostgreSQL | 只保存用户明确要求记住的偏好 |
| 实时交互 | FastAPI SSE + assistant-ui | 流式回答、工具状态和引用实时展示 |
| 运行治理 | 权限校验、调用预算、有限重试、转人工 | 异常时停止扩散并给出明确状态 |

## 技术亮点

### 多路检索与来源引用

- **常规 RAG**：Milvus 稠密/稀疏混合检索，适合商品 FAQ 和保修说明。
- **RAPTOR**：将政策长文构建为递归摘要树，适合退换货、取消政策等跨段落问题。
- **GraphRAG**：Neo4j 固定 schema 和白名单查询，适合商品品牌、属性和关系问题。

知识工具将压缩后的证据交给模型，将原始文档、分数和图路径保存在 `ToolMessage.artifact`；前端只展示经过校验的 `citation`。

### 安全的业务操作

订单写操作采用统一流程：

```text
用户意图 → operation 预览 → approval.required → 用户批准/拒绝 → 幂等执行
```

创建订单、修改联系方式、取消订单和退货申请都不会由模型直接落库。审批状态由 PostgreSQL checkpoint 保存，重复提交不会重复执行。

### 可信身份与数据隔离

`customer_id` 由服务端请求上下文注入，模型不能在消息中指定客户。订单、SQL 和长期记忆都会按客户范围过滤；前端切换“客户 A / 客户 B”会创建新的演示线程。

### 受控 NL2SQL

业务查询只允许单条只读 `SELECT`，经过 AST 解析、关系和字段白名单、客户条件注入、LIMIT 以及 statement timeout。SQL 不会展示在前端。

### 可观测、可评测

后端输出统一 SSE 事件：`message.delta`、`tool.started`、`tool.completed`、`citation`、`approval.required`、`handoff.required` 和 `message.completed`。测试覆盖单元、契约、Docker 集成和真实模型；真实 LangSmith 调用默认关闭，显式开启后即可查看 trace 和评测 experiment。

## 架构

```text
assistant-ui React
        │ SSE / JSON
        ▼
FastAPI API
        │ 可信 RuntimeContext + CustomerService
        ▼
LangChain Agent + LangGraph checkpoint
        ├─ 中间件：权限、预算、重试、转人工
        └─ 工具
           ├─ Milvus：RAG / RAPTOR
           ├─ Neo4j：GraphRAG
           ├─ PostgreSQL：订单 / SQL / checkpoint
           └─ Mem0 + PostgreSQL：长期记忆
```

## 快速启动

### 1. 准备环境

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20.19+ 或 22.12+
- Docker Desktop
- DeepSeek OpenAI-compatible API
- PostgreSQL 17 + pgvector
- Milvus 3.x
- Neo4j 2026.07.1

PostgreSQL 和 Milvus 可以使用已有 Docker 服务；Neo4j 的 Compose 文件位于 `infra/neo4j/compose.yaml`。

### 2. 安装项目依赖

```bash
git clone <your-repository-url>
cd wang-agent
uv sync

cd frontend
npm install
cd ..
```

### 3. 配置 `.env`

```bash
cp .env.example .env
```

然后根据本机服务修改参数：

| 变量 | 必填 | 示例/说明 |
| --- | --- | --- |
| `Deepseek_API_KEY` | 是 | DeepSeek API Key |
| `Deepseek_BASE_URL` | 否 | `https://api.deepseek.com/v1` |
| `MVP_POSTGRES_DSN` | 是 | 订单和 checkpoint 数据库 |
| `MVP_MEM0_DSN` | 是 | Mem0 独立数据库，建议与订单库分开 |
| `MILVUS_URI` | 否 | `http://127.0.0.1:19530` |
| `NEO4J_URI` | 否 | `neo4j://127.0.0.1:7687` |
| `NEO4J_AUTH` | 是 | `neo4j/your_password` |
| `LANGSMITH_API_KEY` | 否 | 仅真实 trace/评测需要 |
| `LANGSMITH_PROJECT` | 否 | LangSmith 项目名称 |

`.env.example` 只包含占位符。真实 API Key、数据库密码和 LangSmith Key 不要提交 Git。

### 4. 启动基础设施并初始化数据

如果本机还没有 Neo4j：

```bash
docker compose --env-file .env -f infra/neo4j/compose.yaml up -d
```

确认 PostgreSQL、Milvus 和 Neo4j 已启动后，执行幂等初始化：

```bash
PYTHONPATH=src uv run --env-file .env \
  python -m customer_service_agent.mvp.seed
```

初始化内容包括两条模拟订单、FAQ、RAPTOR 政策摘要、商品关系图和一条长期记忆。

### 5. 启动后端

终端一：

```bash
PYTHONPATH=src uv run --env-file .env \
  python -m uvicorn customer_service_agent.mvp.app:create_mvp_app_from_environment \
  --factory --host 127.0.0.1 --port 8001
```

健康检查：

```bash
curl http://127.0.0.1:8001/health/live
curl http://127.0.0.1:8001/health/ready
```

### 6. 启动前端

终端二：

```bash
cd frontend
npm run dev
```

打开 <http://127.0.0.1:5173/>，看到“后端就绪”后即可对话。

## 推荐体验问题

```text
你好，我叫王翰
我叫什么
云端降噪耳机保修多久？
耳机是什么品牌？
查询订单 MVP-ORDER-1001
请直接取消订单 MVP-ORDER-1001，原因是不想要了。请生成取消订单预览，等待我审批。
请长期记住我偏好简洁中文回答
查看我的记忆
```

推荐顺序是：先体验会话历史和知识问答，再体验订单查询、订单审批和长期记忆。审批卡片出现后，可以点击“拒绝”确认订单状态不变；切换到客户 B 可以验证客户隔离。

完整验收命令见 [docs/demo.md](docs/demo.md)。

## 测试与 LangSmith

本地快速检查：

```bash
LANGSMITH_TRACING=false uv run pytest -m 'unit or contract' -q
uv run ruff check src tests
cd frontend && npm test && npm run typecheck && npm run build
```

真实 DeepSeek：

```bash
RUN_LIVE_MODEL_TESTS=1 PYTHONPATH=src \
  uv run --env-file .env pytest tests/live/test_mvp_deepseek.py -q -rs
```

真实 LangSmith trace 与评测：

```bash
RUN_LIVE_LANGSMITH_TESTS=1 RUN_LIVE_MODEL_TESTS=1 \
  uv run --env-file .env pytest tests/live/test_langsmith_portfolio_eval.py -q -rs
```

## 运行边界

- `X-Demo-Customer` 只用于本地演示，不是生产认证方案。
- 不包含真实支付、生产订单系统或生产工单平台。
- 外部模型、Tavily 和 LangSmith 调用默认关闭。
- 不保存或展示模型 chain-of-thought。
- 如果页面显示“需要人工客服继续处理”，表示本次触发了 Agent 执行预算或转人工事件；点击“新对话”后用完整订单号和取消原因重新体验审批流程。

## 文档

- [本地体验与验收](docs/demo.md)
- [SDD 规格](docs/specs)
- [TDD 记录](docs/tdd/records)
- [系统提示词](config/prompts.yaml)
- [assistant-ui 前端设计](docs/superpowers/specs/2026-09-07-assistant-ui-local-frontend-design.md)
