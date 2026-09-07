# M4 混合检索纯函数 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 计划：检索与索引 Task 2 Steps 1–3，以及 Step 4 的单元部分
- 生产文件：`src/customer_service_agent/retrieval/hybrid.py`
- 测试文件：`tests/unit/retrieval/test_hybrid.py`

## RED

- 测试先于模块编写；首次运行因 `hybrid.py` 不存在而 collection error。按项目 TDD 规范，该结果不计有效 RED。
- 仅加入字段骨架和固定抛出 `NotImplementedError` 的无行为函数后，同一测试得到 `11 failed`：融合、参数校验、父块展开、完整性错误和 artifact 均未实现。这是有效行为 RED。
- 随后新增“融合方法必须显式声明”的测试，旧 `FusionConfig` 拒绝 `method` 参数，得到 `12 failed`；实现只接受实验方法 `weighted_reciprocal_rank`，避免把策略隐藏在代码默认值中。

## GREEN 与 REFACTOR

- GREEN：显式权重的加权倒数排名融合；`candidate_k`/`final_k` 边界；跨路 child 身份一致性；parent 去重与 locator 保留；dense、sparse、fused 三类 artifact 命中。
- REFACTOR：内部候选状态改为私有 `_Candidate` 数据类，去掉 `dict[str, object]` 动态键；公开接口和测试结果不变。
- 目标结果：`12 passed`。
- 检索单元与契约：`18 passed`。
- 全量快速测试：`153 passed, 8 deselected`。

## 决策门与未验证边界

- 本记录的原始纯函数切片完成时 DG-004 尚未批准；截至 2026-09-02，DG-004 已批准，具体值见 `docs/decisions/002-m4-retrieval-parameters.md`。
- 本实现没有默认权重、top-k、collection、chunk 或 rerank 数量；测试中的值均为显式算法样例，不是部署默认值。
- `weighted_reciprocal_rank` 当前是显式实验方法；获批默认权重和发布配置尚待后续 Milvus TDD 切片实现。
- 尚未安装或调用 PyMilvus，没有创建 Milvus schema/collection，没有运行 BGE-M3、BM25、reranker 或 small-to-big 集成测试。
- 真实 adapter 暂未创建；后续实现必须通过隔离 Milvus L3 测试，不能将纯函数 GREEN 视为真实后端验证。
