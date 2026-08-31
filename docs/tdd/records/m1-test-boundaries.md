# M1 测试边界 RED–GREEN 记录

- Spec：`docs/specs/050-evaluation-and-observability.md`
- 需求 ID：`TEST-001, TEST-010, TEST-011, TDD-001…004`
- 公开行为：默认测试不能创建 socket；未知 marker 被拒绝；live 测试缺少对应环境开关时跳过。
- 测试文件：`tests/unit/quality/test_test_boundaries.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_test_boundaries.py -q`
- 退出码：`1`
- 失败摘要：2 个测试失败、1 个通过；socket 创建未抛出 `ExternalAccessBlocked`，缺少 live 开关未触发 skip。
- 有效性说明：有效 RED 未连接网络，只创建未连接的 socket 对象；pytest 正常执行到目标断言。更早依赖本地端口绑定的探针受命令沙箱影响，已废弃且不计为 RED。

## GREEN

- 最小实现：默认 autouse fixture 禁止 socket；仅 integration/live marker 解除；四类 live marker 要求各自值为 `1` 的显式开关。
- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_test_boundaries.py -q`
- 结果：`3 passed`。

## REFACTOR

- 改动：无重构。
- 相关回归：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/quality/test_test_boundaries.py -q`，3 个测试通过。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，8 个测试通过。

## 边界

- 已验证：默认 socket 禁止、10 个稳定 marker 注册、strict marker 配置和 live opt-in helper。
- 附加探针：一次性未知 marker 收集命令退出码为 `2`，错误为 marker 未注册；探针随后删除，未进入项目测试集。
- 未验证：Docker 服务、真实模型、Tavily、embedding/rerank 和 LangSmith；解除 socket 限制不提供任何凭证。
- 提交：`test: enforce isolated verification layers`（本记录随该提交保存）。
