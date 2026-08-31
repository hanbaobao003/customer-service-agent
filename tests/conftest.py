"""Shared pytest boundaries for deterministic default test runs."""

from collections.abc import Iterator
import os
import secrets
import subprocess
import uuid

import pytest
from pytest_socket import SocketBlockedError, disable_socket, enable_socket


LIVE_OPT_INS = {
    "live_model": "RUN_LIVE_MODEL_TESTS",
    "live_web": "RUN_LIVE_WEB_TESTS",
    "live_embedding": "RUN_LIVE_EMBEDDING_TESTS",
    "live_langsmith": "RUN_LIVE_LANGSMITH_TESTS",
}
EXTERNAL_ACCESS_MARKERS = {
    "integration_postgres",
    "integration_milvus",
    "integration_neo4j",
    *LIVE_OPT_INS,
}


ExternalAccessBlocked = SocketBlockedError
POSTGRES_CONTAINER = "shared-postgres"
POSTGRES_RESOURCE_PREFIX = "wang_agent_orders_test_"


class SecretDsn(str):
    def __repr__(self) -> str:
        return "<redacted-postgres-dsn>"


class ExternalAccessGuard:
    """Block socket creation for deterministic unit and contract tests."""

    def __init__(self) -> None:
        self.active = False

    def enable(self) -> None:
        disable_socket(allow_unix_socket=True)
        self.active = True


@pytest.fixture(autouse=True)
def external_access_guard(
    request: pytest.FixtureRequest,
) -> Iterator[ExternalAccessGuard]:
    marker_names = {marker.name for marker in request.node.iter_markers()}
    for marker, env_name in LIVE_OPT_INS.items():
        if marker in marker_names:
            requires_opt_in(env_name)

    guard = ExternalAccessGuard()
    if not marker_names.intersection(EXTERNAL_ACCESS_MARKERS):
        guard.enable()
    else:
        enable_socket()
    try:
        yield guard
    finally:
        enable_socket()


def requires_opt_in(env_name: str) -> None:
    """Require an explicit environment switch for a live test."""

    if os.getenv(env_name) != "1":
        pytest.skip(f"set {env_name}=1 to run this live test")


def _postgres_resource_name(kind: str, run_id: str) -> str:
    if kind not in {"db", "role"}:
        raise ValueError("resource kind must be db or role")
    if len(run_id) != 12 or any(
        character not in "0123456789abcdef" for character in run_id
    ):
        raise ValueError("test_run_id must be 12 lowercase hexadecimal characters")
    return f"{POSTGRES_RESOURCE_PREFIX}{kind}_{run_id}"


def _postgres_admin_sql(sql: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            POSTGRES_CONTAINER,
            "sh",
            "-c",
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("PostgreSQL test resource command failed")


@pytest.fixture
def postgres_dsn() -> Iterator[str]:
    status = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}",
            POSTGRES_CONTAINER,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if status.returncode != 0 or status.stdout.strip() != "true":
        pytest.fail(
            f"approved PostgreSQL container {POSTGRES_CONTAINER!r} is not running"
        )

    run_id = uuid.uuid4().hex[:12]
    database = _postgres_resource_name("db", run_id)
    role = _postgres_resource_name("role", run_id)
    password = secrets.token_hex(24)
    _postgres_admin_sql(
        f"CREATE ROLE {role} LOGIN PASSWORD '{password}';\n"
        f"CREATE DATABASE {database} OWNER {role};\n"
    )
    try:
        yield SecretDsn(
            f"postgresql://{role}:{password}@127.0.0.1:5432/{database}"
        )
    finally:
        assert database == _postgres_resource_name("db", run_id)
        assert role == _postgres_resource_name("role", run_id)
        _postgres_admin_sql(
            f"DROP DATABASE {database} WITH (FORCE);\n"
            f"DROP ROLE {role};\n"
        )
