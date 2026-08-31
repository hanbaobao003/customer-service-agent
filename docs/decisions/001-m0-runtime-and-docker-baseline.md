# M0 运行时与 Docker 兼容性基线

**状态：** 已批准，M0 完成

**记录日期：** 2026-08-31

**批准日期：** 2026-08-31

**关联决策门：** DG-001、DG-003、DG-006

**主计划：** [智能客服 Agent 主项目实施计划](../superpowers/plans/2026-08-31-project-master-plan.md)

## 1. 范围与安全边界

本记录只包含版本、镜像摘要、架构、端口、健康状态和非敏感能力。检查没有读取或记录 `.env` 内容、数据库密码、API Key、业务数据、数据库名称清单或集合内容。

现有 PostgreSQL 与 Milvus 在检查前均为停止状态。本轮临时启动后只执行健康检查和 PostgreSQL 元数据查询，检查结束已恢复停止状态；没有创建、删除或修改数据库、role、schema、collection、volume 或业务记录。

## 2. Docker 主机

| 项目 | 已验证值 |
|---|---|
| Docker Desktop | 4.80.0 |
| Docker Engine | 29.6.1 |
| Docker API | 1.55 |
| 容器主机 | Linux ARM64 |

结论：三个目标数据库均需要 ARM64 镜像。现有 PostgreSQL、Milvus 与新下载的 Neo4j 镜像均已确认是 `linux/arm64`。

## 3. PostgreSQL 与 pgvector

| 项目 | 已验证值 |
|---|---|
| 容器 | `shared-postgres` |
| 镜像 | `pgvector/pgvector:0.8.6-pg17-bookworm` |
| 镜像摘要 | `sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f` |
| PostgreSQL | 17.11 |
| pgvector | 0.8.6，已安装于当前数据库 |
| 向量索引 | IVFFlat、HNSW 扩展能力已注册 |
| 管理能力 | 当前管理角色具有创建 database 和 role 的权限 |
| 端口 | `127.0.0.1:5432` |
| 运行检查 | 容器健康检查通过，只读 SQL 成功 |
| 检查后状态 | 已恢复停止 |

[pgvector 官方说明](https://github.com/pgvector/pgvector)确认 0.8.6 提供 PostgreSQL 17 Docker 标签，并支持 PostgreSQL 13+。

**适配结论：** 该服务满足 LangGraph PostgreSQL checkpointer、订单事务和 Mem0 PGVector 的基础版本能力。真正的逻辑 database/role 隔离、事务恢复和 Mem0 向量读写必须在 L3 集成测试中验证，当前检查不能替代这些测试。

## 4. Milvus

| 项目 | 已验证值 |
|---|---|
| 主容器 | `milvus-standalone` |
| 镜像 | `milvusdb/milvus:v3.0.0` |
| 镜像摘要 | `sha256:49371c30af46b1013e4d3e0b980e691d81376d69cdbe1b372725baf1d7255862` |
| 部署模式 | Standalone，依赖 etcd 与 MinIO |
| 主端口 | `127.0.0.1:19530` |
| 管理端口 | `127.0.0.1:9091` |
| 运行检查 | etcd、MinIO、Milvus 均通过容器健康检查；Milvus 容器内部 `/healthz` 返回 `OK` |
| 检查后状态 | 三个容器均已恢复停止 |

[Milvus 3.0 发布说明](https://milvus.io/docs/release_notes.md)记录了稀疏向量与 BM25 能力；[Milvus 混合检索文档](https://milvus.io/docs/milvus_hybrid_search_retriever.md)确认 Standalone 支持 dense + 内置 BM25；[PyMilvus 兼容表](https://milvus.io/api-reference/pymilvus/v3.0.x/About.md)推荐 Milvus 3.0.x 使用 PyMilvus 3.0.1。

**适配结论：** 现有部署适合常规 RAG 与 RAPTOR 的 Milvus 后端。当前未创建测试 collection，也未执行真实 dense/BM25 混合检索，因此融合、索引、database/collection 隔离和 P95 仍属于 L3 集成测试边界。

## 5. Neo4j 与 neo4j-graphrag

| 项目 | 已验证值 |
|---|---|
| 数据库版本 | Neo4j Community 2026.07.1 |
| Docker 镜像 | `neo4j:2026.07.1` |
| 镜像摘要 | `sha256:dbc377fb9cd8fe8dabc19d3041b197d5ca0ef8bae514cea175b8df265e5b7a76` |
| 镜像架构 | Linux ARM64 |
| Python 库 | `neo4j-graphrag==1.19.0`，实施到 M4 时安装 |
| Compose | `infra/neo4j/compose.yaml` |
| 计划端口 | `127.0.0.1:7474`、`127.0.0.1:7687` |
| 持久化 | Docker named volumes `wang-agent-neo4j-data`、`wang-agent-neo4j-logs` |
| 当前状态 | 容器已创建并通过验证，检查后已停止 |

[Neo4j Docker 文档](https://neo4j.com/docs/operations-manual/current/docker/introduction/)推荐固定使用 `neo4j:2026.07.1`；[Neo4j GraphRAG 文档](https://neo4j.com/docs/neo4j-graphrag-python/current/)说明该 Python 包支持 Neo4j 2026.01+ 和 Python 3.13。

Compose 配置强制要求本地 `NEO4J_AUTH`，且只把该变量传入容器。用户设置变量后，Compose 配置验证通过；检查过程没有读取或输出变量值。

运行验证确认：

- Neo4j 日志报告版本 2026.07.1，并正常启用 Bolt `7687` 与 HTTP `7474`；
- 容器内部 HTTP 请求成功；
- 通过容器内 `cypher-shell` 执行只读 `RETURN 1 AS ok`，返回 `1`；
- Docker 端口映射确认绑定 `127.0.0.1:7474` 与 `127.0.0.1:7687`；
- 当前命令沙箱无法直接访问 Docker Desktop 的宿主映射端口，因此宿主进程连通性不计为已验证；
- 检查结束使用 Compose `stop` 停止容器，容器与两个 named volumes 均保留。

**适配结论：** 版本、架构、容器内部 HTTP 与 Bolt/Cypher 均兼容。确定性数据导入、白名单模板、路径证据和 Python Driver/`neo4j-graphrag` 集成仍属于 M4/L3 测试边界。

## 6. DG-001 批准版本

| 组件 | 批准版本 | 证据与状态 |
|---|---:|---|
| Python | 3.13.15 | [Python 官方维护版本](https://www.python.org/downloads/release/python-31315/)；已批准 |
| LangChain | 1.3.18 | [PyPI 稳定发布](https://pypi.org/project/langchain/)；已批准 |
| LangGraph | 1.2.11 | [PyPI 稳定发布](https://pypi.org/project/langgraph/)；支持 Python 3.13；已批准 |
| Mem0 | 2.0.19 | [PyPI 稳定发布](https://pypi.org/project/mem0ai/)；要求 Python 3.10+；已批准 |
| PostgreSQL | 17.11 | 现有服务运行验证通过；已批准 |
| pgvector | 0.8.6 | 现有扩展运行验证通过；已批准 |
| Milvus | 3.0.0 | 现有服务健康验证通过；已批准 |
| PyMilvus | 3.0.1 | 官方兼容表推荐；已批准 |
| Neo4j Community | 2026.07.1 | 已批准，镜像已安装 |
| neo4j-graphrag | 1.19.0 | 已批准，M4 才安装 Python 依赖 |

LangGraph PostgreSQL checkpointer 使用官方 `langgraph-checkpoint-postgres` 包；确切包版本将在创建依赖清单前与 LangGraph 1.2.11 一起解析并锁定，不使用未记录的浮动版本。

## 7. DG-006 批准资源规则

- PostgreSQL 与 Milvus 继续复用现有本机 Docker 服务，不编写替代 Compose，也不升级现有容器。
- PostgreSQL 集成测试必须创建带安全前缀的独立 database/role；订单/checkpoint 与 Mem0 不得共用逻辑数据库或角色。
- Milvus 集成测试必须使用带安全前缀和 `test_run_id` 的独立 database/collection；常规 RAG 与 RAPTOR 不得共用 collection。
- Neo4j 使用项目内 Compose、loopback 端口和独立 named volumes；认证保持开启。
- 资源名称、连接字符串结构和测试清理流程在首次 L3 集成任务前单独提交审核。

## 8. M0 完成结论

- DG-001 版本整组已批准，M1 可以按批准版本创建 `pyproject.toml` 和锁文件。
- DG-006 资源规则已批准，首次 L3 测试仍必须在创建资源前验证安全前缀、`test_run_id` 和资源归属。
- M0 没有剩余阻塞；尚未开始 M1，也未创建业务代码或安装 Python 依赖。

## 9. M3 SQL 执行参数补充批准

2026-09-01，用户在参数候选后以“继续”批准以下受控 SQL 参数：

- 最大模型可见返回行数：`50`；
- PostgreSQL `statement_timeout`：`2000 ms`。

参数写入 `src/customer_service_agent/config.py`。单元测试可使用更小的显式值验证边界；生产组装必须使用上述批准值，变更时需再次记录用户批准。

## 10. DG-003 与 M3 长期记忆参数批准

2026-09-01，用户批准以下向量模型和 Mem0 PGVector 参数：

- embedding 模型：`BAAI/bge-m3`；
- embedding 维度：`1024`；
- reranker：`BAAI/bge-reranker-v2-m3`；
- Mem0 collection：`customer_memories_v1`；
- Mem0 PGVector 索引：HNSW，关闭 DiskANN，不增加 pgvectorscale 依赖。

同时批准将相反偏好的替换语义修订为 Mem0 公共 API 支持的原地原子更新：保留原 `memory_id`，在一次 PGVector update 中更新正文、向量和 payload，并只记录一条 `replace_update` 审计。实现不得通过独立 `delete` + `add` 冒充原子替换，也不得直接依赖 Mem0 私有表结构。

上述 embedding 和 reranker 选择关闭 DG-003。真实外部模型调用仍必须使用显式 live opt-in；默认单元与 PostgreSQL 集成测试使用确定性 embedding 替身，不读取或记录 API Key。
