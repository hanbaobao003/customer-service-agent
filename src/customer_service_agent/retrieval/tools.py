"""Retrieval tool policies and result DTOs."""

import json
from datetime import datetime
from typing import Protocol

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from customer_service_agent.retrieval.models import RetrievalResult


class WebSearchRejected(ValueError):
    pass


class WebSearchPolicy:
    _ALLOWED_DOMAIN = "external_current_events"
    _ALLOWED_INTENTS = {
        "public_logistics_disruption",
        "industry_update",
        "external_current_event",
    }

    @classmethod
    def authorize(cls, *, domain: str, intent: str) -> None:
        if intent not in cls._ALLOWED_INTENTS:
            raise WebSearchRejected(f"web search intent is not allowed: {intent}")
        if domain != cls._ALLOWED_DOMAIN:
            raise WebSearchRejected(f"web search domain is not allowed: {domain}")


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    url: HttpUrl
    fetched_at: datetime
    summary: str = Field(min_length=1)
    truncated: bool

    @field_validator("fetched_at")
    @classmethod
    def fetched_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must include timezone")
        return value

    @classmethod
    def build(
        cls,
        *,
        title: str,
        url: str,
        fetched_at: datetime,
        summary: str,
        max_summary_chars: int,
    ) -> "WebSearchResult":
        if max_summary_chars < 1:
            raise ValueError("max_summary_chars must be positive")
        return cls(
            title=title,
            url=url,
            fetched_at=fetched_at,
            summary=summary[:max_summary_chars],
            truncated=len(summary) > max_summary_chars,
        )


class RetrievalToolServicePort(Protocol):
    async def search_product_faq(self, query: str) -> RetrievalResult: ...

    async def search_policy_raptor(self, query: str) -> RetrievalResult: ...

    async def search_commerce_graph(self, query: str) -> RetrievalResult: ...

    async def web_search(self, query: str) -> RetrievalResult: ...


def create_retrieval_tools(service: RetrievalToolServicePort):
    @tool("search_product_faq", response_format="content_and_artifact")
    async def search_product_faq(query: str) -> tuple[str, dict[str, object]]:
        """检索商品信息与常见问题。"""
        return _tool_output(await service.search_product_faq(query))

    @tool("search_policy_raptor", response_format="content_and_artifact")
    async def search_policy_raptor(query: str) -> tuple[str, dict[str, object]]:
        """检索可追溯的政策与手册证据。"""
        return _tool_output(await service.search_policy_raptor(query))

    @tool("search_commerce_graph", response_format="content_and_artifact")
    async def search_commerce_graph(query: str) -> tuple[str, dict[str, object]]:
        """检索商品、品牌、品类与活动的固定图谱关系。"""
        return _tool_output(await service.search_commerce_graph(query))

    @tool("web_search", response_format="content_and_artifact")
    async def web_search(query: str) -> tuple[str, dict[str, object]]:
        """查询可信的外部时效信息。"""
        return _tool_output(await service.web_search(query))

    return (
        search_product_faq,
        search_policy_raptor,
        search_commerce_graph,
        web_search,
    )


def _tool_output(result: RetrievalResult) -> tuple[str, dict[str, object]]:
    return (
        json.dumps(result.to_model_dict(), ensure_ascii=False),
        {
            "retrieval": result.artifact.model_dump(mode="json"),
            "citations": [item.model_dump(mode="json") for item in result.citations],
        },
    )
