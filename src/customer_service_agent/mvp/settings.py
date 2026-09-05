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
    neo4j_username: str
    neo4j_password: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "MvpSettings":
        deepseek_api_key = _required(env, "Deepseek_API_KEY")
        neo4j_username, neo4j_password = _neo4j_auth(env)
        return cls(
            deepseek_api_key=deepseek_api_key,
            postgres_dsn=_required(env, "MVP_POSTGRES_DSN"),
            mem0_dsn=_required(env, "MVP_MEM0_DSN"),
            milvus_uri=_configured_or_default(
                env,
                "MILVUS_URI",
                "http://127.0.0.1:19530",
            ),
            neo4j_uri=_configured_or_default(
                env,
                "NEO4J_URI",
                "neo4j://127.0.0.1:7687",
            ),
            neo4j_username=neo4j_username,
            neo4j_password=neo4j_password,
        )


def validate_owned_resource(name: str, prefix: str) -> None:
    if not name.startswith(prefix):
        raise ValueError("mvp resource is not owned")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def _configured_or_default(
    env: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    return env.get(name, "").strip() or default


def _neo4j_auth(env: Mapping[str, str]) -> tuple[str, str]:
    value = _required(env, "NEO4J_AUTH")
    username, separator, password = value.partition("/")
    if not separator or not username or not password:
        raise ValueError("NEO4J_AUTH must use username/password format")
    return username, password
