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

## 推荐的面试演示顺序

1. 展示 `RuntimeContext` 如何阻止模型伪造客户身份。
2. 展示一次订单写操作只产生 operation 预览，而非直接修改订单。
3. 展示 PostgreSQL checkpoint 支持 HITL 暂停和服务重建后的恢复。
4. 展示 RAG 的模型可见证据与 `ToolMessage.artifact` 中原始数据分离。
5. 展示 SQL AST 白名单和客户范围注入如何限制 NL2SQL。

## 当前范围

已验证：领域状态机、订单预览、受控 SQL、Mem0 PGVector、三类检索索引、单 Agent 工厂、预算/重试、可信 runtime-context、PostgreSQL HITL 恢复与 DeepSeek 工具调用。

已实现（作品集 M6）：六条离线确定性评测、LangSmith 数据集/experiment 适配与 opt-in 的真实 trace 验证。当前环境尚未完成该 live 验证时，测试会明确显示 skipped。

不纳入当前作品集范围：订单 operation approval 的 API/SSE 编排、会话摘要、持久化审计、全工具生产组装、60 条门槛集与 LLM 裁判。项目不会把这些未完成项描述为生产就绪能力。

详细规格见 [docs/specs](docs/specs)，TDD 证据见 [docs/tdd/records](docs/tdd/records)。
