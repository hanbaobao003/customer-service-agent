"""MVP settings and owned-resource validation."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class MvpSettings:
    deepseek_api_key: str
    postgres_dsn: str
    mem0_dsn: str
    milvus_uri: str
    neo4j_uri: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "MvpSettings":
        return cls(
            deepseek_api_key=_required(env, "Deepseek_API_KEY"),
            postgres_dsn=_required(env, "MVP_POSTGRES_DSN"),
            mem0_dsn=_required(env, "MVP_MEM0_DSN"),
            milvus_uri=_required(env, "MILVUS_URI"),
            neo4j_uri=_required(env, "NEO4J_URI"),
        )


def validate_owned_resource(name: str, prefix: str) -> None:
    if not name.startswith(prefix):
        raise ValueError("mvp resource is not owned")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    return value
