# M3 Mem0 PGVector TDD 记录

- Spec：`docs/specs/040-long-term-memory.md` 1.2
- 计划：长期记忆 Task 5
- 生产文件：`src/customer_service_agent/memory/mem0_pg.py`、`src/customer_service_agent/memory/service.py`
- 测试文件：`tests/unit/memory/test_mem0_adapter_config.py`、`tests/unit/memory/test_conflicts.py`、`tests/integration/postgres/test_mem0_pgvector.py`

## RED

- 原地替换契约先修改测试；旧实现产生 `replace_delete`、`replace_create` 两条审计，专用测试以 `1 failed` 证明新契约尚未实现。
- 新适配器测试首次运行因目标模块尚不存在而 collection 失败。依据 TDD 规范，该导入错误只记录实施顺序，不计为有效行为 RED；后续所有配置、调用映射和客户隔离断言在实现前已经写入，但没有单独取得可运行的行为 RED，这是本 Task 的流程偏差。
- 首次 PostgreSQL 集成失败于缺少 Mem0 PGVector 必需的 `psycopg_pool`，补为现有 psycopg 的 `pool` extra。
- 第二次集成失败于隔离应用 role 无权创建 `vector` 扩展，修正为管理夹具在临时 database 中预装扩展，不提升应用 role 权限。
- 第三次集成失败于确定性测试查询低于 Mem0 默认相似度阈值；客户隔离测试改用同文查询，并关闭默认遥测，避免产生无关外网请求。

## GREEN

- `Mem0PgConfig` 固定 `customer_memories_v1`、1024 维、HNSW、关闭 DiskANN，只保存隔离 DSN 的环境变量引用。
- `Mem0PgMemoryStore` 对 add/search/get_all/get/update/delete 做稳定 DTO 映射；add 固定 `infer=False`；查询始终带 `user_id` filter；get 后再次校验所有权才允许更新或删除。
- 冲突替换使用 Mem0 update，保留原 `memory_id`，应用层只写一条 `replace_update` 审计。
- PostgreSQL 故障注入在 payload UPDATE 前抛错；验证同一 Mem0 PGVector 事务中先执行的 vector UPDATE 也回滚，旧正文和向量保持不变。

## 验证证据

- 记忆单元与契约：`41 passed`。
- 全部快速测试：`141 passed, 8 deselected`。
- Mem0 专用 PostgreSQL 集成：`1 passed`。
- 全部 PostgreSQL 集成：`8 passed`。
- 真实数据库验证范围：临时独立 database/role、pgvector 0.8.6、1024 维 HNSW、保存、检索、列出、原地替换、故障回滚、删除、重复删除和客户隔离。

## 未验证边界

- 没有调用 SiliconFlow；`BAAI/bge-m3` 和 `BAAI/bge-reranker-v2-m3` 只完成批准与静态配置约束，真实模型调用仍需 `live_embedding` opt-in。
- 没有验证生产备份恢复、并发容量、超过 100 条记忆的列表行为或真实网络故障。
- Mem0 history SQLite 不属于业务事实源；本集成只验证 PGVector 记录事务，不声称 history 与向量表构成跨存储原子事务。
