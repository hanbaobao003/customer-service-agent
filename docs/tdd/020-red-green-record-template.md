# RED–GREEN–REFACTOR 记录模板

**状态：** 待用户审阅

**版本：** 1.1

每个实施任务复制以下结构到该任务的实施记录或提交说明。尖括号表示记录字段格式，不是待实现需求；填写时必须替换为实际证据。

```markdown
## <task-id> <task-name>

- Spec：`<spec-path>`
- 需求 ID：`<REQ-001, REQ-002>`
- 公开行为：`<输入条件下可观察的结果>`
- 测试文件：`<tests/path/test_file.py>`
- 测试名称：`<test_exact_behavior>`

### RED

- 命令：`<exact pytest command>`
- 退出码：`1`
- 失败摘要：`<expected value and actual value>`
- 有效性说明：测试完成收集和执行，因目标行为缺失发生断言失败；不是导入、语法、依赖或连接错误。

### GREEN

- 最小实现：`<production files and exact behavior added>`
- 命令：`<same focused pytest command>`
- 结果：`<N passed in Xs>`

### REFACTOR

- 改动：`<rename, deduplication, or 无重构>`
- 相关回归：`<module pytest command and result>`
- 全量快速测试：`<unit plus contract command and result>`

### 边界

- 已验证：`<unit or contract behavior actually proven>`
- 未验证：`<Docker, live model, restart, performance, or other boundary>`
- 提交：`<commit hash and message>`
```

## 有效示例

```markdown
## API-CTX-01 拒绝线程客户不匹配

- Spec：`docs/specs/010-agent-api-and-middleware.md`
- 需求 ID：`CTX-003, API-003`
- 公开行为：已绑定客户 A 的 thread 由客户 B 请求时，返回 `THREAD_CUSTOMER_MISMATCH` 且不读取检查点。
- 测试文件：`tests/unit/agent_api/test_thread_binding.py`
- 测试名称：`test_bound_thread_rejects_different_customer_before_checkpoint_read`

### RED

- 命令：`uv run pytest tests/unit/agent_api/test_thread_binding.py::test_bound_thread_rejects_different_customer_before_checkpoint_read -q`
- 退出码：`1`
- 失败摘要：期望 `THREAD_CUSTOMER_MISMATCH`，实际继续调用应用服务。
- 有效性说明：测试正常执行到断言，失败由客户绑定校验缺失造成。

### GREEN

- 最小实现：在 `ThreadAccessService.authorize` 比较持久化绑定与可信上下文，并在不匹配时返回稳定领域错误。
- 命令：`uv run pytest tests/unit/agent_api/test_thread_binding.py::test_bound_thread_rejects_different_customer_before_checkpoint_read -q`
- 结果：`1 passed`。

### REFACTOR

- 改动：无重构。
- 相关回归：`uv run pytest tests/unit/agent_api/test_thread_binding.py -q`，全部通过。
- 全量快速测试：`uv run pytest -m 'unit or contract' -q`，全部通过。

### 边界

- 已验证：应用服务在读取检查点前拒绝客户不匹配。
- 未验证：PostgreSQL 线程绑定持久化和服务重启恢复，由 integration_postgres 覆盖。
- 提交：提交时填写实际 hash 和 message。
```

## 无效 RED

以下结果必须修复环境或测试后重新运行，不能进入记录：

- `ModuleNotFoundError`，且被测模块本应已经存在；
- pytest 未安装或配置解析失败；
- fixture 名称拼写错误；
- Docker 未启动导致本应为单元测试的测试失败；
- 测试在目标断言前抛出无关异常；
- 测试第一次运行即通过；
- 只断言 mock 被调用次数，没有断言业务结果或公开事件。
