import pytest
from pydantic import ValidationError

from customer_service_agent.memory.service import (
    ForgetMemoryRequest,
    ListMemoriesRequest,
    RememberMemoryRequest,
)


@pytest.mark.contract
@pytest.mark.parametrize(
    "request_type",
    [RememberMemoryRequest, ListMemoriesRequest, ForgetMemoryRequest],
)
def test_memory_model_schemas_never_expose_customer_id(request_type: type) -> None:
    schema = request_type.model_json_schema()

    assert "customer_id" not in schema["properties"]


@pytest.mark.contract
def test_memory_model_requests_reject_unknown_customer_id() -> None:
    with pytest.raises(ValidationError):
        RememberMemoryRequest(
            kind="preference",
            content="偏好中文",
            category="language",
            customer_id="model-supplied",
        )
