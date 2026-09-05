# 智能客服 Agent

一个面向电商客服场景的 LangChain/LangGraph 后端原型。项目重点不是堆叠工具数量，而是把 Agent 在真实业务中容易失控的部分收敛为可验证的边界：可信客户身份、检索证据、受控 SQL、订单写操作预览、持久化审批和评测分层。

## 作品亮点

- 单一 `create_agent` 编排，工具、模型和检查点均通过显式工厂注入。
- 可信 `RuntimeContext` 仅由 API/CLI 创建；`customer_id` 不进入模型提示词或工具 schema，工具从 LangGraph runtime 获得客户范围。
- 写订单工具只创建带参数哈希的 operation 预览；领域层已有批准后的原子执行与重复批准幂等。
- LangGraph HITL 使用 PostgreSQL checkpointer，已验证“暂停 → 重建 saver/Agent → 同线程恢复”。
- 模型和工具调用分别有持久化上限；连接、超时、429、5xx 才允许有限重试，超限会输出结构化转人工事件。
- 常规混合 RAG、RAPTOR、固定 schema GraphRAG 和受控 NL2SQL 均与客户数据隔离设计配套测试。

## 架构

```text
FastAPI / CLI
    -> RuntimeContext（可信 customer_id、thread_id、request_id）
    -> CustomerService（线程所有权与单运行锁）
    -> LangGraphAgentRunner
        -> RuntimeAuthorizationMiddleware
        -> GovernanceMiddleware（预算与有限重试）
        -> 工具：RAG / SQL / 订单预览 / 记忆
    -> SSE AppEvent

PostgreSQL: 订单、operation、审计、LangGraph checkpoint、Mem0 pgvector
Milvus: 混合 RAG 与 RAPTOR 索引
Neo4j: 确定性商品关系图
```

## 快速验证

```bash
uv run pytest -m 'unit or contract' -q
uv run pytest -m integration_postgres -q
uv run ruff check src tests
```

作品集版 M6 另提供 6 条固定场景的离线工具路由评测：

```bash
uv run pytest tests/unit/quality/test_portfolio_eval.py -q
```

真实模型与 LangSmith 测试默认关闭。获得授权并在当前终端配置 `LANGSMITH_API_KEY`、`Deepseek_API_KEY`、`Deepseek_BASE_URL` 后才运行：

```bash
RUN_LIVE_LANGSMITH_TESTS=1 RUN_LIVE_MODEL_TESTS=1 \
  uv run pytest tests/live/test_langsmith_portfolio_eval.py -q
```

该命令会在 `wang-agent-portfolio` LangSmith 项目中创建或复用 `wang-agent-portfolio-v1` 数据集、创建一次评测 experiment，并记录一次真实 DeepSeek 工具调用 trace。测试不读取或输出 API Key、数据库密码或 `.env` 内容。

## 本地手工演示

### 真实 MVP 浏览器演示

先在 Docker 服务已启动、根目录 `.env` 已配置的终端执行一次幂等 seed：

```bash
cd /Users/danny/Documents/wang-agent/.worktrees/mvp-complete
PYTHONPATH=src uv run --env-file /Users/danny/Documents/wang-agent/.env \
  python -m customer_service_agent.mvp.seed
```

启动真实 DeepSeek MVP：

```bash
PYTHONPATH=src uv run --env-file /Users/danny/Documents/wang-agent/.env \
  python -m uvicorn customer_service_agent.mvp.app:create_mvp_app_from_environment \
  --factory --host 127.0.0.1 --port 8001
```

打开 `http://127.0.0.1:8001/`。页面展示对话、工具调用、引用及订单审批按钮；演示用例和固定 mock 数据见 [本地 Demo 验收手册](docs/demo.md)。实时模型验收可执行：

```bash
RUN_LIVE_MODEL_TESTS=1 PYTHONPATH=src \
  uv run --env-file /Users/danny/Documents/wang-agent/.env \
  pytest tests/live/test_mvp_deepseek.py -q -rs
```

### 离线 mock 演示

无需 Docker、`.env` 或模型即可启动一个确定性 mock Agent：

```bash
uv run uvicorn customer_service_agent.demo:app --app-dir src --reload --port 8000
```

它通过 SSE 展示订单查询、FAQ 引用和取消订单的 HITL 批准恢复。完整请求与 mock 数据见 [本地 Demo 验收手册](docs/demo.md)。

## 推荐的面试演示顺序

1. 展示 `RuntimeContext` 如何阻止模型伪造客户身份。
2. 展示一次订单写操作只产生 operation 预览，而非直接修改订单。
3. 展示 PostgreSQL checkpoint 支持 HITL 暂停和服务重建后的恢复。
4. 展示 RAG 的模型可见证据与 `ToolMessage.artifact` 中原始数据分离。
5. 展示 SQL AST 白名单和客户范围注入如何限制 NL2SQL。

## 当前范围

已验证：领域状态机、订单预览、受控 SQL、Mem0 PGVector、三类检索索引、单 Agent 工厂、并发工具预算、可信 runtime-context、PostgreSQL checkpoint 与真实 DeepSeek FAQ 工具调用。

已实现（作品集 M6）：六条离线确定性评测、LangSmith 数据集/experiment 适配与 opt-in 的真实 trace 验证。当前环境尚未完成该 live 验证时，测试会明确显示 skipped。

不纳入当前作品集范围：生产身份认证、支付、会话摘要、完整持久化审计、60 条门槛集、LLM 裁判、生产部署与容量治理。项目不会把 MVP 描述为生产就绪能力。

详细规格见 [docs/specs](docs/specs)，TDD 证据见 [docs/tdd/records](docs/tdd/records)。
