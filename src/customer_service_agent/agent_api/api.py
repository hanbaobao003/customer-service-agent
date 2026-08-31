"""HTTP and SSE adapter boundary."""

from customer_service_agent.shared.models import AppEvent


def encode_sse(event: AppEvent) -> bytes:
    frame = (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {event.model_dump_json()}\n\n"
    )
    return frame.encode("utf-8")
