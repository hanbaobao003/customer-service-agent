import pytest

from customer_service_agent.shared.errors import ServiceError
from customer_service_agent.shared.models import RuntimeContext


@pytest.mark.unit
def test_runtime_context_rejects_blank_customer_id() -> None:
    with pytest.raises(ValueError, match="customer_id"):
        RuntimeContext.trusted(
            customer_id=" ",
            thread_id="thread-1",
            request_id="req-1",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "values"),
    [
        (
            "thread_id",
            {"customer_id": "customer-1", "thread_id": "\t", "request_id": "req-1"},
        ),
        (
            "request_id",
            {"customer_id": "customer-1", "thread_id": "thread-1", "request_id": ""},
        ),
    ],
)
def test_runtime_context_rejects_other_blank_trusted_ids(
    field: str,
    values: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match=field):
        RuntimeContext.trusted(**values)


@pytest.mark.unit
def test_runtime_context_normalizes_ids_and_is_immutable() -> None:
    context = RuntimeContext.trusted(
        customer_id=" customer-1 ",
        thread_id=" thread-1 ",
        request_id=" req-1 ",
        channel="cli",
    )

    assert context.customer_id == "customer-1"
    assert context.thread_id == "thread-1"
    assert context.request_id == "req-1"
    assert context.locale == "zh-CN"
    assert context.channel == "cli"
    with pytest.raises(AttributeError):
        context.customer_id = "customer-2"


@pytest.mark.unit
def test_service_error_public_dict_omits_internal_cause() -> None:
    error = ServiceError(
        code="THREAD_BUSY",
        message="当前会话正在处理其他请求",
        retryable=True,
        request_id="req-1",
        cause=RuntimeError("internal database detail"),
    )

    assert error.to_public_dict() == {
        "code": "THREAD_BUSY",
        "message": "当前会话正在处理其他请求",
        "retryable": True,
        "request_id": "req-1",
    }
