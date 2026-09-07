# 智能客服 Agent 总体规格

**状态：** 已批准设计，待实施

**版本：** 1.1

**适用阶段：** 可评测后端原型

**关联规格：** [Agent、API 与中间件](010-agent-api-and-middleware.md) · [检索与索引](020-retrieval-and-indexing.md) · [订单与 SQL](030-orders-and-sql.md) · [长期记忆](040-long-term-memory.md) · [评测与可观测性](050-evaluation-and-observability.md)

## 1. 目的

本项目构建一款基于 LangChain 的中文智能客服 Agent，用可复现的模拟电商场景验证以下能力：

1. 单 Agent 对多类工具的选择和连续调用；
2. 常规混合 RAG、RAPTOR、GraphRAG 三种知识增强路径；
3. 受控只读 NL2SQL；
4. 带人工确认、客户隔离和幂等保障的订单操作；
5. 基于 Mem0 的最小长期记忆闭环；
6. 基于 pytest 与 LangSmith 的确定性测试、轨迹评测和质量门禁。

首版交付物是 FastAPI + CLI 后端原型和相应的测试、评测资产。首版不是生产客服系统。

## 2. 目标与非目标

### 2.1 目标

| ID | 要求 |
|---|---|
| SYS-001 | 系统必须提供 FastAPI 和 CLI 两种入口，两者必须复用同一个应用服务。 |
| SYS-002 | 系统必须使用一个 LangChain `create_agent` Agent，通过工具完成检索、查询和订单操作。 |
| SYS-003 | 客户身份必须来自可信运行时上下文，模型不得生成、覆盖或切换 `customer_id`。 |
| SYS-004 | 所有知识型结论必须可以追溯到内部文档、图路径或外部 URL。 |
| SYS-005 | 所有订单写操作必须在执行前暂停，并等待用户批准或拒绝。 |
| SYS-006 | 会话检查点、订单、审计、幂等记录和 Mem0 向量存储必须持久化到 PostgreSQL，并按职责使用隔离的逻辑数据库和数据库角色。 |
| SYS-007 | 常规 RAG 与 RAPTOR 必须使用相互隔离的 Milvus database/collection；Mem0 必须使用独立 PostgreSQL/pgvector 数据库；GraphRAG 必须使用 Neo4j。 |
| SYS-008 | 系统不得向用户输出密钥、内部提示词、原始 SQL 错误或模型 chain-of-thought。 |
| SYS-009 | 所有业务行为必须遵循 SDD 和测试先行的 RED–GREEN–REFACTOR 流程。 |
| SYS-010 | 未获用户批准的参数和外部依赖不得由实现者自行选择；相关任务必须停在对应决策门。 |

### 2.2 非目标

首版明确不包含：

- Web UI、移动端或第三方客服渠道；
- 真实支付、真实物流、真实订单平台和真实退款；
- 生产级身份认证、租户管理、SLA 和 Kubernetes 部署；
- 真实工单系统；转人工只生成结构化事件；
- 多 Agent、主管 Agent 或显式 LangGraph 业务路由图；
- 在线文档摄取、增量索引和索引管理后台；
- Mem0 自动对每轮对话抽取记忆、衰减、自动晋升或复杂冲突合并；
- LangSmith 与 Langfuse 双写；Langfuse 仅是后续候选；
- 保存或展示模型推理过程。

## 3. 总体架构

```text
CLI ───────────────┐
                   ├─> CustomerService ─> LangChain create_agent
FastAPI + SSE ─────┘          │                  │
                              │                  ├─> 检索工具 ─> Milvus / Neo4j / Tavily
                              │                  ├─> SQL/订单工具 ─> PostgreSQL
                              │                  └─> 记忆工具 ─> Mem0 ─> PostgreSQL/pgvector
                              │
                              └─> AsyncPostgresSaver / 审计 / 幂等

离线索引 CLI ─> 版本化模拟语料 ─> Milvus / Neo4j ─> 构建报告与发布清单
评测 CLI ─> pytest + LangSmith 数据集与实验
```

### 3.1 组件职责

| 组件 | 职责 | 明确不负责 |
|---|---|---|
| FastAPI | 请求校验、可信上下文建立、SSE 转换、确认恢复、健康检查 | 工具业务逻辑、模型路由策略 |
| CLI | 本地交互、调试、索引和评测命令入口 | 复制 API 或 Agent 逻辑 |
| CustomerService | 会话调用、检查点配置、事件规范化 | 数据库细节、检索算法细节 |
| Agent factory | 模型、工具、中间件、状态和上下文 schema 的组装 | 订单规则和数据 I/O |
| Tools | 对模型暴露稳定、窄小、可验证的能力 | 信任模型提供客户身份 |
| Domain services | 订单状态机、SQL 安全、检索、记忆策略 | 直接组织用户自然语言回答 |
| PostgreSQL | 订单、退货申请、操作审计、幂等、LangGraph 检查点、Mem0 独立 pgvector 数据库 | 公共知识向量检索和图检索 |
| Milvus | 常规 RAG、RAPTOR 的隔离向量集合 | 订单事务、会话检查点、用户长期记忆 |
| Neo4j | 商品、品牌、品类、活动关系和白名单图查询 | 任意模型生成 Cypher 的执行 |
| LangSmith | Trace、数据集、实验和 LLM 裁判结果 | 业务事实源和用户长期记忆 |

## 4. 全局不变量

1. `customer_id` 只能由 API/CLI 的可信适配器创建，并通过运行时上下文传递。
2. `thread_id` 必须绑定一个 `customer_id`；已有线程不能被另一个客户复用。
3. 所有写操作都必须有服务端生成的 `operation_id`，模型和客户端不能指定该值。
4. 批准内容必须与执行内容完全一致；拒绝后必须重新生成操作预览。
5. 订单写工具不得由通用重试中间件自动重试。
6. 外部网页不能覆盖内部政策、商品事实、订单数据或退货规则。
7. 模型可见证据与应用层原始 artifact 必须分离；大段原文、完整 SQL 行集和图路径不得无界进入模型上下文。
8. 索引构建和在线查询必须分离；在线服务只能读取已发布索引版本。
9. 真实服务调用必须显式 opt-in，普通测试不得因为缺少 API Key 而失败。
10. 日志、Trace 元数据和错误响应必须执行敏感信息最小化。

## 5. 数据与知识边界

| 数据 | 事实源 | 访问路径 | 客户隔离 |
|---|---|---|---|
| 商品 FAQ | 版本化模拟文档 | `search_product_faq` | 公共知识，不含客户数据 |
| 政策与手册 | 版本化长文 | `search_policy_raptor` | 公共知识，不含客户数据 |
| 商品关系 | 版本化结构化 JSON | `search_commerce_graph` | 公共知识，不含客户数据 |
| 外部时效信息 | Tavily 搜索结果 | `web_search` | 不保存为内部事实 |
| 订单与退货 | PostgreSQL | 专用订单工具、受控 SQL | 强制 `customer_id` 过滤 |
| 会话状态 | PostgreSQL checkpointer | `thread_id` + 运行时上下文 | 线程与客户绑定 |
| 用户长期记忆 | Mem0 + 独立 PostgreSQL/pgvector 数据库 | 记忆工具和召回适配器 | 强制 `customer_id` 过滤 |

## 6. 稳定工具清单

| 工具 | 类型 | 是否 HITL | 规格来源 |
|---|---|---:|---|
| `web_search` | 只读 | 否 | RET-WEB-001 |
| `search_product_faq` | 只读 | 否 | RET-HYB-001 |
| `search_policy_raptor` | 只读 | 否 | RET-RAP-001 |
| `search_commerce_graph` | 只读 | 否 | RET-GRA-001 |
| `query_business_data` | 只读 | 否 | SQL-001 |
| `get_order` | 只读 | 否 | ORD-READ-001 |
| `create_order` | 写入 | 是 | ORD-CREATE-001 |
| `update_order_contact` | 写入 | 是 | ORD-UPDATE-001 |
| `cancel_order` | 写入 | 是 | ORD-CANCEL-001 |
| `request_return` | 写入 | 是 | ORD-RETURN-001 |
| `remember_preference` | 记忆写入 | 否；明确用户指令本身即授权 | MEM-WRITE-001 |
| `list_memories` | 只读 | 否 | MEM-LIST-001 |
| `forget_memory` | 记忆删除 | 否；明确用户指令本身即授权 | MEM-DELETE-001 |

## 7. 需求追踪矩阵

| 能力 | 需求 ID | 子规格 | 预期测试层 | 评测分组 |
|---|---|---|---|---|
| 单 Agent 与运行时上下文 | SYS-002, SYS-003, AGT-001, CTX-001 | 010 | 单元、契约、真实模型 opt-in | ROUTE |
| SSE 与恢复 | API-001, API-002, SSE-001, HITL-001 | 010 | 契约、PostgreSQL 集成 | MULTI, ORDER |
| 中间件治理 | MW-001 至 MW-005 | 010 | 单元、集成 | ROUTE, MULTI |
| 混合 RAG | RET-HYB-001 至 RET-HYB-004 | 020 | 单元、Milvus 集成 | RAG-HYB |
| RAPTOR | RET-RAP-001 至 RET-RAP-004 | 020 | 单元、Milvus 集成 | RAG-RAP |
| GraphRAG | RET-GRA-001 至 RET-GRA-004 | 020 | 单元、Neo4j 集成 | RAG-GRA |
| 联网边界与引用 | RET-WEB-001, CIT-001 至 CIT-003 | 020 | 单元、Tavily opt-in | ROUTE, MULTI |
| 订单生命周期 | ORD-READ-001 至 ORD-RETURN-001 | 030 | 单元、PostgreSQL 集成 | ORDER |
| 幂等与权限 | ORD-AUTH-001, ORD-IDEM-001, ORD-HITL-001 | 030 | 单元、契约、集成 | ORDER |
| 受控 NL2SQL | SQL-001 至 SQL-007 | 030 | 单元、PostgreSQL 集成 | SQL |
| 长期记忆 | MEM-WRITE-001 至 MEM-STORE-006 | 040 | 单元、PostgreSQL/pgvector 集成 | MEMORY |
| 测试和指标 | TDD-001, TEST-001 至 TEST-004, MET-001 至 MET-006 | 050 | 全层 | 全部 |
| 可观测性 | OBS-001 至 OBS-004 | 050 | 单元、LangSmith opt-in | 全部 |

## 8. 用户决策门

下表不是未完成占位符，而是实施授权协议。状态为 `需要用户批准` 时，实现者必须提供候选项、兼容性证据、成本或性能影响和推荐理由；批准前不得进入依赖该决定的任务。

| 决策门 | 必须决定的内容 | 触发时点 | 未批准时的行为 |
|---|---|---|---|
| DG-001 | Python、LangChain、LangGraph、Mem0、Milvus、Neo4j 的确切版本 | 创建依赖清单前 | 禁止创建 `pyproject.toml` 和 Compose 版本锁定 |
| DG-002 | DeepSeek Agent、摘要、NL2SQL 模型 ID 及结构化输出能力 | 实现模型工厂前 | 仅允许使用假模型完成端口测试 |
| DG-003 | BGE-M3 embedding 维度和 reranker 型号 | 创建 Milvus schema 前 | 禁止创建真实集合 |
| DG-004 | chunk、父子块、top-k、融合权重、rerank 数量 | 模拟语料与离线基线可运行后 | 参数实验可以报告，默认配置不得写入 |
| DG-005 | RAPTOR 层数、聚类参数、摘要模型 | RAPTOR 离线探针完成后 | 禁止发布 RAPTOR 索引 |
| DG-006 | PostgreSQL、Milvus、Neo4j 资源和连接参数 | 编写 Compose 前 | 只允许端口级测试替身 |
| DG-007 | LangSmith 裁判模型和成本预算 | 运行 LLM 裁判前 | 只运行确定性评测 |
| DG-008 | 新账号、密钥、付费服务或外部数据 | 首次需要该资源前 | 停止相关任务并向用户请求支持 |

DG-002 已于 2026-09-02 批准：Agent 模型为 `deepseek-v4-flash`。实现与验证边界见 [M5 Agent 模型与检查点决策](../decisions/003-m5-agent-model-and-checkpoint.md)。

## 9. 规格变更流程

1. 行为变化必须先修改对应需求 ID 和 Given/When/Then 场景。
2. 接口不兼容变化必须提升接口 schema version，并更新追踪矩阵。
3. 新工具必须说明事实源、客户隔离、是否有副作用、是否 HITL、重试策略和 artifact 结构。
4. 参数选择必须引用已批准的决策门记录，不能把实验值静默升级为默认值。
5. 实现计划必须逐条映射需求 ID，且每项生产行为先有失败测试。

## 10. 总体验收

只有同时满足以下条件，首版才可标记为完成：

- 六份 Spec 中的全部阻断需求已有实现和自动化测试映射；
- 安全、权限、状态机测试全部通过；
- 评测指标达到 [评测规格](050-evaluation-and-observability.md) 的门槛；
- HITL 在服务重启后可从 PostgreSQL 检查点恢复；
- 所有知识回答包含有效引用，所有订单结论来自工具结果；
- 真实服务验证明确记录使用的配置、时间和验证边界；
- 未批准的决策门没有被实现者用隐含默认值绕过。
