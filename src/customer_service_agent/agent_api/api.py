"""HTTP and SSE adapter boundary."""

from collections.abc import Callable, Mapping
from typing import Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from customer_service_agent.agent_api.service import CustomerService
from customer_service_agent.config import MAX_MESSAGE_CHARACTERS
from customer_service_agent.shared.models import AppEvent, RuntimeContext


class TrustedContextProvider(Protocol):
    async def get_customer_id(self, request: Request) -> str: ...


class HealthProvider(Protocol):
    async def readiness(self) -> Mapping[str, bool]: ...


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        if len(normalized) > MAX_MESSAGE_CHARACTERS:
            raise ValueError(
                f"message must not exceed {MAX_MESSAGE_CHARACTERS} characters"
            )
        return normalized


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interrupt_id: str
    decision: Literal["approve", "reject"]
    reason: str | None = None

    @field_validator("interrupt_id")
    @classmethod
    def interrupt_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("interrupt_id must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def reject_requires_reason(self) -> "DecisionRequest":
        if self.decision == "reject" and not (self.reason and self.reason.strip()):
            raise ValueError("reject decision requires a reason")
        if self.reason is not None:
            self.reason = self.reason.strip()
        return self


def encode_sse(event: AppEvent) -> bytes:
    frame = (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {event.model_dump_json()}\n\n"
    )
    return frame.encode("utf-8")


def _request_id() -> str:
    return str(uuid4())


def create_app(
    *,
    service: CustomerService,
    context_provider: TrustedContextProvider,
    health: HealthProvider,
    request_id_factory: Callable[[], str] = _request_id,
) -> FastAPI:
    app = FastAPI()

    async def trusted_context(request: Request, thread_id: str) -> RuntimeContext:
        return RuntimeContext.trusted(
            customer_id=await context_provider.get_customer_id(request),
            thread_id=thread_id,
            request_id=request_id_factory(),
        )

    @app.post("/v1/threads/{thread_id}/messages:stream")
    async def stream_message(
        thread_id: str,
        body: MessageRequest,
        request: Request,
    ) -> StreamingResponse:
        context = await trusted_context(request, thread_id)
        events = service.stream_message(context, body.message)
        return StreamingResponse(
            (encode_sse(event) async for event in events),
            media_type="text/event-stream",
        )

    @app.post("/v1/threads/{thread_id}/decisions")
    async def resume_decision(
        thread_id: str,
        body: DecisionRequest,
        request: Request,
    ) -> StreamingResponse:
        context = await trusted_context(request, thread_id)
        events = service.resume_decision(
            context,
            interrupt_id=body.interrupt_id,
            decision=body.decision,
            reason=body.reason,
        )
        return StreamingResponse(
            (encode_sse(event) async for event in events),
            media_type="text/event-stream",
        )

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        dependencies = {
            name: bool(ready) for name, ready in (await health.readiness()).items()
        }
        ready = all(dependencies.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "dependencies": dependencies,
            },
        )

    return app
