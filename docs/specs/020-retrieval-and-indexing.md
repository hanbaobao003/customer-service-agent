# 检索与索引规格

**状态：** 已批准设计，实施中（Task 1 已完成）

**版本：** 1.1

**上位规格：** [智能客服 Agent 总体规格](000-customer-service-agent-overview.md)

## 1. 范围与知识域

本规格定义三种内部知识检索工具、联网搜索边界、证据与引用格式、模拟语料、离线全量索引和索引发布规则。

| 知识域 | 工具 | 技术路径 |
|---|---|---|
| 商品信息与 FAQ | `search_product_faq` | Milvus 稠密向量 + BM25 + 融合 + rerank + small-to-big |
| 政策与手册长文 | `search_policy_raptor` | RAPTOR 递归聚类与摘要层检索 |
| 商品、品牌、品类、活动关系 | `search_commerce_graph` | Neo4j 固定 schema + 白名单图查询 |
| 外部时效信息 | `web_search` | Tavily，只作为外部来源 |

三种内部 RAG 按知识域分工，不要求对同一问题做算法排行榜。离线评测仍必须分别验证各自目标知识域的证据命中率。

## 2. 统一检索契约

所有内部检索工具必须返回模型可见 `content` 和应用可见 `artifact` 两层结果。

### 2.1 模型可见内容

```json
{
  "answerable": true,
  "evidence": [
    {
      "citation_id": "DOC-001#section-2",
      "text": "经过长度限制的证据片段",
      "source_label": "退货政策第2节"
    }
  ],
  "notice": "可选的证据不足或冲突说明"
}
```

### 2.2 应用层 artifact

```json
{
  "query": "string",
  "retriever": "hybrid | raptor | graph | web",
  "index_version": "string",
  "hits": [
    {
      "source_id": "string",
      "parent_id": "string | null",
      "score": 0.0,
      "rerank_score": 0.0,
      "raw_text": "string",
      "metadata": {}
    }
  ],
  "timing_ms": {},
  "warnings": []
}
```

| ID | 要求 |
|---|---|
| RET-COM-001 | 工具必须返回有限长度的模型可见证据和完整应用层 artifact，不能把完整原文无界放入上下文。 |
| RET-COM-002 | 每个命中必须包含稳定 `source_id`、索引版本、分数和知识域元数据。 |
| RET-COM-003 | 无命中、低置信度或冲突必须设置 `answerable=false` 或明确 warnings，不得返回伪证据。 |
| RET-COM-004 | 在线工具不得修改索引、原始文档或图谱。 |

## 3. 常规混合 RAG

### 3.1 数据模型

每篇商品或 FAQ 文档必须形成两级结构：

- Parent：完整 FAQ 答案或商品说明章节，具有 `parent_id`；
- Child：用于召回的小块，具有 `child_id`、`parent_id`、文本、dense vector、BM25/sparse 字段和元数据。

Milvus collection 必须同时支持 BGE-M3 稠密表示与 BM25/稀疏表示。具体 embedding 维度由 DG-003 批准，具体检索参数由 DG-004 批准。

参考：[Milvus Multi-Vector Hybrid Search](https://milvus.io/docs/multi-vector-search.md)。

### 3.2 查询流程

1. 使用同一规范化查询执行稠密检索和 BM25 检索；
2. 使用获批融合策略合并候选，不在实现中隐式选择权重；
3. 使用获批 reranker 对候选重排；
4. 选择高排名 child 后按 `parent_id` 获取完整 parent；
5. 对重复 parent 去重，生成有限长度证据；
6. artifact 保留 child 命中、融合分数、rerank 分数和 parent 扩展关系。

| ID | 要求 |
|---|---|
| RET-HYB-001 | `search_product_faq` 只能检索商品与 FAQ collection。 |
| RET-HYB-002 | 稠密与 BM25 两路结果必须在 artifact 中可区分，融合过程必须可复现。 |
| RET-HYB-003 | 最终模型证据必须使用 parent 文本，引用必须能定位到命中的 child 与展开的 parent。 |
| RET-HYB-004 | 未获 DG-003、DG-004 批准前，只允许测试端口和参数实验，不得发布默认 Milvus schema 或检索配置。 |

## 4. RAPTOR

### 4.1 离线树结构

政策与手册文档必须形成：

- level 0：原始叶子块；
- level 1..N：聚类摘要节点；
- 每个节点包含 `node_id`、`document_id`、`level`、`child_ids`、摘要或原文、embedding、数据版本和生成元数据。

`N`、聚类算法参数和摘要模型由 DG-005 批准。离线构建必须使用固定输入版本，记录模型 ID、温度、prompt version 和随机种子（若算法支持）。

### 4.2 查询流程

1. 同时从叶子和摘要层召回候选；
2. 根据查询需要保留能够回答的层级，不能只返回无来源摘要；
3. 摘要节点必须能下钻到支持它的叶子节点；
4. 最终引用必须指向原始政策文档和章节，摘要节点仅作为检索路径；
5. 证据冲突时必须返回 warnings，并优先使用版本更新且仍有效的政策。

| ID | 要求 |
|---|---|
| RET-RAP-001 | `search_policy_raptor` 只能检索政策与手册树。 |
| RET-RAP-002 | 每个摘要节点必须保存完整子节点引用，不允许无法追溯的孤立摘要。 |
| RET-RAP-003 | 查询 artifact 必须显示命中层级、下钻路径和最终叶子证据。 |
| RET-RAP-004 | 未获 DG-005 批准前不得发布 RAPTOR 索引；获批前探针产物必须标记为实验结果。 |

## 5. GraphRAG

### 5.1 固定图 schema

首版使用确定性结构化 JSON 导入，不使用 LLM 从文本自动抽取实体或关系。

节点：

- `Product {product_id, name, description, active}`
- `Brand {brand_id, name}`
- `Category {category_id, name}`
- `Promotion {promotion_id, name, starts_at, ends_at, rules}`
- `Attribute {attribute_id, name, value}`

关系：

- `(:Product)-[:MADE_BY]->(:Brand)`
- `(:Product)-[:IN_CATEGORY]->(:Category)`
- `(:Product)-[:ELIGIBLE_FOR]->(:Promotion)`
- `(:Product)-[:HAS_ATTRIBUTE]->(:Attribute)`
- `(:Category)-[:PARENT_OF]->(:Category)`
- `(:Product)-[:RELATED_TO]->(:Product)`

### 5.2 查询约束

模型只传递自然语言问题和显式实体提示，不得传递 Cypher。工具内部必须从以下白名单查询族中选择：

- 商品 → 品牌/品类/属性；
- 品类 → 商品；
- 商品/品类 → 当前有效活动；
- 商品 → 关联商品；
- 两实体之间的限定深度路径。

最大路径深度、返回条数和 Neo4j 资源参数属于 DG-006；未获批准前集成工具保持禁用，但纯函数查询规划可以使用固定测试值验证。

| ID | 要求 |
|---|---|
| RET-GRA-001 | `search_commerce_graph` 必须使用白名单查询模板，不得执行模型生成的任意 Cypher。 |
| RET-GRA-002 | 图谱构建必须可由版本化 JSON 确定性重建，同一输入产生相同节点和关系主键。 |
| RET-GRA-003 | artifact 必须包含匹配实体、关系路径、图数据版本和查询模板 ID。 |
| RET-GRA-004 | 最终引用必须指向可展示的实体或关系证据，不得只给出数据库内部节点 ID。 |

实现可基于官方 [Neo4j GraphRAG for Python](https://neo4j.com/docs/neo4j-graphrag-python/current/)，但固定 schema 和白名单查询是本项目的稳定契约。

## 6. 联网搜索

| ID | 要求 |
|---|---|
| RET-WEB-001 | `web_search` 仅用于物流公共动态、行业动态和其他明确外部时效信息。 |
| RET-WEB-002 | 内部商品事实、政策、订单和退货规则不得用联网搜索回答。 |
| RET-WEB-003 | Tavily 结果必须包含标题、URL、抓取时间和有限长度摘要；最终回答必须引用 URL。 |
| RET-WEB-004 | 网页与内部事实冲突时，内部事实优先，并在回答中说明外部信息不适用于内部规则。 |
| RET-WEB-005 | Tavily 超时或不可用时只能有界重试；无可靠来源则说明无法确认或转人工。 |

## 7. 引用规则

| ID | 要求 |
|---|---|
| CIT-001 | 每个知识型最终答案必须至少引用一个实际工具返回的 citation。 |
| CIT-002 | 模型不得创建工具 artifact 中不存在的 citation ID、文档 ID 或 URL。 |
| CIT-003 | 引用必须支持相邻结论；只有相关但不支持结论的来源视为引用错误。 |
| CIT-004 | 订单事实不使用文档引用，而应在回答中明确来自订单查询结果和查询时间。 |

内部引用格式：`[来源：<source_label>（<citation_id>）]`。

外部引用格式：Markdown 链接，URL 必须来自 Tavily artifact。

## 8. 模拟语料

首版必须包含可版本控制的最小语料：

1. 商品与 FAQ：覆盖规格、兼容性、保修和常见使用问题；
2. 政策长文：覆盖取消、退货、退款、配送和例外条款，具有跨章节问题；
3. 图数据：覆盖多个品牌、层级品类、商品属性、活动资格和关联商品；
4. gold evidence：每个评测问题关联可接受的 source ID 或图路径；
5. 反例：语料中不存在答案、过期政策、相似商品和冲突陈述。

模拟语料不得包含真实个人信息、真实订单或受版权限制的未授权长文。

## 9. 离线全量索引与发布

### 9.1 构建阶段

```text
validate source -> normalize -> build candidate index -> run integrity checks
-> run retrieval smoke set -> write build report -> atomically publish version
```

| ID | 要求 |
|---|---|
| IDX-001 | 构建命令必须显式接收数据版本，禁止在线请求触发索引。 |
| IDX-002 | 每次构建必须写报告：输入哈希、文档数、块/节点数、模型配置引用、耗时、失败和 smoke 结果。 |
| IDX-003 | 构建失败不得覆盖当前已发布版本。 |
| IDX-004 | 发布必须原子切换逻辑别名或版本指针；在线查询必须在整个请求中使用同一版本。 |
| IDX-005 | 旧版本删除是独立维护操作，不属于构建成功路径。 |
| IDX-006 | 密钥、连接串和原始供应商响应不得写入构建报告。 |

## 10. Given/When/Then 验收场景

### RET-SCN-001：small-to-big

- Given：查询命中一个商品 FAQ child；
- When：执行混合检索；
- Then：模型看到对应 parent 的有限证据，artifact 保留 child、parent 和两路分数。

### RET-SCN-002：跨章节政策

- Given：问题需要结合退货政策两个章节；
- When：执行 RAPTOR 检索；
- Then：命中摘要路径并下钻到两个叶子证据，最终引用原始章节。

### RET-SCN-003：商品关系

- Given：用户询问某品牌下参与活动且具备指定属性的商品；
- When：执行图检索；
- Then：只运行白名单模板并返回可审计关系路径。

### RET-SCN-004：错误的联网兜底

- Given：内部退货政策未命中；
- When：Agent 考虑外部搜索；
- Then：不得使用网页补造内部政策，应报告证据不足或转人工。

### RET-SCN-005：索引构建失败

- Given：候选索引完整性检查失败；
- When：构建命令结束；
- Then：命令失败、报告记录原因，在线别名仍指向旧版本。

## 11. 测试要求

- 单元：规范化、父子映射、融合输入输出、RAPTOR 节点追溯、图模板选择、引用校验、发布状态机。
- Milvus 集成：稠密/BM25 两路召回、metadata 过滤，以及常规 RAG 与 RAPTOR collection 的版本隔离。
- Neo4j 集成：确定性导入、约束、白名单模板、路径证据和数据版本。
- Tavily opt-in：URL、抓取时间、超时和脱敏，不将网络波动计入普通测试。
- 评测：每种 RAG 5 条场景，gold evidence Hit@5 必须达到 80% 或以上。
