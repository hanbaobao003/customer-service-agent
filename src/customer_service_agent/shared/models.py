"""Shared application value objects."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Identity and request metadata supplied by a trusted adapter."""

    customer_id: str
    thread_id: str
    request_id: str
    locale: str = "zh-CN"
    channel: Literal["api", "cli"] = "api"

    @classmethod
    def trusted(
        cls,
        *,
        customer_id: str,
        thread_id: str,
        request_id: str,
        locale: str = "zh-CN",
        channel: Literal["api", "cli"] = "api",
    ) -> "RuntimeContext":
        identifiers = {
            "customer_id": customer_id,
            "thread_id": thread_id,
            "request_id": request_id,
        }
        for name, value in identifiers.items():
            if not value.strip():
                raise ValueError(f"{name} must not be blank")

        return cls(
            customer_id=customer_id.strip(),
            thread_id=thread_id.strip(),
            request_id=request_id.strip(),
            locale=locale,
            channel=channel,
        )
