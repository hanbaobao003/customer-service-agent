import socket

import pytest

from conftest import ExternalAccessBlocked, requires_opt_in


PLANNED_MARKERS = {
    "unit",
    "contract",
    "integration_postgres",
    "integration_milvus",
    "integration_neo4j",
    "live_model",
    "live_web",
    "live_embedding",
    "live_langsmith",
    "eval_gate",
}


@pytest.mark.unit
def test_unit_test_cannot_open_network_socket(external_access_guard) -> None:
    opened_socket = None
    try:
        with pytest.raises(ExternalAccessBlocked):
            opened_socket = socket.socket()
    finally:
        if opened_socket is not None:
            opened_socket.close()


@pytest.mark.unit
def test_all_verification_markers_are_registered(pytestconfig) -> None:
    registered = {
        definition.split(":", 1)[0].strip()
        for definition in pytestconfig.getini("markers")
    }

    assert PLANNED_MARKERS <= registered
    assert pytestconfig.getoption("strict_markers") is True


@pytest.mark.unit
def test_live_test_without_opt_in_is_skipped(monkeypatch) -> None:
    monkeypatch.delenv("RUN_LIVE_MODEL_TESTS", raising=False)

    with pytest.raises(pytest.skip.Exception):
        requires_opt_in("RUN_LIVE_MODEL_TESTS")
