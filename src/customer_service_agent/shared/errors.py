"""Stable service errors shared by public adapters."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServiceError(Exception):
    """A stable public error with an optional log-only internal cause."""

    code: str
    message: str
    retryable: bool
    request_id: str
    cause: Exception | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
        }
