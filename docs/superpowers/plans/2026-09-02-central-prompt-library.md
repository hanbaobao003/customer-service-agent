# 集中提示词库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 以唯一、版本化的 YAML 文件维护客服 Agent 与 RAPTOR 的稳定提示词，并在代码中严格控制变量和可信上下文。

**Architecture:** config/prompts.yaml 是唯一的提示词文本来源。agent_api/service.py 提供只读 PromptCatalog、安全加载和确定性渲染；RAPTOR 通过 render_raptor_summary() 使用同一 catalog，不复制提示词文本。动态身份、权限和业务输入始终由 Python 传入。

**Tech Stack:** Python 3.13、PyYAML 6.0.2、Pydantic v2、pytest。

**Spec:** docs/superpowers/specs/2026-09-02-central-prompt-library-design.md；docs/specs/010-agent-api-and-middleware.md；docs/specs/020-retrieval-and-indexing.md

## Global Constraints

- 只使用 config/prompts.yaml；不得从数据库、网络读取提示词。
- 模板仅使用 Python str.format_map；不启用 Jinja、表达式、文件包含或运行时模板选择。
- customer_id、API Key、密码、连接串、原始检索结果和推理过程不得进入 YAML、渲染结果或测试快照。
- YAML 顶层版本固定为整数 1；RAPTOR prompt version 固定为 raptor-summary-v1。
- 单元测试不读取 .env、不连接 Docker、不调用任何模型。

---

### Task 1: 固定 YAML 契约与有效 RED

**Files:**
- Create: config/prompts.yaml
- Create: tests/unit/agent_api/test_prompt_catalog.py
- Modify: src/customer_service_agent/agent_api/service.py
- Modify: pyproject.toml
- Modify: uv.lock

**Interfaces:**
- Produces: PromptConfigError(ValueError)。
- Produces: RenderedPrompt(system: str, user: str | None, version: str | None)。
- Produces: PromptCatalog.from_path(path: Path) -> PromptCatalog。
- Produces: PromptCatalog.render_agent_system(*, trusted_context: str, tool_policy: str) -> RenderedPrompt。
- Produces: PromptCatalog.render_raptor_summary(*, source_ids: tuple[str, ...], content: str) -> RenderedPrompt。

- [x] **Step 1: 写失败测试，定义合法模板和非法结构**

    def test_catalog_rejects_unknown_template_variable(tmp_path: Path) -> None:
        path = tmp_path / "prompts.yaml"
        path.write_text("version: 1\nagent:\n  customer_service:\n    system: '{customer_id}'\n")

        with pytest.raises(PromptConfigError, match="unknown placeholder"):
            PromptCatalog.from_path(path)

    def test_catalog_renders_only_approved_raptor_variables(tmp_path: Path) -> None:
        catalog = PromptCatalog.from_path(write_valid_prompts(tmp_path))

        rendered = catalog.render_raptor_summary(
            source_ids=("policy#1",), content="七天无理由退货"
        )
        assert rendered.version == "raptor-summary-v1"
        assert "policy#1" in rendered.user

同时为缺失文件、非法 YAML、顶层版本不是 1、缺少 retrieval.raptor_summary、空文本、未提供渲染变量和多传入变量各写一个独立断言。

- [x] **Step 2: 运行测试，确认是目标行为 RED**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_prompt_catalog.py -q

Expected: FAIL，原因是 PromptCatalog 与 PromptConfigError 尚未定义；不是 YAML 安装、导入或网络错误。

- [x] **Step 3: 安装解析器并实现最小 YAML 文件与 catalog**

Run: UV_CACHE_DIR=.uv-cache uv add pyyaml==6.0.2

    @dataclass(frozen=True)
    class RenderedPrompt:
        system: str
        user: str | None = None
        version: str | None = None

    class PromptCatalog:
        @classmethod
        def from_path(cls, path: Path) -> "PromptCatalog":
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls(_validate_prompt_payload(payload))

在 _validate_prompt_payload() 中使用 string.Formatter().parse() 取得字段名并逐条和固定集合比较：客服只可有 trusted_context、tool_policy；RAPTOR 只可有 source_ids、content。render_* 先比较实参键集合，再调用 format_map；任何缺失或额外键均抛出 PromptConfigError。

config/prompts.yaml 写入真实首版中文客服系统提示词与 RAPTOR 摘要提示词，但不写用户、订单或凭据样例。

- [x] **Step 4: 运行 GREEN 与受影响回归**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_prompt_catalog.py tests/unit/agent_api/test_context.py tests/unit/agent_api/test_customer_service.py -q

Expected: PASS；渲染后没有未替换的大括号变量。

- [x] **Step 5: 记录 TDD 证据并提交**

Create: docs/tdd/records/m5-prompt-catalog.md

记录有效 RED、GREEN 及“不证明真实 Agent/DeepSeek 调用”的边界。

    git add pyproject.toml uv.lock config/prompts.yaml src/customer_service_agent/agent_api/service.py \
      tests/unit/agent_api/test_prompt_catalog.py docs/tdd/records/m5-prompt-catalog.md
    git commit -m "feat: centralize stable prompt templates"

### Task 2: 可信 Agent 上下文渲染边界

**Files:**
- Modify: src/customer_service_agent/agent_api/service.py
- Modify: tests/unit/agent_api/test_prompt_catalog.py

**Interfaces:**
- Produces: build_agent_prompt(catalog: PromptCatalog, *, context: RuntimeContext, tool_policy: str) -> RenderedPrompt。
- Consumes: RuntimeContext 仅由可信 API/CLI adapter 构造。

- [x] **Step 1: 写失败测试，证明身份不会进入模型提示词**

    def test_agent_prompt_uses_anonymous_context_not_customer_identity(tmp_path: Path) -> None:
        prompt = build_agent_prompt(
            PromptCatalog.from_path(write_valid_prompts(tmp_path)),
            context=RuntimeContext.trusted(
                customer_id="customer-secret", thread_id="thread-1", request_id="req-1"
            ),
            tool_policy="仅使用已注册工具。",
        )
        assert "customer-secret" not in prompt.system
        assert "thread-1" not in prompt.system
        assert "仅使用已注册工具。" in prompt.system

- [x] **Step 2: 运行 RED**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_prompt_catalog.py::test_agent_prompt_uses_anonymous_context_not_customer_identity -q

Expected: FAIL，因为 build_agent_prompt 尚未定义。

- [x] **Step 3: 实现最小可信上下文构造器**

    def build_agent_prompt(
        catalog: PromptCatalog, *, context: RuntimeContext, tool_policy: str
    ) -> RenderedPrompt:
        trusted_context = f"服务渠道：{context.channel}；语言：{context.locale}。"
        return catalog.render_agent_system(
            trusted_context=trusted_context, tool_policy=tool_policy
        )

不得把 customer_id、thread_id 或 request_id 拼入 trusted_context；空白 tool_policy 抛 PromptConfigError。

- [x] **Step 4: 运行 GREEN 与契约回归**

Run: UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_prompt_catalog.py tests/contract/test_cli_contract.py tests/contract/test_sse_contract.py -q

Expected: PASS；客服提示词不泄露身份且 SSE 继续拒绝 prompt 敏感字段。

- [x] **Step 5: 更新 TDD 记录并提交**

    git add src/customer_service_agent/agent_api/service.py \
      tests/unit/agent_api/test_prompt_catalog.py docs/tdd/records/m5-prompt-catalog.md
    git commit -m "feat: render trusted agent prompt context"

## Requirement Coverage

| Requirement | Task |
| --- | --- |
| 单一 YAML 维护稳定提示词 | Task 1 |
| 受控变量与安全加载 | Task 1 |
| 客户身份不进入模型可见提示词 | Task 2 |
| RAPTOR 提示词版本可追踪 | Task 1；由 M4 计划消费 |

## Plan Verification

    UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/agent_api/test_prompt_catalog.py -q
    UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q
    git diff --check

真实 Agent 装配、DeepSeek 调用和任何 live_model 验证不属于本计划。
