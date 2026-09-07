import pytest
from pydantic import ValidationError

from customer_service_agent.retrieval.models import (
    Citation,
    Evidence,
    RetrievalArtifact,
    RetrievalHit,
    RetrievalResult,
)


def citation() -> Citation:
    return Citation(
        id="citation-1",
        source_id="policy-1",
        locator="section-2",
        title="退货政策",
    )


def artifact() -> RetrievalArtifact:
    return RetrievalArtifact(
        retriever="hybrid",
        query="退货期限",
        index_version="faq-v1",
        hits=(
            RetrievalHit(
                source_id="policy-1",
                score=0.9,
                raw_text="这是只应进入 artifact 的完整原文",
                metadata={"collection": "internal-faq"},
            ),
        ),
    )


@pytest.mark.unit
def test_long_evidence_is_truncated_with_explicit_warning() -> None:
    result = RetrievalResult.build(
        answerable=True,
        evidence=[
            Evidence(
                citation_id="citation-1",
                text="支持七天无理由退货",
                source_label="退货政策",
            )
        ],
        citations=[citation()],
        artifact=artifact(),
        max_evidence_chars=6,
    )

    assert result.evidence[0].text == "支持七天无理"
    assert result.artifact.warnings == ("evidence_truncated:citation-1",)


@pytest.mark.unit
def test_empty_evidence_cannot_be_marked_answerable() -> None:
    result = RetrievalResult.build(
        answerable=True,
        evidence=[],
        citations=[],
        artifact=artifact(),
        max_evidence_chars=100,
    )

    assert result.answerable is False


@pytest.mark.unit
def test_evidence_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_evidence_chars"):
        RetrievalResult.build(
            answerable=True,
            evidence=[
                Evidence(
                    citation_id="citation-1",
                    text="证据",
                    source_label="政策",
                )
            ],
            citations=[citation()],
            artifact=artifact(),
            max_evidence_chars=0,
        )


@pytest.mark.unit
def test_retrieval_dtos_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            citation_id="citation-1",
            text="证据",
            source_label="政策",
            connection_string="postgresql://must-not-be-accepted",
        )
