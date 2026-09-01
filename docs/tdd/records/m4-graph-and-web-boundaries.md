# M4 图与联网边界 TDD 记录

- Spec：`docs/specs/020-retrieval-and-indexing.md`
- 计划：检索与索引 Task 4
- 生产文件：`src/customer_service_agent/retrieval/graph.py`、`src/customer_service_agent/retrieval/tools.py`
- 测试文件：`tests/unit/retrieval/test_graph.py`、`tests/unit/retrieval/test_web_policy.py`、`tests/integration/neo4j/test_neo4j_graph.py`

## RED

- 两个单元测试文件先于模块编写；首次运行因 `graph.py`、`tools.py` 不存在而 collection error，不计有效 RED。
- 加入无行为接口骨架后得到 `18 failed`，覆盖未知模板拒绝、参数白名单、固定 schema、显式资源上限、内部知识联网拒绝和网页 DTO。
- Neo4j adapter 测试先得到 `AttributeError`，随后最小实现 Python Driver 的只读路由、database 绑定和 `Record` 转换。
- 初次真实 Neo4j 测试通过后加入跨版本中间节点反例，旧限定路径模板错误返回 2 条路径，得到 `1 failed`；补充全路径节点版本过滤后恢复 GREEN。
- 代码审阅继续加入跨版本品牌、品类和活动关系，旧商品上下文错误返回 5 条而非 3 条记录，再次得到 `1 failed`；所有关联实体模板补齐 `data_version` 过滤后恢复 GREEN。

## GREEN 与集成

- `GraphQueryRegistry` 只执行五个版本控制模板；未知 template ID、缺少/多余参数、类型错误和空字符串都会在 driver 调用前拒绝。
- `max_path_depth`、`result_limit` 必须显式传入且为正数，没有生产默认值。
- Python adapter 固定使用 Neo4j Driver 的 READ routing 和指定 database。
- 五个查询族均在 `neo4j:2026.07.1` 上执行：商品上下文、品类商品、有效活动、关联商品、限定深度路径。
- 集成测试使用两个 `wang_agent_graph_test_<12hex>` 数据版本制造同版本与跨版本路径；清理逐个使用精确 `data_version`，fixture 与独立只读检查均确认剩余节点为 0。
- `WebSearchPolicy` 只放行外部时效知识域中的公共物流、行业动态和其他外部时效意图；内部政策、商品事实和订单意图拒绝。
- `WebSearchResult` 固定标题、HTTP(S) URL、带时区抓取时间、有限摘要和截断标记。

## 依赖与未验证边界

- 已按 DG-001 锁定 `neo4j-graphrag==1.19.0`；本切片使用其兼容的 Neo4j Python Driver 执行确定性白名单 Cypher，不使用 LLM 图抽取。
- Neo4j L3：`1 passed`；图与联网单元：`19 passed`。
- 包含 PostgreSQL、Neo4j 集成层的全量回归：`190 passed`。
- Tavily 未发生真实调用；`live_web` 仍要求显式 opt-in，超时重试与供应商响应映射留在 Task 5 工具装配。
- 模型可见有限证据与完整 `ToolMessage.artifact` 的最终装配属于 Task 5；本切片返回 template ID、数据版本和原始 records 作为其稳定输入。
