import os

import pytest

from customer_service_agent.mvp import seed
from customer_service_agent.mvp.settings import MvpSettings, validate_owned_resource


def test_settings_rejects_missing_deepseek_api_key() -> None:
    with pytest.raises(ValueError, match="Deepseek_API_KEY"):
        MvpSettings.from_environment({})


def test_owned_resource_validation_rejects_unowned_name() -> None:
    with pytest.raises(ValueError, match="mvp resource"):
        validate_owned_resource("orders", "wang_agent_mvp_")


def test_settings_requires_neo4j_authentication() -> None:
    with pytest.raises(ValueError, match="NEO4J_AUTH"):
        MvpSettings.from_environment(
            {
                "Deepseek_API_KEY": "test-key",
                "MVP_POSTGRES_DSN": "postgresql://demo",
                "MVP_MEM0_DSN": "postgresql://memory",
                "MILVUS_URI": "http://127.0.0.1:19530",
                "NEO4J_URI": "neo4j://127.0.0.1:7687",
            }
        )


def test_settings_uses_local_docker_defaults_for_milvus_and_neo4j() -> None:
    settings = MvpSettings.from_environment(
        {
            "Deepseek_API_KEY": "test-key",
            "MVP_POSTGRES_DSN": "postgresql://demo",
            "MVP_MEM0_DSN": "postgresql://memory",
            "NEO4J_AUTH": "neo4j/test-password",
        }
    )

    assert settings.milvus_uri == "http://127.0.0.1:19530"
    assert settings.neo4j_uri == "neo4j://127.0.0.1:7687"


def test_mem0_runtime_disables_telemetry_before_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MEM0_TELEMETRY", "true")

    seed._configure_mem0_runtime(tmp_path / "history.db")

    assert os.environ["MEM0_TELEMETRY"] == "false"
    assert os.environ["MEM0_DIR"] == str(tmp_path / "mem0-runtime")


def test_settings_exposes_local_service_configuration() -> None:
    settings = MvpSettings.from_environment(
        {
            "Deepseek_API_KEY": "test-key",
            "MVP_POSTGRES_DSN": "postgresql://demo",
            "MVP_MEM0_DSN": "postgresql://memory",
            "MILVUS_URI": "http://127.0.0.1:19530",
            "NEO4J_URI": "neo4j://127.0.0.1:7687",
            "NEO4J_AUTH": "neo4j/test-password",
        }
    )

    assert settings.postgres_dsn == "postgresql://demo"
    assert settings.mem0_dsn == "postgresql://memory"
    assert settings.milvus_uri == "http://127.0.0.1:19530"
    assert settings.neo4j_uri == "neo4j://127.0.0.1:7687"
    assert settings.neo4j_username == "neo4j"
