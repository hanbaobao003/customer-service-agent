# 智能客服 Agent TDD 执行规范

**状态：** 待用户审阅

**版本：** 1.0

**上位规格：** [总体规格](../specs/000-customer-service-agent-overview.md) · [评测与可观测性规格](../specs/050-evaluation-and-observability.md)

## 1. 目的

本文定义本项目从规格到实现的测试先行流程。它约束测试分层、RED–GREEN–REFACTOR 证据、外部服务隔离、测试数据安全和阶段验收，不定义产品行为；产品行为以六份 SDD Spec 为准。

当前阶段只编写和审阅文档。用户批准实施计划前，不创建 Python 项目配置、不安装依赖、不写生产代码、不启动或修改 Docker 服务。

## 2. 强制原则

1. 每个新生产行为必须先有一个最小失败测试。
2. RED 必须因目标行为尚未实现而失败；导入错误、依赖缺失、语法错误、连接失败和测试数据错误不算有效 RED。
3. GREEN 只实现通过当前失败测试所需的最少行为。
4. REFACTOR 只能在相关测试和全量快速测试均为绿色后进行。
5. 修复缺陷时，先增加能够复现缺陷的失败测试，再修改生产代码。
6. 单元测试不得连接 PostgreSQL、Milvus、Neo4j 或任何模型、搜索、embedding、Mem0 云服务和 LangSmith 服务。
7. 测试不得依赖执行顺序、开发者个人数据、个人容器中的既有业务数据或未版本化的网络响应。
8. 假模型、假 repository 和测试替身只能证明应用逻辑；不能声称证明真实模型、数据库事务或服务重启恢复。
9. 任何跳过测试必须带稳定原因，并且不得被计入质量门禁的通过样本。
10. 密钥、连接密码、完整地址、完整电话、支付信息和 `.env` 内容不得进入测试输出、快照、Trace 或提交。

## 3. 测试分层

| 层级 | 目标 | 允许依赖 | 禁止依赖 | 默认执行 |
|---|---|---|---|---:|
| L1 单元测试 | 领域规则、纯转换、策略、状态机 | 标准库、被测模块、内存测试替身 | Docker、网络、真实 SDK 客户端 | 是 |
| L2 契约测试 | FastAPI、SSE、工具 DTO、artifact、错误码、端口协议 | 本地 ASGI、schema、确定性 fake | 真实数据库、真实模型、外网 | 是 |
| L3 Docker 集成测试 | PostgreSQL 事务/检查点、pgvector、Milvus、Neo4j | 隔离 Docker 测试资源 | 开发或生产业务数据、付费 API | 显式标记 |
| L4 真实服务验证 | DeepSeek、Tavily、SiliconFlow、Mem0 适配、LangSmith 上传 | 明确 opt-in 的真实配置 | 普通 CI 默认路径 | 显式 opt-in |
| L5 固定评测 | 约 60 条版本化场景和质量门禁 | 固定代码/数据/索引/模型引用 | 未记录版本的临时样本 | 发布门禁 |

### 3.1 L1 单元测试

- 测试文件按能力与生产模块一一对应。
- 优先测试真实领域对象和内存端口实现，不用 mock 调用次数代替业务断言。
- 每个测试只验证一个主要行为；测试名描述输入条件与可观察结果。
- 时间、UUID、哈希和重试等待通过显式端口注入，测试中使用固定值。
- 订单和记忆客户隔离必须断言“不可观察到其他客户存在性”，不能只断言空列表。

### 3.2 L2 契约测试

- 使用 ASGI test client 调用 FastAPI，不启动真实监听端口。
- 检查请求拒绝、响应 schema、SSE 事件名称、序号、终止事件和脱敏字段。
- 工具 schema 必须证明模型可见参数不包含 `customer_id`、凭证、连接串和服务端 operation ID。
- 对 artifact 只验证稳定字段和类型，不对无稳定性承诺的内部顺序做快照。

### 3.3 L3 Docker 集成测试

- PostgreSQL 使用独立测试 database/role；Mem0 pgvector 使用另一个独立测试 database/role。
- Milvus 使用测试专用 database/collection 前缀和版本；Neo4j 使用测试 database 或唯一 run marker。
- 每次运行生成 `test_run_id`，所有测试资源名称均由固定安全前缀和该 ID 构成。
- 清理命令只能删除本次运行创建且带安全前缀的资源；不允许通配删除数据库、volume 或 collection。
- 集成测试开始前执行资源归属检查；检查失败立即停止，不尝试“自动修复”。
- PostgreSQL、Milvus、Neo4j 分组可以独立运行和报告，单一服务缺失不能伪装为其他分组通过。

### 3.4 L4 真实服务验证

- 使用独立 pytest marker 和环境开关；缺少开关时报告 skipped。
- 报告记录服务、模型 ID、日期、代码提交、数据版本、用例和边界，不记录密钥值。
- 网络超时、429 和 5xx 与领域断言分开统计。
- SDK 调用只能报告为 SDK 验证；没有运行 CLI 时不得写成 CLI 验证。

## 4. 目标目录结构

以下目录是实施计划的目标，不代表当前已经创建：

```text
src/customer_service_agent/
tests/
  unit/
    agent/
    retrieval/
    orders/
    sql/
    memory/
    evaluation/
  contract/
  integration/
    postgres/
    milvus/
    neo4j/
  live/
  fixtures/
evals/
  datasets/
  scorers/
docs/tdd/
docs/superpowers/plans/
```

公共 fixture 只保存稳定测试构造器和版本化模拟数据。面向单一测试文件的辅助函数留在该文件，避免形成不可理解的全局 fixture 网络。

## 5. RED–GREEN–REFACTOR 记录协议

每个实施任务维护一个简短记录，格式见 [RED–GREEN 记录模板](020-red-green-record-template.md)。记录至少包含：

- 对应 Spec 需求 ID；
- 被测公开行为；
- RED 命令、退出码和核心断言差异；
- RED 为何是目标行为缺失，而不是环境错误；
- GREEN 的最小实现范围；
- GREEN 命令和通过数量；
- REFACTOR 内容或明确写“无重构”；
- 全量快速测试结果；
- 未验证边界。

终端完整日志不要求提交；记录只保留足以复核的命令、结果摘要和边界，禁止复制密钥或大段供应商响应。

## 6. 测试命名与标记

### 6.1 文件与测试名

- 文件：`test_<被测能力>.py`。
- 测试：`test_<条件>_<期望结果>`。
- 场景 ID 放在 docstring 或 pytest metadata 中，例如 `API-SCN-002`。
- 同一个测试不得同时承担不相关需求；存在两个主要断言目标时拆分测试。

### 6.2 pytest markers

计划使用以下稳定 marker：

| marker | 含义 |
|---|---|
| `unit` | 不访问外部系统的快速测试 |
| `contract` | schema、ASGI、SSE 和端口协议测试 |
| `integration_postgres` | PostgreSQL、checkpointer、pgvector 集成 |
| `integration_milvus` | Milvus 检索和索引集成 |
| `integration_neo4j` | Neo4j 图导入和查询集成 |
| `live_model` | 真实模型调用 |
| `live_web` | Tavily 真实调用 |
| `live_embedding` | SiliconFlow 真实 embedding/rerank 调用 |
| `live_langsmith` | LangSmith 上传和实验 |
| `eval_gate` | 固定评测集与阻断阈值 |

未知 marker 必须使测试配置检查失败，避免拼写错误导致测试静默进入默认路径。

## 7. 计划命令契约

实施获批后，项目统一从仓库根目录运行命令。具体依赖清单由 Agent/API 基础计划创建并锁定；计划命令形态如下：

```bash
UV_CACHE_DIR=.uv-cache uv sync --python 3.13
UV_CACHE_DIR=.uv-cache uv run pytest -m unit -q
UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_postgres -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus -q
UV_CACHE_DIR=.uv-cache uv run pytest -m integration_neo4j -q
UV_CACHE_DIR=.uv-cache uv run pytest -m eval_gate -q
```

`UV_CACHE_DIR` 使用项目内忽略目录，避免依赖用户全局缓存权限。真实服务命令还必须带相应显式开关；没有开关只能 skipped。

## 8. 端口与测试替身

外部系统通过窄接口隔离：

| 端口 | 单元测试替身 | Docker/真实适配器 |
|---|---|---|
| `CheckpointStore` | 内存检查点替身 | `AsyncPostgresSaver` 适配器 |
| `ThreadBindingRepository` | 字典实现 | PostgreSQL repository |
| `OrderRepository` | 事务语义内存实现 | PostgreSQL repository |
| `SqlReadPort` | 固定行集实现 | PostgreSQL 只读角色 |
| `HybridSearchPort` | 固定 dense/BM25 命中 | Milvus client |
| `GraphSearchPort` | 固定路径实现 | Neo4j driver |
| `WebSearchPort` | 固定 URL 结果 | Tavily adapter |
| `MemoryStorePort` | 客户隔离内存实现 | Mem0 PGVector adapter |
| `ModelPort` | 确定性 scripted model | DeepSeek LangChain adapter |
| `TraceSink` | 内存事件收集器 | LangSmith adapter |

测试替身必须复现公开语义，如客户过滤、乐观锁、幂等结果和暂时性错误分类；不得为了测试方便暴露生产接口中不存在的后门。

## 9. 合并与提交门禁

每个实施任务只有满足以下条件才可提交：

1. 已记录有效 RED；
2. 目标测试 GREEN；
3. 受影响模块的全部单元/契约测试通过；
4. `git diff --check` 通过；
5. 新公开接口有类型和契约测试；
6. 没有密钥、`.env` 值、连接密码或真实客户数据进入 diff；
7. 提交只包含该任务需要的生产代码、测试和必要文档；
8. 提交说明列出需求 ID 和未验证边界。

Docker 集成、真实服务和评测门禁在对应阶段单独提交，不能用单元测试通过替代。

## 10. 完成定义

TDD 流程本身的完成条件是：

- [需求—测试矩阵](010-test-matrix.md) 中每个阻断需求族均映射到实施计划和预期测试层；
- 五份子 Spec 各有独立实施计划；
- 每个计划的任务均包含 RED、GREEN、相关回归和提交步骤；
- 参数决策门未批准时，计划明确停止点和可继续的纯测试范围；
- 文档不存在未决占位标记、含糊的测试描述或未定义接口；
- 用户审阅批准后才进入代码实施。
