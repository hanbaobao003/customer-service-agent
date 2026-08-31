# Spec 对齐的代码目录设计

**状态：** 已批准

**版本：** 1.0

**批准日期：** 2026-08-31

## 1. 目标

代码按照五份子 Spec 的业务能力分区，使规格、生产代码和测试能够直接互相定位。首版不采用全局 `models/`、`ports/`、`services/`、`adapters/` 技术分层，避免一个功能需要跨多个目录阅读。

## 2. 目标结构

```text
wang-agent/
├── docs/
│   ├── architecture/
│   ├── specs/
│   ├── tdd/
│   └── superpowers/plans/
├── src/customer_service_agent/
│   ├── config.py
│   ├── cli.py
│   ├── shared/
│   │   ├── models.py
│   │   └── errors.py
│   ├── agent_api/
│   │   ├── service.py
│   │   ├── middleware.py
│   │   └── api.py
│   ├── retrieval/
│   │   ├── models.py
│   │   ├── hybrid.py
│   │   ├── raptor.py
│   │   ├── graph.py
│   │   ├── indexing.py
│   │   └── tools.py
│   ├── commerce/
│   │   ├── orders.py
│   │   ├── sql.py
│   │   └── postgres.py
│   ├── memory/
│   │   ├── service.py
│   │   └── mem0_pg.py
│   └── quality/
│       ├── evaluation.py
│       └── observability.py
├── tests/
│   ├── unit/
│   │   ├── agent_api/
│   │   ├── retrieval/
│   │   ├── commerce/
│   │   ├── memory/
│   │   └── quality/
│   ├── contract/
│   ├── integration/
│   │   ├── postgres/
│   │   ├── milvus/
│   │   └── neo4j/
│   └── live/
├── data/
├── evals/
└── migrations/
```

各 Python package 可以包含最小 `__init__.py` 以导出公开接口；`__init__.py` 不放业务逻辑，也不计入主要模块数量。

## 3. Spec 映射

| Spec | 生产目录 | 单元测试目录 | 默认阅读入口 |
|---|---|---|---|
| 010 Agent、API 与中间件 | `agent_api/` | `tests/unit/agent_api/` | `agent_api/service.py` |
| 020 检索与索引 | `retrieval/` | `tests/unit/retrieval/` | `retrieval/models.py` 后进入对应算法 |
| 030 订单与 SQL | `commerce/` | `tests/unit/commerce/` | `commerce/orders.py`、`commerce/sql.py` |
| 040 长期记忆 | `memory/` | `tests/unit/memory/` | `memory/service.py` |
| 050 评测与可观测性 | `quality/` | `tests/unit/quality/` | `quality/evaluation.py` |

## 4. 文件职责

### 4.1 根目录与 shared

- `config.py`：配置 schema、决策门状态和依赖连接引用；不得读取或输出密钥值。
- `cli.py`：CLI 命令解析和应用入口，不复制业务逻辑。
- `shared/models.py`：仅存放跨两个以上 Spec 使用的可信上下文、应用事件和少量公共值对象。
- `shared/errors.py`：稳定错误码与公开错误 envelope。

`shared/` 不允许放订单状态机、检索算法、记忆策略或供应商 SDK 代码。

### 4.2 agent_api

- `service.py`：系统提示词、单 `create_agent` 工厂、`CustomerService`、线程绑定和 PostgreSQL checkpointer 组装。
- `middleware.py`：异步工具中间件、调用预算、有限重试、HITL 和摘要边界。
- `api.py`：FastAPI 应用、请求/响应 schema、SSE 编码、decision 和健康检查。

### 4.3 retrieval

- `models.py`：Evidence、Citation、Artifact、统一检索结果和引用完整性校验。
- `hybrid.py`：Milvus dense/BM25 融合、rerank 接口和 small-to-big。
- `raptor.py`：RAPTOR 树、完整性检查和 Milvus 查询。
- `graph.py`：Neo4j 固定 schema、白名单查询和路径证据。
- `indexing.py`：离线构建报告、候选验证和原子发布状态机。
- `tools.py`：四个模型工具入口和 Tavily 外部时效搜索边界。

### 4.4 commerce

- `orders.py`：订单 DTO、状态机、客户隔离端口、预览、HITL、幂等和订单工具。
- `sql.py`：SQL DTO、AST 白名单、客户作用域和只读查询工具。
- `postgres.py`：订单 repository、事务、审计、operation 和 SQL 只读执行；不包含 Mem0 数据访问。

### 4.5 memory

- `service.py`：记忆 DTO、明确意图、敏感规则、冲突、召回、查看、删除和三个工具。
- `mem0_pg.py`：Mem0 `AsyncMemory`、独立 PostgreSQL/pgvector 配置与 DTO 映射。

### 4.6 quality

- `evaluation.py`：评测数据 schema、加载器、确定性评分器、指标、质量门禁和报告。
- `observability.py`：Trace 层级、脱敏、大小预算、best-effort sink 和 LangSmith 适配。

## 5. 拆分规则

1. 会一起修改和测试的接口、服务与工具优先放在同一个功能文件。
2. 只有确实需要测试替身或供应商替换时才定义 Protocol；Protocol 放在使用它的功能文件中。
3. 主要文件目标为 200～400 行；超过约 400 行且出现两个独立修改原因时才拆分。
4. 拆分必须留在原 Spec 功能目录内，不能重新建立全局技术层目录。
5. 外部 SDK 适配优先与对应功能放置；只有 PostgreSQL 被 commerce 内多个能力共享，因此集中在 `commerce/postgres.py`。
6. 测试按业务行为组织，不要求与生产文件机械一一对应。

## 6. 导航示例

需求 `RET-HYB-002` 的阅读路径固定为：

```text
docs/specs/020-retrieval-and-indexing.md
→ src/customer_service_agent/retrieval/hybrid.py
→ tests/unit/retrieval/test_hybrid.py
→ tests/integration/milvus/test_hybrid.py
```

需求 `ORD-IDEM-002` 的阅读路径固定为：

```text
docs/specs/030-orders-and-sql.md
→ src/customer_service_agent/commerce/orders.py
→ src/customer_service_agent/commerce/postgres.py
→ tests/unit/commerce/test_order_operations.py
→ tests/integration/postgres/test_orders.py
```
