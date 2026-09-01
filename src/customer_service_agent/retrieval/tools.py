"""Retrieval tool policies and result DTOs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


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
