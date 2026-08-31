"""Shared pytest boundaries for deterministic default test runs."""

from collections.abc import Iterator
import os
import socket
from typing import NoReturn

import pytest


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


class ExternalAccessBlocked(RuntimeError):
    """Raised when a default test attempts socket access."""


class ExternalAccessGuard:
    """Block socket creation for deterministic unit and contract tests."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.active = False

    def enable(self) -> None:
        self._monkeypatch.setattr(socket, "socket", self._blocked)
        self.active = True

    @staticmethod
    def _blocked(*args: object, **kwargs: object) -> NoReturn:
        raise ExternalAccessBlocked("socket access is disabled for this test layer")


@pytest.fixture(autouse=True)
def external_access_guard(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ExternalAccessGuard]:
    marker_names = {marker.name for marker in request.node.iter_markers()}
    for marker, env_name in LIVE_OPT_INS.items():
        if marker in marker_names:
            requires_opt_in(env_name)

    guard = ExternalAccessGuard(monkeypatch)
    if not marker_names.intersection(EXTERNAL_ACCESS_MARKERS):
        guard.enable()
    yield guard


def requires_opt_in(env_name: str) -> None:
    """Require an explicit environment switch for a live test."""

    if os.getenv(env_name) != "1":
        pytest.skip(f"set {env_name}=1 to run this live test")
