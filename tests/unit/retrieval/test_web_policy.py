from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from customer_service_agent.retrieval.tools import (
    WebSearchPolicy,
    WebSearchRejected,
    WebSearchResult,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "intent",
    [
        "internal_return_policy",
        "internal_product_fact",
        "order_status",
    ],
)
def test_internal_knowledge_intents_cannot_use_web_search(intent: str) -> None:
    with pytest.raises(WebSearchRejected, match="intent"):
        WebSearchPolicy.authorize(domain="external_current_events", intent=intent)


@pytest.mark.unit
def test_public_logistics_disruption_can_use_web_search() -> None:
    WebSearchPolicy.authorize(
        domain="external_current_events",
        intent="public_logistics_disruption",
    )


@pytest.mark.unit
def test_non_external_domain_is_rejected_even_for_public_intent() -> None:
    with pytest.raises(WebSearchRejected, match="domain"):
        WebSearchPolicy.authorize(
            domain="internal_policy",
            intent="public_logistics_disruption",
        )


@pytest.mark.unit
def test_web_result_has_bounded_summary_and_traceable_source() -> None:
    result = WebSearchResult.build(
        title="物流公共动态",
        url="https://example.com/logistics",
        fetched_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        summary="港口因天气短时延误，预计晚间恢复。",
        max_summary_chars=8,
    )

    assert result.title == "物流公共动态"
    assert str(result.url) == "https://example.com/logistics"
    assert result.fetched_at.tzinfo is UTC
    assert result.summary == "港口因天气短时延"
    assert result.truncated is True


@pytest.mark.unit
def test_web_result_rejects_untraceable_or_unbounded_input() -> None:
    with pytest.raises(ValidationError):
        WebSearchResult.build(
            title="物流动态",
            url="not-a-url",
            fetched_at=datetime(2026, 9, 1, 8, 0),
            summary="正文",
            max_summary_chars=10,
        )
    with pytest.raises(ValueError, match="positive"):
        WebSearchResult.build(
            title="物流动态",
            url="https://example.com/logistics",
            fetched_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            summary="正文",
            max_summary_chars=0,
        )
