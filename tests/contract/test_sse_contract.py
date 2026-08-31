import json

import pytest
from pydantic import ValidationError

from customer_service_agent.agent_api.api import encode_sse
from customer_service_agent.shared.models import AppEvent, EventSequencer, EventType


@pytest.mark.contract
def test_sse_frame_contains_sequence_id_event_name_and_public_envelope() -> None:
    event = EventSequencer(thread_id="thread-1", request_id="request-1").emit(
        EventType.MESSAGE_DELTA,
        {"text": "你好"},
    )

    frame = encode_sse(event).decode("utf-8")
    lines = frame.rstrip("\n").splitlines()
    payload = json.loads(lines[2].removeprefix("data: "))

    assert lines[0] == "id: 1"
    assert lines[1] == "event: message.delta"
    assert payload["schema_version"] == "1.0"
    assert payload["thread_id"] == "thread-1"
    assert payload["request_id"] == "request-1"
    assert payload["sequence"] == 1
    assert payload["payload"] == {"text": "你好"}


@pytest.mark.contract
@pytest.mark.parametrize(
    "forbidden_payload",
    [
        {"customer_id": "customer-a"},
        {"nested": {"api_key": "secret"}},
        {"prompt": "internal"},
        {"chain_of_thought": "internal"},
    ],
)
def test_event_payload_rejects_sensitive_fields(forbidden_payload: dict[str, object]) -> None:
    sequencer = EventSequencer(thread_id="thread-1", request_id="request-1")

    with pytest.raises(ValueError, match="sensitive"):
        sequencer.emit(EventType.TOOL_COMPLETED, forbidden_payload)


@pytest.mark.contract
def test_event_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AppEvent.model_validate(
            {
                "schema_version": "1.0",
                "event_id": "event-1",
                "event_type": "message.delta",
                "thread_id": "thread-1",
                "request_id": "request-1",
                "sequence": 1,
                "timestamp": "2026-08-31T00:00:00Z",
                "payload": {"text": "你好"},
                "unexpected": True,
            }
        )
