"""Shared retrieval evidence and artifact contracts."""

import json
from collections.abc import Sequence
from typing import Literal

from langchain.messages import ToolMessage
from pydantic import BaseModel, ConfigDict, Field


class CitationIntegrityError(ValueError):
    pass


class _StrictRetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Citation(_StrictRetrievalModel):

    id: str
    source_id: str
    locator: str
    title: str
    url: str | None = None


class Evidence(_StrictRetrievalModel):

    citation_id: str
    text: str
    source_label: str


class RetrievalHit(_StrictRetrievalModel):

    source_id: str
    parent_id: str | None = None
    score: float
    rerank_score: float | None = None
    raw_text: str
    metadata: dict[str, object]


class RetrievalArtifact(_StrictRetrievalModel):

    retriever: Literal["hybrid", "raptor", "graph", "web"]
    query: str
    index_version: str
    hits: tuple[RetrievalHit, ...]
    timing_ms: dict[str, float] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class RetrievalResult(_StrictRetrievalModel):

    answerable: bool
    evidence: tuple[Evidence, ...]
    citations: tuple[Citation, ...]
    artifact: RetrievalArtifact
    notice: str | None = None

    @classmethod
    def build(
        cls,
        *,
        answerable: bool,
        evidence: list[Evidence],
        citations: list[Citation],
        artifact: RetrievalArtifact,
        max_evidence_chars: int,
        notice: str | None = None,
    ) -> "RetrievalResult":
        if max_evidence_chars < 1:
            raise ValueError("max_evidence_chars must be positive")
        validate_citations(evidence=evidence, citations=citations)
        bounded_evidence: list[Evidence] = []
        truncation_warnings: list[str] = []
        for item in evidence:
            if len(item.text) > max_evidence_chars:
                bounded_evidence.append(
                    item.model_copy(update={"text": item.text[:max_evidence_chars]})
                )
                truncation_warnings.append(f"evidence_truncated:{item.citation_id}")
            else:
                bounded_evidence.append(item)

        return cls(
            answerable=answerable and bool(bounded_evidence),
            evidence=tuple(bounded_evidence),
            citations=tuple(citations),
            artifact=artifact.model_copy(
                update={"warnings": (*artifact.warnings, *truncation_warnings)}
            ),
            notice=notice,
        )

    def to_model_dict(self) -> dict[str, object]:
        return {
            "answerable": self.answerable,
            "evidence": [item.model_dump(mode="json") for item in self.evidence],
            "notice": self.notice,
        }

    def to_tool_message(self, *, tool_call_id: str, name: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(self.to_model_dict(), ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=name,
            artifact={
                "retrieval": self.artifact.model_dump(mode="json"),
                "citations": [
                    citation.model_dump(mode="json") for citation in self.citations
                ],
            },
        )


def validate_citations(
    *,
    evidence: Sequence[Evidence],
    citations: Sequence[Citation],
) -> None:
    known = {citation.id for citation in citations}
    missing = sorted({item.citation_id for item in evidence} - known)
    if missing:
        raise CitationIntegrityError(tuple(missing))
