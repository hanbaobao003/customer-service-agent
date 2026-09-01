# M4 RAPTOR 纯函数 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 计划：检索与索引 Task 3 Steps 1–3、Step 4 的单元部分，以及纯函数切片提交
- 生产文件：`src/customer_service_agent/retrieval/raptor.py`
- 测试文件：`tests/unit/retrieval/test_raptor.py`

## RED

- 测试先于模块编写；首次运行因 `raptor.py` 不存在而 collection error。按项目 TDD 规范，该结果不计有效 RED。
- 仅加入数据结构、端口和固定抛出 `NotImplementedError` 的函数骨架后，同一测试得到 `8 failed`：节点构造、树校验、下钻和 artifact 均未实现。这是有效行为 RED。
- GREEN 后新增“查询时必须重新校验摘要来源”的缺陷测试，旧实现得到 `1 failed, 8 passed`；随后补充在线下钻校验，避免绕过离线 `validate_tree` 读取不可追溯摘要。

## GREEN 与 REFACTOR

- 节点 ID 使用 `data_version + level + sorted(child_ids) + content_hash` 的规范化输入计算；child/source 顺序不影响结果，内容或数据版本变化会产生新 ID。
- 叶节点固定为 level 0；摘要节点必须有子节点，且每条边保持同文档、同数据版本和相邻层级。
- 摘要 `source_ids` 必须等于子树来源集合；离线整树校验和在线逐层下钻都会执行完整性检查。
- 多个 root 命中重叠时按输入优先级保留首条叶路径，artifact 分开记录 `raptor_root` 与 `raptor_leaf`，并保留命中层级、完整 node path、叶 source 和 locator。
- `SummarizerPort` 仅定义摘要依赖边界，没有选择或调用真实模型。
- 目标结果：RAPTOR 单元 `9 passed`；检索单元与契约 `26 passed`；包含已批准 PostgreSQL 集成层的全量测试 `170 passed`。

## 决策门与未验证边界

- DG-005 尚未批准；本切片没有选择 RAPTOR 层数、聚类算法/参数、摘要模型或生成参数，也没有发布真实树。
- 没有创建 RAPTOR Milvus database/collection、schema 或版本指针，没有运行 Milvus 集成测试。
- 本切片证明确定性树契约、完整性和证据追踪，不证明真实摘要质量、召回效果、Hit@5 或 P95。
- Task 3 完整完成门仍是 DG-005 获批后，真实离线树和隔离的 Milvus 集成测试通过。
