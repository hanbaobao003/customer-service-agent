"""MVP settings and owned-resource validation."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class MvpSettings:
    deepseek_api_key: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "MvpSettings":
        api_key = env.get("Deepseek_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Deepseek_API_KEY must be configured")
        return cls(deepseek_api_key=api_key)


def validate_owned_resource(name: str, prefix: str) -> None:
    if not name.startswith(prefix):
        raise ValueError("mvp resource is not owned")
