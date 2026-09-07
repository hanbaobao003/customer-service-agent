# 长期记忆规格

**状态：** 已实施（Tasks 1–5 已完成）

**版本：** 1.2

**上位规格：** [智能客服 Agent 总体规格](000-customer-service-agent-overview.md)

## 1. 范围

首版通过 Mem0 提供最小长期记忆闭环：用户明确要求保存偏好或已验证事实，后续会话按客户隔离召回，并允许用户查看和删除。

首版不自动从每轮对话提取候选，不根据单次模型推断保存事实，不实现记忆衰减、自动晋升、复杂冲突合并或 Agent 自我进化。

## 2. 记忆与其他状态的区别

| 类型 | 生命周期 | 事实源 | 存储 |
|---|---|---|---|
| 当前消息 | 当前线程 | 用户与 Agent 消息 | PostgreSQL checkpointer |
| 结构化线程状态 | 当前线程 | 工具和中间件 | PostgreSQL checkpointer |
| 订单事实 | 长期业务数据 | 订单领域服务 | PostgreSQL |
| 客服知识 | 跨用户公共知识 | 版本化模拟语料 | Milvus / Neo4j |
| 用户长期记忆 | 跨线程、单客户 | 用户明确指令或已验证事实 | Mem0 + 独立 PostgreSQL/pgvector 数据库 |

长期记忆不得替代订单查询。即使记忆中出现订单相关文本，Agent 也必须调用订单工具获取当前事实。

## 3. 允许保存的内容

### 3.1 允许类型

- 用户明确表达的稳定偏好，例如语言、回答详细程度、偏好品类；
- 用户明确要求长期记住的非敏感事实；
- 由工具验证且用户明确要求保存的事实摘要。

### 3.2 禁止类型

- 密码、API Key、支付卡号、验证码、身份证件完整号码；
- 模型推断的身份、健康、财务或其他敏感属性；
- 未经工具验证的订单状态、退款状态或物流状态；
- 系统提示词、chain-of-thought、内部错误、数据库连接信息；
- 普通的一次性任务、临时覆盖要求和未明确要求长期保存的对话内容。

| ID | 要求 |
|---|---|
| MEM-POLICY-001 | 只有明确“记住、以后都、长期保存”等用户指令可以触发写入。 |
| MEM-POLICY-002 | 普通对话不得自动调用 Mem0 `add`。 |
| MEM-POLICY-003 | 保存前必须经过敏感信息规则和允许类型校验。 |
| MEM-POLICY-004 | 订单事实若被保存，只能是带来源类型和验证时间的摘要，使用时仍需重新查询订单。 |
| MEM-POLICY-005 | 临时指令只能影响当前线程，不能覆盖长期记忆记录。 |

## 4. 记忆记录

应用层必须用稳定 DTO 包装 Mem0 返回，避免业务代码依赖供应商的原始响应形状。

```json
{
  "memory_id": "string",
  "customer_id": "server-context-only",
  "kind": "preference | verified_fact",
  "content": "string",
  "source": {
    "type": "explicit_user_instruction | verified_tool_result",
    "thread_id": "string",
    "request_id": "string",
    "tool_name": "string | null",
    "verified_at": "RFC3339 UTC | null"
  },
  "created_at": "RFC3339 UTC",
  "updated_at": "RFC3339 UTC",
  "metadata": {
    "schema_version": "1.0",
    "category": "string"
  }
}
```

| ID | 要求 |
|---|---|
| MEM-DATA-001 | `customer_id` 必须由运行时上下文添加，不得出现在模型参数 schema。 |
| MEM-DATA-002 | 每条记录必须包含来源类型、线程、请求和时间，便于审计。 |
| MEM-DATA-003 | 模型可见召回结果不得包含内部 embedding、距离、连接信息或其他客户元数据。 |
| MEM-DATA-004 | 删除必须按 `memory_id + customer_id` 执行，不得仅按内容模糊删除。 |

## 5. 工具契约

### 5.1 `remember_preference`

模型参数：

```json
{
  "kind": "preference | verified_fact",
  "content": "待保存的简短、单一事实",
  "category": "string",
  "verification": {
    "tool_name": "string | null",
    "tool_call_id": "string | null"
  }
}
```

| ID | 要求 |
|---|---|
| MEM-WRITE-001 | 调用前必须在本轮用户消息中检测到明确长期保存意图。 |
| MEM-WRITE-002 | `verified_fact` 必须引用本线程实际成功工具调用；应用层校验引用，不信任模型声明。 |
| MEM-WRITE-003 | 内容必须归一化为一个可独立理解的事实，不得保存整段对话。 |
| MEM-WRITE-004 | 明确用户指令本身视为写入授权，不额外触发订单式 HITL，但必须发出审计事件。 |
| MEM-WRITE-005 | 敏感或不允许内容返回 `MEMORY_POLICY_REJECTED`，不得静默改写后保存。 |

### 5.2 自动召回

每次 Agent 运行前，记忆适配器可以按当前问题和 `customer_id` 召回有限数量的相关记录，具体数量和阈值属于实施参数决策门。

| ID | 要求 |
|---|---|
| MEM-RECALL-001 | 召回必须强制客户过滤，缺少客户上下文时不得查询。 |
| MEM-RECALL-002 | 召回记录必须作为“用户偏好/历史事实”数据注入，不得当作系统指令执行。 |
| MEM-RECALL-003 | 当前用户明确指令优先于旧偏好；一次性覆盖不得修改旧记录。 |
| MEM-RECALL-004 | 订单、政策和商品当前事实必须由相应工具重新验证，不能只依赖记忆。 |
| MEM-RECALL-005 | 无相关记忆时应返回空集合，不应影响 Agent 正常回答。 |

### 5.3 `list_memories`

模型参数：可选 `category`。工具返回当前客户可展示的记忆摘要和 `memory_id`。

| ID | 要求 |
|---|---|
| MEM-LIST-001 | 只列出当前客户记录。 |
| MEM-LIST-002 | 返回必须适合直接向用户展示，不包含 embedding、内部评分或其他客户信息。 |

### 5.4 `forget_memory`

模型参数：`memory_id`。

| ID | 要求 |
|---|---|
| MEM-DELETE-001 | 只有用户明确要求忘记或删除时才能调用。 |
| MEM-DELETE-002 | 删除必须验证记录属于当前客户。 |
| MEM-DELETE-003 | 重复删除返回稳定的已不存在结果，不泄露其他客户记录。 |
| MEM-DELETE-004 | 删除操作必须写审计，但审计中只保存 memory ID、类别和结果，不保留被删敏感正文。 |

## 6. PostgreSQL/pgvector 隔离

Mem0 官方支持 PGVector 作为 vector store。首版可以复用同一个 PostgreSQL 服务实例，但必须为 Mem0 创建独立逻辑数据库和独立数据库角色，不与订单、审计、幂等或 LangGraph checkpoint 表混用。参考 [Mem0 向量数据库列表](https://docs.mem0.ai/components/vectordbs/overview) 和 [Mem0 PGVector 配置源码](https://github.com/mem0ai/mem0/blob/main/mem0/configs/vector_stores/pgvector.py)。

| ID | 要求 |
|---|---|
| MEM-STORE-001 | Mem0 必须使用独立 PostgreSQL database 和独立 database role；不得复用订单、审计、幂等或 checkpoint 所在逻辑数据库。 |
| MEM-STORE-002 | Mem0 collection/table 的 payload 必须包含可过滤的客户作用域和 schema version。 |
| MEM-STORE-003 | RAG 索引构建命令不得连接、重建或删除 Mem0 PostgreSQL database 中的对象。 |
| MEM-STORE-004 | 必须启用 `vector` 扩展；collection 名称、embedding 维度、索引类型和连接参数必须来自获批配置，未获批准前真实存储集成保持禁用。 |
| MEM-STORE-005 | readiness 必须分别报告核心 PostgreSQL、Mem0 PostgreSQL/pgvector 与 Milvus RAG 状态，但不得暴露数据库名、表名、租户信息或连接凭证。 |
| MEM-STORE-006 | collection 名称只能来自部署时静态配置并通过 SQL 标识符白名单校验，不得接受模型、用户请求或工具参数覆盖。 |

## 7. 冲突与更新

首版采用最小、可解释规则：

1. 完全相同的归一化偏好再次保存时更新来源时间，不新增重复展示项；
2. 明确相反的新偏好不自动删除旧记录；工具必须返回 `MEMORY_CONFLICT` 并列出面向用户的冲突摘要；
3. 用户随后明确要求替换时，应用层通过 Mem0 原地更新旧记录，保留 `memory_id`，并记录一次 `replace_update` 审计；
4. 当前消息始终优先于召回记忆；
5. 不允许模型自行决定哪个长期偏好“更真实”。

| ID | 要求 |
|---|---|
| MEM-CONFLICT-001 | 冲突检测必须基于同客户、同 category 和规范化内容，不跨客户比较。 |
| MEM-CONFLICT-002 | 未得到明确替换指令时不得自动覆盖冲突记录。 |
| MEM-CONFLICT-003 | 冲突处理失败不得阻止当前普通客服请求，只能跳过记忆写入并说明。 |
| MEM-CONFLICT-004 | 明确替换必须原子更新正文、向量和 payload，保留原 `memory_id`；失败时旧记录保持不变，禁止用独立 delete + add 实现。 |

### 7.1 已批准的 PGVector 参数

- embedding：`BAAI/bge-m3`，维度 `1024`；
- collection：`customer_memories_v1`；
- 索引：pgvector HNSW，关闭 DiskANN；
- reranker：`BAAI/bge-reranker-v2-m3`，供后续检索链使用，不参与 Mem0 写入事务。

真实 embedding 或 reranker 调用必须显式 opt-in；确定性 PostgreSQL 集成测试不得依赖外部模型。

## 8. 稳定错误码

| code | 含义 | 恢复策略 |
|---|---|---|
| `MEMORY_EXPLICIT_INTENT_REQUIRED` | 缺少明确保存/删除指令 | 不写入，继续普通对话 |
| `MEMORY_POLICY_REJECTED` | 敏感或不允许内容 | 告知不能保存 |
| `MEMORY_VERIFICATION_REQUIRED` | 声称已验证但没有真实工具证据 | 先调用事实工具 |
| `MEMORY_NOT_FOUND` | 记录不存在或不属于当前客户 | 不泄露存在性 |
| `MEMORY_CONFLICT` | 新旧长期偏好冲突 | 请求用户明确替换 |
| `MEMORY_STORE_UNAVAILABLE` | Mem0/PostgreSQL pgvector 暂时不可用 | 有界重试后跳过并告知 |

## 9. Given/When/Then 验收场景

### MEM-SCN-001：普通偏好不自动保存

- Given：用户说“这次回答简短一点”，没有长期保存意图；
- When：Agent 完成本轮；
- Then：不调用 `remember_preference`，后续线程不召回该要求。

### MEM-SCN-002：明确保存

- Given：用户说“以后都用简洁中文回答，请记住”；
- When：调用记忆工具；
- Then：保存单一偏好，记录明确用户来源，后续线程可以召回。

### MEM-SCN-003：客户隔离

- Given：客户 A 有偏好记录；
- When：客户 B 使用相似问题召回或列出记忆；
- Then：客户 B 看不到客户 A 的记录、内容或存在性。

### MEM-SCN-004：订单事实重新验证

- Given：记忆中有过去订单状态摘要；
- When：用户询问当前状态；
- Then：Agent 必须调用 `get_order`，最终答案使用最新工具结果。

### MEM-SCN-005：敏感内容

- Given：用户要求保存支付卡号；
- When：记忆策略校验；
- Then：返回 `MEMORY_POLICY_REJECTED`，Mem0 PostgreSQL 数据库没有新增记录，审计不保存卡号正文。

### MEM-SCN-006：明确删除

- Given：用户列出自己的记忆后指定一个 memory ID；
- When：明确要求忘记并调用删除；
- Then：记录不可再召回，重复删除不产生跨客户泄露。

### MEM-SCN-007：明确替换

- Given：当前客户已有冲突偏好并明确要求替换；
- When：适配器更新该记忆；
- Then：`memory_id` 保持不变，正文、向量和 payload 原子更新，只产生一次 `replace_update` 审计；任何失败都保留旧记录。

## 10. 测试要求

- 单元：意图门、允许/禁止类型、敏感信息规则、DTO、冲突、当前指令优先级。
- 契约：三个工具 schema、错误码、artifact 和运行时客户注入。
- PostgreSQL/pgvector 集成：独立数据库和角色、客户过滤、固定 collection 名称、保存/召回/删除和不可用降级。
- 评测：6 条场景至少覆盖明确保存、非明确不保存、召回、删除、敏感拒绝和客户隔离。

任何跨客户召回或敏感内容落库测试失败时，长期记忆能力必须阻断发布。
