# M2 长期记忆策略 RED–GREEN 记录

- Spec：`docs/specs/040-long-term-memory.md`
- 需求 ID：`MEM-POLICY-001…005`、`MEM-WRITE-001`、`MEM-WRITE-005`
- 公开行为：只有明确长期保存或删除指令可授权写操作；敏感内容被拒绝且决策不回显原文。
- 测试文件：`tests/unit/memory/test_policy.py`

## RED

- 命令：`UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/memory/test_policy.py -q`
- 退出码：`1`
- 失败摘要：9 个策略场景全部运行到未实现的策略函数并失败。
- 有效性说明：失败覆盖临时偏好、明确保存、明确删除和六类敏感信息，不依赖模型或外部服务。

## GREEN

- 最小实现：保存范围词与保存动词组合、明确删除动词、按类别命名的保守敏感规则；决策只返回敏感类别。
- 命令：与 RED 相同。
- 结果：`9 passed`。

## REFACTOR

- 改动：策略为确定性规则，不加入 LLM 分类器或自动抽取。
- 全量快速测试：`UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q`，`79 passed`。

## 边界

- 已验证：明确授权、临时覆盖拒绝、支付卡/验证码/API Key/密码/身份证/内部推理拒绝和不回显。
- 未验证：语言模型释义扩展和真实 Mem0 写入；首版授权故意保持保守。
- 提交：`feat: require explicit safe memory intent`（本记录随该提交保存）。
