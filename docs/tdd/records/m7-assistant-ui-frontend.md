# M7 assistant-ui 本地前端 TDD 记录

## 范围

只新增独立 `frontend/`，通过 Vite 代理连接既有 FastAPI SSE 与审批接口；没有修改 Agent、工具、订单状态机或数据访问协议。

## RED → GREEN

1. SSE 客户端：先用网络分片、尾帧和安全 HTTP 错误测试确认目标导出不存在；最小实现后 4 项测试通过。
2. assistant-ui adapter：先加入累积文本、事件转发、审批暂停和决策恢复测试，确认 `createMvpAdapter` 不存在；实现后累计 8 项测试通过。
3. 页面状态：先加入线程身份、工具生命周期、引用字段过滤和健康状态测试，确认状态函数不存在；实现后累计 14 项测试通过。
4. 浏览器回归：真实点击“拒绝”复现 `Duplicate key ... in useResources` 白屏。新增断言要求恢复响应不得重复输出已由 assistant-ui 管理的审批 tool-call；测试先失败，删除重复 part 后 14 项测试通过，浏览器恢复为“已拒绝，本次操作不会执行”。
5. 后端回归：真实审批后继续提问暴露 checkpoint 中未完成的工具消息，以及跨轮次预算计数累积。先增加“审批预览必须 drain 图并提交 ToolMessage”和“每轮从零开始预算计数”的失败测试；实现后相关单元/契约测试通过。决策结果也回写同一 PostgreSQL checkpoint，后续模型可看到“订单状态未改变”。

期间还修复了两个构建层问题：Vitest/Vite 配置类型来源不一致，以及 CSS 模块缺少 `vite/client` 类型。二者均只修改前端配置。

## 真实验收证据

- `npm test`：14 passed。
- `npm run typecheck`：通过。
- `npm run build`：705 modules transformed，产物成功生成。
- `tests/live/test_mvp_deepseek.py`：1 passed，真实 DeepSeek 调用约 6 秒。
- 幂等 seed：订单、FAQ、RAPTOR、GraphRAG 和 1 条长期记忆均已就绪。
- `/health/ready`：PostgreSQL 与 Milvus 均为 ready；Neo4j 容器运行中。
- 浏览器真实验证：FAQ 得到 12 个月保修回答及引用；订单查询返回 `MVP-ORDER-1001`；取消订单显示 HITL 审批卡片；拒绝后通过同一检查点恢复且订单不变。
- 浏览器真实验证（修复前端与后端 checkpoint 版本）：FAQ 得到 12 个月保修回答及引用；订单查询返回 `MVP-ORDER-1001`；取消订单显示 HITL 审批卡片；拒绝后页面正常恢复且订单不变。最新后端代码还会重置每轮预算并回写决策消息，需重启 8001 后端后生效。

## 边界

`X-Demo-Customer` 是本地演示身份，不是生产认证。前端只显示公开事件字段，不显示密钥、连接密码、原始 SQL、向量分数、图路径、artifact 或 chain-of-thought。
