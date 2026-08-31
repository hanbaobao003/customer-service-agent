import pytest

from customer_service_agent.cli import parse_cli_request


@pytest.mark.contract
def test_cli_customer_identity_stays_in_trusted_context_not_message() -> None:
    context, message = parse_cli_request(
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
