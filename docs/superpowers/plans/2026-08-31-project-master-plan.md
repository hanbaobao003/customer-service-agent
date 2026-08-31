# 智能客服 Agent 主项目实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this roadmap milestone by milestone. Entering a feature task also requires `superpowers:test-driven-development`; the five child plans remain the source of exact RED→GREEN steps.

**状态：** 已批准，M0 已完成，待 M1 实施

**版本：** 1.0

**Goal:** 按明确依赖和用户决策门，将五份子 Spec 分阶段实现为可评测的智能客服 Agent 后端原型。

**Architecture:** 本计划只编排里程碑、跨模块依赖、停止点和验收门，不复制子计划中的代码步骤。生产代码继续按 Spec 能力分区；每个里程碑只在其依赖满足后进入，并以可复核测试证据结束。

**Tech Stack:** Python、LangChain、LangGraph、FastAPI、PostgreSQL/pgvector、Milvus、Neo4j、Mem0、Tavily、LangSmith、pytest、uv；确切版本以已批准的决策记录为准。

**Spec:** [总体规格](../../specs/000-customer-service-agent-overview.md)

## Global Constraints

- 遵循 [TDD 执行规范](../../tdd/000-tdd-strategy.md)，每项生产行为先取得有效 RED，再写最小实现。
- 代码目录遵循 [Spec 对齐的目录设计](../../architecture/code-layout.md)，不得重新引入全局技术分层。
- 本计划不覆盖五份子计划；发生冲突时，先以 Spec 为准并修正文档，再继续实施。
- 未批准的决策门只阻塞依赖它的工作，不阻塞可使用确定性测试替身验证的任务。
- 单元和契约测试不得访问 Docker、真实模型或外网；真实依赖必须进入对应的显式测试层。
- 不读取、打印或提交 API Key、数据库密码、`.env` 内容和真实客户数据。
- PostgreSQL、Milvus、Neo4j 集成测试只能使用有明确归属标记的隔离测试资源。
- 每个子任务提交前必须保留 RED、GREEN、受影响回归和未验证边界的证据。

---

## 1. 计划关系

| 能力区 | 子计划 | 主计划中的位置 |
|---|---|---|
| Agent、API 与中间件 | [Agent、API 与中间件计划](2026-08-31-agent-api-and-middleware.md) | M1、M2、M5 |
| 检索与索引 | [检索与索引计划](2026-08-31-retrieval-and-indexing.md) | M2、M4 |
| 订单与 SQL | [订单与受控 SQL 计划](2026-08-31-orders-and-sql.md) | M2、M3 |
| 长期记忆 | [长期记忆计划](2026-08-31-long-term-memory.md) | M2、M3 |
| 评测与可观测性 | [评测与可观测性计划](2026-08-31-evaluation-and-observability.md) | M1、M6 |

实施者每次只执行当前里程碑列出的子计划 Task。子计划中的 Task 顺序不变；当一个子计划被分配到多个里程碑时，在前一里程碑完成指定 Task 后暂停，直到主计划允许继续。

## 2. 决策门与阻塞范围

| 决策门 | 最迟批准时点 | 阻塞范围 | 未阻塞工作 |
|---|---|---|---|
| DG-001 运行时与框架确切版本 | M1 创建 `pyproject.toml` 前 | 依赖锁定和所有 SDK 实现 | 文档与环境只读盘点 |
| DG-002 DeepSeek 模型 ID | M5 创建真实模型工厂前 | 真实 Agent、摘要和 NL2SQL 模型调用 | scripted model 单元与契约测试 |
| DG-003 embedding 维度与 reranker | M3/M4 创建真实向量 schema 前 | Mem0 向量适配、Milvus schema 与真实 rerank | 纯函数、端口和确定性测试替身 |
| DG-004 检索参数 | M4 发布混合索引前 | 默认检索配置与发布 | 参数化算法测试和基线报告 |
| DG-005 RAPTOR 参数 | M4 发布 RAPTOR 索引前 | 真实摘要树发布 | 确定性树构建与完整性测试 |
| DG-006 数据服务连接与资源 | M0 兼容性检查后、首次集成测试前 | 对应 Docker 集成测试 | 单元和契约测试 |
| DG-007 LangSmith 裁判与预算 | M6 运行 LLM 裁判前 | LLM 裁判实验 | 确定性评分与本地评测门禁 |
| DG-008 新账号、付费服务或外部数据 | 首次需要前 | 对应真实服务验证 | 不依赖该资源的全部工作 |

DG-001 的批准版本记录在 [M0 运行时与 Docker 兼容性基线](../../decisions/001-m0-runtime-and-docker-baseline.md)。后续不得静默升级；需要变更时必须更新决策记录并重新批准。

## 3. 里程碑执行顺序

### M0：实施前基线与 Docker 兼容性

**目标：** 获得可实施的版本和环境事实，不修改现有 Docker 服务的配置与数据；运行时验证只能在批准后临时启动并恢复原状态。

**工作：**

- 只读记录本机 PostgreSQL 与 Milvus 的服务端版本、部署模式和健康状态。
- 验证 PostgreSQL 是否允许创建隔离 database/role，并确认 `vector` 扩展可用性。
- 验证 Milvus 是否支持计划要求的稠密向量、BM25/稀疏检索、混合搜索和隔离 database/collection。
- 记录 Neo4j 当前是否可用；不可用时只标记 GraphRAG 集成阻塞，不阻塞白名单逻辑单元测试。
- 基于官方兼容矩阵提出 DG-001 和 DG-006 候选，交由用户批准。
- 将批准结果写入版本化决策记录；只记录环境变量名称和非敏感连接能力。

**完成门：**

- DG-001 已批准，可安全创建依赖清单。
- PostgreSQL 与 Milvus 的“已验证能力、未验证边界、阻塞项”均有记录。
- DG-006 至少批准现有 PostgreSQL 与 Milvus 的测试资源方案。
- 未重建、删除或升级任何现有容器、数据库、volume 或 collection；临时启动的现有容器已恢复原状态。

### M1：工程骨架与测试守卫

**前置：** M0 完成，DG-001 已批准。

**执行：**

1. [Agent、API 与中间件计划 Task 1](2026-08-31-agent-api-and-middleware.md#task-1-项目测试入口与可信运行时上下文)：建立 `pyproject.toml`、pytest 配置、可信运行时上下文和稳定错误类型。
2. [评测与可观测性计划 Task 1](2026-08-31-evaluation-and-observability.md#task-1-测试分层配置与外部访问守卫)：加入默认 socket 禁止、marker 校验和真实服务 opt-in 守卫。

**完成门：**

- 首个有效 RED 和最小 GREEN 已记录。
- 默认测试路径无法访问 Docker、模型或外网。
- `unit` 与 `contract` marker 可独立运行，未知 marker 会失败。
- `git diff --check` 通过，依赖锁与批准版本一致。

### M2：稳定契约与纯领域核心

**前置：** M1 完成。

**执行：**

1. Agent 计划 Tasks 2～4：线程绑定、单运行锁、应用事件、SSE、FastAPI/CLI 和 decision 契约，全部使用确定性替身。
2. 检索计划 Task 1：统一 Evidence、Citation、Artifact 与引用完整性校验。
3. 订单计划 Tasks 1～3：订单状态机、客户隔离端口、operation 预览、哈希、审批和幂等核心。
4. 长期记忆计划 Tasks 1～4：明确记忆意图、敏感拒绝、DTO、客户隔离操作和冲突策略。

**完成门：**

- 可信 `customer_id` 不进入任何模型可见工具 schema。
- SSE、错误码、引用、订单状态机、审批哈希和记忆策略契约通过。
- 所有验证均明确标记为单元/契约结果，不宣称真实持久化或真实模型能力。

### M3：PostgreSQL 业务持久化

**前置：** M2 完成；PostgreSQL 对应 DG-006 已批准。Mem0 真实向量 schema 还需要相关向量模型与维度批准。

**执行：**

1. 订单计划 Task 4：实现创建、修改、取消、退货以及 PostgreSQL 原子事务。
2. 订单计划 Task 5：实现受控 SELECT、AST 白名单、客户过滤、只读事务、LIMIT 与超时。
3. 长期记忆计划 Task 5：实现 Mem0 `AsyncMemory` 与独立 PostgreSQL/pgvector database/role 适配。

**完成门：**

- 写操作保持“预览 → approve/reject → 幂等执行”，重复批准不会产生第二次副作用。
- 状态变更、operation 结果和审计位于同一事务。
- NL2SQL 无法执行非 SELECT、跨客户查询或无界查询。
- 订单/checkpoint 与 Mem0 使用隔离的逻辑数据库和角色。
- 集成结果只证明隔离测试资源，不外推到生产部署。

### M4：检索、图谱与索引发布

**前置：** M2 完成；Milvus 对应 DG-006 已批准。真实 schema、默认参数和发布分别受 DG-003、DG-004、DG-005 控制。

**执行：**

1. 检索计划 Task 2：BGE-M3 dense、Milvus BM25、融合、rerank 接口和 small-to-big。
2. 检索计划 Task 3：RAPTOR 确定性树结构、完整性校验和逐层下钻。
3. 检索计划 Task 4：Neo4j 固定 schema、白名单查询模板及 Tavily 外部时效边界。
4. 检索计划 Task 5：离线构建报告、候选验证和原子版本发布。

**完成门：**

- 三种 RAG 都返回可追溯证据，模型回答无法引用不存在的 evidence ID。
- 常规 RAG 与 RAPTOR 使用隔离的 Milvus database/collection。
- GraphRAG 不能执行模型生成的任意 Cypher。
- 失败构建不会替换当前已发布索引。
- Neo4j 不可用时，GraphRAG 集成保持明确阻塞，其他检索结果单独报告。

### M5：Agent 组装、治理与持久恢复

**前置：** M2 完成；M3、M4 提供稳定工具接口；DG-002 在真实模型工厂前获批。

**执行：**

1. Agent 计划 Task 5：组装单 `create_agent`、中文客服提示词、异步工具中间件、调用上限、有限重试、HITL 和会话摘要。
2. 接入 PostgreSQL `AsyncPostgresSaver`，验证暂停、服务适配器重建和同一 thread 恢复。
3. 使用确定性模型跑通全部工具路由，再以显式 opt-in 验证获批 DeepSeek 模型。

**完成门：**

- 只有一个客服 Agent 实例，所有工具名称和职责符合 Spec。
- 异步流使用异步工具中间件路径；写工具不进入通用重试。
- 未审批写操作不会发生，错误客户无法恢复他人 thread。
- SSE 每次正常或错误运行都以一个稳定终止事件结束。
- scripted model、PostgreSQL 恢复和真实模型验证边界分别报告。

### M6：评测、可观测性与发布门禁

**前置：** M3～M5 的可用能力已明确；缺失的外部服务可以形成单独阻塞报告，但不能被记为通过。

**执行：**

1. 评测与可观测性计划 Task 2：建立约 60 条版本化固定场景。
2. Task 3：实现确定性评分器、显式分母、P95 和阻断门禁。
3. Task 4：实现 Trace 层级、脱敏、大小预算和 best-effort sink。
4. Task 5：实现 LangSmith opt-in 实验、裁判决策门和失败报告。

**完成门：**

- 安全与状态机 100%，工具选择不低于 90%，知识正确率不低于 85%，引用正确率不低于 95%。
- 三种 RAG 的 Hit@5 分别不低于 80%，内部检索 P95 不高于 2 秒。
- 确定性安全失败不能被平均值或 LLM 裁判覆盖。
- Trace 不包含密钥、完整敏感字段、原始 SQL 或未压缩 artifact。
- LangSmith 不可用不阻断核心客服服务，但对应 live 验证不得记为通过。

## 4. 每个任务的固定执行循环

每次只从当前子计划领取一个 Task，并按以下顺序执行：

1. 读取对应 Spec 需求 ID、子计划 Task 和已有公开接口。
2. 写一个最小失败测试并运行，确认失败来自目标行为缺失。
3. 把 RED 命令、退出码、断言差异和有效性写入记录。
4. 写通过当前测试所需的最少生产代码。
5. 运行相同测试取得 GREEN，再运行受影响的单元或契约回归。
6. 仅在全绿后做必要重构，并重新运行相同验证。
7. 运行 `git diff --check` 和敏感信息检查。
8. 提交当前 Task 所需文件；提交说明列出需求 ID 和未验证边界。
9. 在进入下一个 Task 前检查本主计划的前置条件和决策门。

## 5. 暂停与升级规则

- RED 因导入、依赖、语法或连接失败而失败时，不得写生产实现；先修复测试环境并重新取得有效 RED。
- 现有 Docker 服务与计划版本不兼容时，停止对应集成任务，提供适配、并行部署或升级三种方案及影响，不自行修改容器。
- 发现需要新增工具、修改稳定 API/SSE 事件、改变订单状态机或放宽客户隔离时，先修改 Spec 并等待用户批准。
- 需要账号、密钥、付费调用或外部数据时，触发 DG-008；没有明确授权不得继续真实调用。
- 某个外部服务阻塞时，继续执行不依赖它的确定性任务，并在验收报告中单独列出阻塞边界。

## 6. 项目完成定义

只有同时满足以下条件，首版项目才可标记为完成：

- [需求—测试矩阵](../../tdd/010-test-matrix.md) 中所有阻断需求族都有通过证据。
- 五份子计划中的全部 Task 已完成并具有可复核的 RED→GREEN 记录。
- PostgreSQL checkpointer、订单事务、Mem0 pgvector、Milvus 与 Neo4j 的集成边界分别验证和报告。
- 固定评测达到 M6 阈值，所有安全与状态机场景通过。
- 真实模型和外部服务验证均为显式 opt-in，未验证项没有被描述为通过。
- 仓库不包含密钥、`.env` 内容、真实客户数据或未批准的隐含默认参数。
- `git diff --check`、快速测试、相关集成测试和评测报告均与最终提交对应。

## 7. 当前实施入口

M0 已完成，DG-001 与 DG-006 已批准。下一阶段为 M1；开始写代码前仍需用户明确要求执行，首个切片固定为 Agent 计划 Task 1，不提前进入其他子计划任务。
