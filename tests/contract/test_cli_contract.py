import pytest

from customer_service_agent import cli


@pytest.mark.contract
def test_cli_customer_identity_stays_in_trusted_context_not_message() -> None:
    context, message = cli.parse_cli_request(
        [
            "--customer-id",
            "customer-a",
            "--thread-id",
            "thread-1",
            "查询订单",
        ],
        request_id_factory=lambda: "request-1",
    )

    assert context.customer_id == "customer-a"
    assert context.thread_id == "thread-1"
    assert context.request_id == "request-1"
    assert context.channel == "cli"
    assert message == "查询订单"


@pytest.mark.contract
def test_cli_index_build_parses_only_an_approved_offline_kind() -> None:
    request = cli.parse_index_build_request(
        ["index-build", "--kind", "raptor", "--data-version", "policy-v1"]
    )

    assert request.kind == "raptor"
    assert request.data_version == "policy-v1"


@pytest.mark.contract
def test_cli_index_build_rejects_unknown_kind_before_client_creation() -> None:
    with pytest.raises(SystemExit) as error:
        cli.parse_index_build_request(
            ["index-build", "--kind", "unknown", "--data-version", "v1"]
        )

    assert error.value.code == 2
