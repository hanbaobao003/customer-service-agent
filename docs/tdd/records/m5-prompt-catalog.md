# M5 集中提示词库 TDD 记录

- Spec：`docs/superpowers/specs/2026-09-02-central-prompt-library-design.md`
- Plan：`docs/superpowers/plans/2026-09-02-central-prompt-library.md`
- 生产文件：`config/prompts.yaml`、`src/customer_service_agent/agent_api/service.py`
- 测试文件：`tests/unit/agent_api/test_prompt_catalog.py`

## RED

- 首个 catalog 行为测试运行 `UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_prompt_catalog.py -q` 得到 `3 failed`。现有 `service` 模块可导入，失败原因为 `PromptCatalog` 公开入口不存在；不是收集、依赖或网络错误。
- 项目 YAML 加载测试在文件创建前运行，得到 `1 failed`，原因为 `config/prompts.yaml` 不存在，且 catalog 将其转换为稳定配置错误。
- 可信上下文测试运行得到 `2 failed`，原因为 `build_agent_prompt` 公开入口不存在；不是身份或 SSE 既有契约失败。

## GREEN 与 REFACTOR

- `PromptCatalog` 用安全 YAML 解析与固定结构加载唯一的 `config/prompts.yaml`；未知模板变量、非法 YAML、缺失字段和空文本通过 `PromptConfigError` 拒绝。
- 客服模板仅渲染 `trusted_context`、`tool_policy`；RAPTOR 模板仅渲染 `source_ids`、`content`，并返回 `raptor-summary-v1`。
- `build_agent_prompt` 仅注入 channel、locale 和固定工具策略，不注入 `customer_id`、`thread_id` 或 `request_id`。
- 无重构：实现保持在既有 `agent_api/service.py`，未新增全局 prompt package。

## 验证

- 项目 YAML GREEN：`14 passed`。
- 提示词、CLI 与 SSE 回归：`13 passed`。
- 本切片尚未证明真实 `create_agent` 装配、DeepSeek 调用或 RAPTOR 离线索引；它们属于后续 M4/M5 切片。
