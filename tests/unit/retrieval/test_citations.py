import pytest

from customer_service_agent.retrieval.models import (
    CitationIntegrityError,
    Evidence,
    validate_citations,
)


@pytest.mark.unit
def test_rejects_evidence_with_unknown_citation() -> None:
    with pytest.raises(CitationIntegrityError) as captured:
        validate_citations(
            evidence=[
                Evidence(
                    citation_id="missing",
                    text="支持七天退货",
                    source_label="退货政策",
                )
            ],
            citations=[],
        )

    assert captured.value.args == (("missing",),)
