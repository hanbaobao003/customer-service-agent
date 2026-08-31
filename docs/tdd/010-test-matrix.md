# 需求—测试层追踪矩阵

**状态：** 待用户审阅

**版本：** 1.0

**流程规范：** [TDD 执行规范](000-tdd-strategy.md)

本矩阵按需求族追踪测试责任。单个需求的精确测试函数、文件和 RED 命令由对应实施计划定义。

| 需求族 | Spec | 主要测试层 | Docker/真实边界 | 阻断条件 |
|---|---|---|---|---|
| AGT-001…006 | 010 | unit、contract | DeepSeek 仅 live | 单 Agent、事实来源或提示词边界失败 |
| CTX-001…005 | 010 | unit、contract、integration_postgres | 线程绑定需 PostgreSQL | 任意客户越权 |
| API-001…004 | 010 | contract | 无 | 输入、并发或断开语义失败 |
| HITL-001…006 | 010 | unit、contract、integration_postgres | 恢复必须真实 checkpointer | 未审批写入或错误恢复 |
| SSE-001…004 | 010 | unit、contract | 无 | 序号、终止事件或敏感字段失败 |
| MW-001…006 | 010 | unit、contract | 真实模型仅补充验证 | 异步链路、重试或调用上限失败 |
| ERR-001…003、HOF-001…004 | 010 | unit、contract | 无 | 泄露内部错误或虚构工单 |
| RET-COM-001…004 | 020 | unit、contract | 无 | 无界上下文、伪证据或在线写入 |
| RET-HYB-001…004 | 020 | unit、integration_milvus | embedding/rerank 另做 live | 错 collection、不可复现融合或引用断链 |
| RET-RAP-001…004 | 020 | unit、integration_milvus | 摘要模型参数受 DG-005 阻塞 | 摘要节点不可追溯 |
| RET-GRA-001…004 | 020 | unit、integration_neo4j | 无任意 Cypher | 白名单或路径证据失败 |
| RET-WEB-001…005 | 020 | unit、live_web | Tavily 显式 opt-in | 用网页回答内部规则 |
| CIT-001…004 | 020 | unit、contract、eval_gate | 无 | 引用不存在或不支持结论 |
| IDX-001…006 | 020 | unit、integration_milvus、integration_neo4j | 发布参数受决策门控制 | 失败构建覆盖线上版本 |
| ORD-STATE-001…004 | 030 | unit、integration_postgres | 无 | 状态机或事务原子性失败 |
| ORD-READ/CREATE/UPDATE/CANCEL/RETURN | 030 | unit、contract、integration_postgres | 无 | 越权、未审批写入或错误承诺 |
| ORD-HITL-001…004 | 030 | unit、integration_postgres | 与 010 的 interrupt 契约集成 | 哈希、绑定或过期校验失败 |
| ORD-IDEM-001…004 | 030 | unit、integration_postgres | 无 | 重复批准产生第二次副作用 |
| ORD-AUTH-001…004 | 030 | unit、contract、integration_postgres | 无 | 任意跨客户可观察性 |
| SQL-001…008 | 030 | unit、contract、integration_postgres | 行数/超时受决策门控制 | 非 SELECT、越权或只读约束失败 |
| MEM-POLICY-001…005 | 040 | unit | 无 | 非明确意图写入或敏感落库 |
| MEM-DATA-001…004 | 040 | unit、contract | 无 | 模型 schema 暴露客户 ID 或内部向量 |
| MEM-WRITE/RECALL/LIST/DELETE | 040 | unit、integration_postgres | Mem0 PGVector adapter | 客户越权、删除泄露或事实替代 |
| MEM-STORE-001…006 | 040 | contract、integration_postgres | 独立 database/role | 复用核心数据库或动态表名 |
| MEM-CONFLICT-001…003 | 040 | unit | 无 | 未授权覆盖长期偏好 |
| TDD-001…006 | 050 | 流程审计 | 无 | 缺少有效 RED 证据 |
| TEST-001…013 | 050 | test-of-tests、CI 配置 | 各层独立 | 外部依赖进入默认测试或验证误报 |
| EVAL-001…005 | 050 | unit、eval_gate | LangSmith 上传另做 live | 样本不可复现或无版本 |
| MET-001…009 | 050 | unit、eval_gate | 性能使用内部耗时 | 阻断阈值未达标 |
| JUDGE-001…004 | 050 | unit、live_langsmith | DG-007 批准前禁用 LLM 裁判 | 裁判覆盖确定性安全失败 |
| OBS-001…005 | 050 | unit、contract、live_langsmith | LangSmith 显式 opt-in | 泄露或追踪失败阻断核心业务 |

## 计划映射

| 子 Spec | 实施计划文件 | 首个可独立验收切片 |
|---|---|---|
| 010 Agent、API 与中间件 | `docs/superpowers/plans/2026-08-31-agent-api-and-middleware.md` | 可信运行时上下文与错误/SSE 契约 |
| 020 检索与索引 | `docs/superpowers/plans/2026-08-31-retrieval-and-indexing.md` | 统一证据 DTO、artifact 和引用校验 |
| 030 订单与 SQL | `docs/superpowers/plans/2026-08-31-orders-and-sql.md` | 订单状态机与客户隔离端口 |
| 040 长期记忆 | `docs/superpowers/plans/2026-08-31-long-term-memory.md` | 明确意图与敏感信息策略 |
| 050 评测与可观测性 | `docs/superpowers/plans/2026-08-31-evaluation-and-observability.md` | 确定性评分器和测试分层守卫 |

## 追踪维护规则

1. 新需求 ID 必须先进入对应 Spec，再进入本矩阵和实施计划。
2. 测试移动文件时同步更新实施计划的最终文件清单，不改变需求语义。
3. 一个需求由多层测试共同证明时，记录每层验证边界，禁止用其中一层代替全部。
4. 需求删除或替换必须保留 Spec 版本记录，不能只删除失败测试。
