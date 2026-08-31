import pytest

from customer_service_agent.shared.models import EventSequencer, EventType


@pytest.mark.unit
def test_event_sequence_starts_at_one_and_increments() -> None:
    sequencer = EventSequencer(thread_id="thread-1", request_id="request-1")

    assert sequencer.emit(EventType.MESSAGE_DELTA, {"text": "你"}).sequence == 1
    assert sequencer.emit(EventType.MESSAGE_COMPLETED, {"message": "你好"}).sequence == 2


@pytest.mark.unit
def test_terminal_event_rejects_later_events() -> None:
    sequencer = EventSequencer(thread_id="thread-1", request_id="request-1")
    sequencer.emit(EventType.APPROVAL_REQUIRED, {"interrupt_id": "interrupt-1"})

    with pytest.raises(ValueError, match="terminated"):
        sequencer.emit(EventType.MESSAGE_DELTA, {"text": "不应出现"})


@pytest.mark.unit
def test_retryable_error_does_not_terminate_stream() -> None:
    sequencer = EventSequencer(thread_id="thread-1", request_id="request-1")
    sequencer.emit(EventType.ERROR, {"retryable": True})

    event = sequencer.emit(EventType.MESSAGE_DELTA, {"text": "已恢复"})

    assert event.sequence == 2
