# M3 PostgreSQL 测试资源安全记录

**状态：** 已定义，尚未创建资源  
**适用里程碑：** M3 Orders Task 4 及后续 PostgreSQL L3 测试  
**依据：** DG-006

## 创建前硬门

集成夹具只允许管理同时满足以下条件的资源：

- 容器名必须精确等于 `shared-postgres`；
- database 名以 `wang_agent_orders_test_db_` 开头；
- role 名以 `wang_agent_orders_test_role_` 开头；
- 后缀 `test_run_id` 只能包含小写十六进制字符，长度为 12；
- database owner 必须是同一次运行创建的 role；
- 任何一项不满足时，在执行 DDL 前失败。

测试资源不得使用或修改 `postgres`、订单正式库、checkpoint 库、Mem0 库。Mem0 后续必须采用不同的固定前缀和独立 role/database。

## 生命周期

1. 测试进程生成随机 `test_run_id` 并先做纯函数校验；
2. 仅通过 `docker exec shared-postgres psql` 使用容器内管理入口创建临时 role/database；
3. 应用测试只使用该临时 role 连接临时 database；
4. 测试结束后先终止该 database 的测试连接，再删除 database 和 role；
5. 清理目标仍须再次通过相同的安全前缀和 run ID 校验。

临时 role 的口令只存在于测试进程，不写入仓库、日志或 `.env`。测试失败若导致清理未完成，后续只允许按上述精确前缀和 owner 证据人工处理，不使用通配删除。

## 本阶段验证目标

- operation、订单/订单项、退货请求和审计使用同一 PostgreSQL 事务；
- 在审计写入点注入异常时，领域写入和 operation 结果一并回滚；
- operation 行锁与唯一约束阻止重复副作用；
- repository 的读取始终带 `customer_id`。
