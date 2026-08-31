"""Command-line adapter for local customer service runs."""

import argparse
from collections.abc import Callable, Sequence

from customer_service_agent.shared.models import RuntimeContext


def parse_cli_request(
    argv: Sequence[str],
    *,
    request_id_factory: Callable[[], str],
) -> tuple[RuntimeContext, str]:
    parser = argparse.ArgumentParser(prog="customer-service-agent")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("message")
    args = parser.parse_args(list(argv))

    return (
        RuntimeContext.trusted(
            customer_id=args.customer_id,
            thread_id=args.thread_id,
            request_id=request_id_factory(),
            channel="cli",
        ),
        args.message,
    )
