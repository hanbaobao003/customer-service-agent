# M4 检索参数与提示词决策

**状态：** 已批准

**日期：** 2026-09-02

## 决策

| 决策门 | 已批准值 | 影响范围 |
| --- | --- | --- |
| DG-004 | token chunk 500；overlap 100；候选 top-k 20；dense/BM25 融合权重 0.5/0.5；rerank 3 | 常规 RAG 离线构建、Milvus schema 后的检索默认参数和评测基线 |
| DG-005 | L0 叶节点加 L1–L3 三层摘要；余弦确定性层次聚类；seed 42；目标 8 个子节点，尾组为避免单子节点摘要可为 9；摘要模型 deepseek-v4-flash；temperature 0；最大 300 tokens；提示词版本 raptor-summary-v1 | RAPTOR 离线构建、索引构建报告和 live-model 验证 |

## 保留边界

- DG-004 与 DG-005 的批准允许实现和 L3 Milvus 集成测试；不等于真实 embedding、reranker 或 DeepSeek 调用已经验证。
- SiliconFlow embedding/rerank 必须以 live_embedding 明确 opt-in。
- DeepSeek RAPTOR 摘要必须以 live_model 明确 opt-in，并在执行前取得用户对费用与凭据使用的授权。
- 每次 Milvus L3 测试使用专属 database 和安全清理；不得触碰已有数据库或 collection。
- 提示词正文与受控变量契约以 docs/superpowers/specs/2026-09-02-central-prompt-library-design.md 为准。

