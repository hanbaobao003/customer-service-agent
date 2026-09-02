"""Command-line adapter for local customer service runs."""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from customer_service_agent.shared.models import RuntimeContext


@dataclass(frozen=True)
class IndexBuildRequest:
    kind: Literal["hybrid", "raptor"]
    data_version: str


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


def parse_index_build_request(argv: Sequence[str]) -> IndexBuildRequest:
    parser = argparse.ArgumentParser(prog="customer-service-agent")
    parser.add_argument("command", choices=("index-build",))
    parser.add_argument("--kind", choices=("hybrid", "raptor"), required=True)
    parser.add_argument("--data-version", required=True)
    args = parser.parse_args(list(argv))
    if not args.data_version.strip():
        parser.error("--data-version must not be blank")
    return IndexBuildRequest(kind=args.kind, data_version=args.data_version)
