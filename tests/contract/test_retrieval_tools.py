import json

import pytest

from customer_service_agent.retrieval.models import (
    Citation,
    Evidence,
    RetrievalArtifact,
    RetrievalHit,
    RetrievalResult,
)


@pytest.mark.contract
def test_tool_message_separates_bounded_model_content_from_full_artifact() -> None:
    result = RetrievalResult.build(
        answerable=True,
        evidence=[
            Evidence(
                citation_id="citation-1",
                text="支持七天退货",
                source_label="退货政策",
            )
        ],
        citations=[
            Citation(
                id="citation-1",
                source_id="policy-1",
                locator="section-2",
                title="退货政策",
            )
        ],
        artifact=RetrievalArtifact(
            retriever="hybrid",
            query="退货期限",
            index_version="faq-v1",
            hits=(
                RetrievalHit(
                    source_id="policy-1",
                    score=0.91,
                    rerank_score=0.88,
                    raw_text="这是完整原文，只允许进入 artifact。",
                    metadata={"collection": "internal-faq"},
                ),
            ),
            timing_ms={"dense": 12.5},
        ),
        max_evidence_chars=100,
    )

    message = result.to_tool_message(tool_call_id="call-1", name="search_product_faq")
    model_content = json.loads(message.content)

    assert set(model_content) == {"answerable", "evidence", "notice"}
    assert "完整原文" not in message.content
    assert "score" not in message.content
    assert "collection" not in message.content
    assert message.artifact["retrieval"]["hits"][0]["raw_text"].startswith("这是完整原文")
    assert message.artifact["citations"][0]["id"] == "citation-1"
