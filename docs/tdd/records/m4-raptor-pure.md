# M4 RAPTOR 建树与 Milvus TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 计划：`docs/superpowers/plans/2026-09-02-m4-milvus-raptor-indexing.md` Task 3
- 生产文件：`src/customer_service_agent/retrieval/raptor.py`
- 测试文件：`tests/unit/retrieval/test_raptor.py`、`tests/integration/milvus/test_milvus_raptor.py`

## RED

- 测试先于模块编写；首次运行因 `raptor.py` 不存在而 collection error。按项目 TDD 规范，该结果不计有效 RED。
- 仅加入数据结构、端口和固定抛出 `NotImplementedError` 的函数骨架后，同一测试得到 `8 failed`：节点构造、树校验、下钻和 artifact 均未实现。这是有效行为 RED。
- GREEN 后新增“查询时必须重新校验摘要来源”的缺陷测试，旧实现得到 `1 failed, 8 passed`；随后补充在线下钻校验，避免绕过离线 `validate_tree` 读取不可追溯摘要。
- 本次新增建树/存储测试首先得到 `6 failed, 9 passed`（缺少 `RaptorBuildConfig` 与 `build_raptor_tree`），以及一个 L3 `AttributeError`（缺少 RAPTOR collection adapter）；失败均来自缺失契约，不是模型或网络。

## GREEN 与 REFACTOR

- 节点 ID 使用 `data_version + level + sorted(child_ids) + content_hash` 的规范化输入计算；child/source 顺序不影响结果，内容或数据版本变化会产生新 ID。
- 叶节点固定为 level 0；摘要节点必须有子节点，且每条边保持同文档、同数据版本和相邻层级。
- 摘要 `source_ids` 必须等于子树来源集合；离线整树校验和在线逐层下钻都会执行完整性检查。
- 多个 root 命中重叠时按输入优先级保留首条叶路径，artifact 分开记录 `raptor_root` 与 `raptor_leaf`，并保留命中层级、完整 node path、叶 source 和 locator。
- `SummarizerPort` 仅定义摘要依赖边界，没有选择或调用真实模型。
- 已批准参数被收敛到 `RaptorBuildConfig`：仅允许 L1–L3、组大小 8、seed 42 和 `raptor-summary-v1`。叶输入同时带预注入的 1024 维向量；同 document 内稳定按余弦相似度分组，最后单节点尾组并入前一组，故可形成 9-child summary。
- 512 个叶的确定性测试产生 64 个 L1、8 个 L2 和 1 个 L3；摘要 port 每次接收 `source_ids`、层级和 YAML 已批准 `prompt_version`。
- `MilvusRaptorStore` 在独立 collection 保存 node、树边、source、locator 与向量；insert 后 flush，根检索强制当前 `data_version` 和 `is_root == true`，同一 store 可作为 `descend_hits()` 的 `NodeStorePort` 下钻 L0 证据。

## 决策门与未验证边界

- 本记录的原始纯函数切片完成时 DG-005 尚未批准；截至 2026-09-02，DG-005 已批准，具体值见 `docs/decisions/002-m4-retrieval-parameters.md`。本切片本身没有选择或调用真实摘要模型，也没有发布真实树。
- Docker L3 已验证：`RUN_MILVUS_INTEGRATION=1 UV_CACHE_DIR=.uv-cache uv run pytest -m integration_milvus tests/integration/milvus/test_milvus_raptor.py -q`，结果 `1 passed`；当前隔离 Milvus 集合回归为 `6 passed`。
- 本切片证明确定性树契约、根到叶证据追踪和在固定向量下的 Milvus 根过滤，不证明真实摘要质量、BGE 召回、Hit@5 或 P95。
- 真实 DeepSeek 摘要实现与 live 验证仍属于 Task 4，必须用 `live_model` opt-in；不会在默认测试中读取凭据或发起模型调用。
