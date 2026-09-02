import json

import pytest

import customer_service_agent.retrieval.tools as retrieval_tools
from customer_service_agent.retrieval.models import (
    Citation,
    Evidence,
    RetrievalArtifact,
    RetrievalHit,
    RetrievalResult,
)


class RetrievalService:
    async def search_product_faq(self, query: str) -> RetrievalResult:
        assert query == "能否退货"
        return retrieval_result("hybrid")

    async def search_policy_raptor(self, query: str) -> RetrievalResult:
        assert query == "能否退货"
        return retrieval_result("raptor")

    async def search_commerce_graph(self, query: str) -> RetrievalResult:
        assert query == "能否退货"
        return retrieval_result("graph")

    async def web_search(self, query: str) -> RetrievalResult:
        assert query == "能否退货"
        return retrieval_result("web")


def retrieval_result(retriever: str) -> RetrievalResult:
    return RetrievalResult.build(
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
            retriever=retriever,
            query="能否退货",
            index_version="fixture-v1",
            hits=(
                RetrievalHit(
                    source_id="policy-1",
                    score=0.91,
                    raw_text="完整原文仅进入 artifact。",
                    metadata={"channel": retriever},
                ),
            ),
        ),
        max_evidence_chars=100,
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


@pytest.mark.contract
@pytest.mark.asyncio
async def test_retrieval_tools_have_fixed_query_schema_and_artifact_boundary() -> None:
    factory = retrieval_tools.create_retrieval_tools
    by_name = {tool.name: tool for tool in factory(RetrievalService())}

    assert set(by_name) == {
        "search_product_faq",
        "search_policy_raptor",
        "search_commerce_graph",
        "web_search",
    }
    for tool in by_name.values():
        schema = tool.tool_call_schema.model_json_schema()
        assert set(schema["properties"]) == {"query"}
        content, artifact = await tool.coroutine(query="能否退货")
        assert json.loads(content)["evidence"][0]["text"] == "支持七天退货"
        assert "完整原文" not in content
        assert artifact["retrieval"]["hits"][0]["raw_text"] == "完整原文仅进入 artifact。"
