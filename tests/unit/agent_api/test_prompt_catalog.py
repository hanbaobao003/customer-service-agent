from pathlib import Path

import pytest

from customer_service_agent.agent_api import service
from customer_service_agent.shared.models import RuntimeContext


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def write_prompts(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "prompts.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.unit
def test_catalog_rejects_unknown_template_variable(tmp_path: Path) -> None:
    path = write_prompts(
        tmp_path,
        """version: 1
agent:
  customer_service:
    system: "客户：{customer_id}"
retrieval:
  raptor_summary:
    version: raptor-summary-v1
    system: "摘要"
    user: "{source_ids} {content}"
""",
    )

    with pytest.raises(ValueError, match="unknown placeholder"):
        service.PromptCatalog.from_path(path)


@pytest.mark.unit
def test_catalog_renders_only_approved_raptor_variables(tmp_path: Path) -> None:
    catalog = service.PromptCatalog.from_path(
        write_prompts(
            tmp_path,
            """version: 1
agent:
  customer_service:
    system: "{trusted_context} {tool_policy}"
retrieval:
  raptor_summary:
    version: raptor-summary-v1
    system: "提炼客服政策"
    user: "来源：{source_ids}；内容：{content}"
""",
        )
    )

    rendered = catalog.render_raptor_summary(
        source_ids=("policy#1",),
        content="七天无理由退货",
    )

    assert rendered.version == "raptor-summary-v1"
    assert rendered.system == "提炼客服政策"
    assert rendered.user == "来源：policy#1；内容：七天无理由退货"


@pytest.mark.unit
def test_catalog_rejects_missing_required_raptor_variable(tmp_path: Path) -> None:
    catalog = service.PromptCatalog.from_path(
        write_prompts(
            tmp_path,
            """version: 1
agent:
  customer_service:
    system: "{trusted_context} {tool_policy}"
retrieval:
  raptor_summary:
    version: raptor-summary-v1
    system: "提炼客服政策"
    user: "来源：{source_ids}；内容：{content}"
""",
        )
    )

    with pytest.raises(ValueError, match="missing placeholder"):
        catalog.render_raptor_summary(source_ids=("policy#1",))


@pytest.mark.unit
def test_project_prompt_catalog_renders_the_approved_raptor_template() -> None:
    catalog = service.PromptCatalog.from_path(PROJECT_ROOT / "config" / "prompts.yaml")

    rendered = catalog.render_raptor_summary(
        source_ids=("policy#return",),
        content="未发货订单可取消。",
    )

    assert rendered.version == "raptor-summary-v1"
    assert rendered.user is not None
    assert "policy#return" in rendered.user


@pytest.mark.unit
def test_agent_prompt_uses_anonymous_context_not_customer_identity() -> None:
    catalog = service.PromptCatalog.from_path(PROJECT_ROOT / "config" / "prompts.yaml")

    rendered = service.build_agent_prompt(
        catalog,
        context=RuntimeContext.trusted(
            customer_id="customer-secret",
            thread_id="thread-secret",
            request_id="request-secret",
            channel="cli",
        ),
        tool_policy="仅使用已注册工具。",
    )

    assert "customer-secret" not in rendered.system
    assert "thread-secret" not in rendered.system
    assert "request-secret" not in rendered.system
    assert "服务渠道：cli；语言：zh-CN。" in rendered.system
    assert "仅使用已注册工具。" in rendered.system


@pytest.mark.unit
def test_agent_prompt_rejects_blank_tool_policy() -> None:
    catalog = service.PromptCatalog.from_path(PROJECT_ROOT / "config" / "prompts.yaml")

    with pytest.raises(ValueError, match="tool policy"):
        service.build_agent_prompt(
            catalog,
            context=RuntimeContext.trusted(
                customer_id="customer-a",
                thread_id="thread-1",
                request_id="request-1",
            ),
            tool_policy=" ",
        )
