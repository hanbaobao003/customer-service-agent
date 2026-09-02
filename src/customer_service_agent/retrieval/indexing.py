"""Offline index build, validation, and publication contracts."""

from dataclasses import dataclass
from typing import Literal, Protocol


class IndexBuildFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateIndex:
    data_version: str
    index_ref: str
    input_hash: str
    document_count: int
    item_count: int
    config_ref: str
    duration_ms: float


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    smoke_results: tuple[str, ...]
    failure_code: str | None


@dataclass(frozen=True)
class PublishedIndexRef:
    data_version: str
    index_ref: str


@dataclass(frozen=True)
class IndexBuildReport:
    data_version: str
    status: Literal["failed", "published"]
    stages: tuple[str, ...]
    input_hash: str | None = None
    document_count: int | None = None
    item_count: int | None = None
    config_ref: str | None = None
    duration_ms: float | None = None
    smoke_results: tuple[str, ...] = ()
    failure_stage: str | None = None
    failure_code: str | None = None


class CandidateBuilderPort(Protocol):
    def build_candidate(self, data_version: str) -> CandidateIndex: ...


class CandidateValidatorPort(Protocol):
    def validate(self, candidate: CandidateIndex) -> ValidationResult: ...


class IndexPublisherPort(Protocol):
    def publish(self, candidate: CandidateIndex) -> PublishedIndexRef: ...


class BuildReportSinkPort(Protocol):
    def write(self, report: IndexBuildReport) -> None: ...


class IndexPipeline:
    def __init__(
        self,
        *,
        builder: CandidateBuilderPort,
        validator: CandidateValidatorPort,
        publisher: IndexPublisherPort,
        report_sink: BuildReportSinkPort,
    ) -> None:
        self.builder = builder
        self.validator = validator
        self.publisher = publisher
        self.report_sink = report_sink

    def run(self, *, data_version: str) -> PublishedIndexRef:
        if not data_version:
            raise ValueError("data_version must not be empty")
        try:
            candidate = self.builder.build_candidate(data_version)
        except Exception as error:
            self.report_sink.write(
                IndexBuildReport(
                    data_version=data_version,
                    status="failed",
                    stages=(),
                    failure_stage="candidate_build",
                    failure_code=type(error).__name__,
                )
            )
            raise IndexBuildFailed("candidate build failed") from error

        try:
            validation = self.validator.validate(candidate)
        except Exception as error:
            self.report_sink.write(
                _report(
                    candidate,
                    status="failed",
                    stages=("candidate_built",),
                    smoke_results=(),
                    failure_stage="validation",
                    failure_code=type(error).__name__,
                )
            )
            raise IndexBuildFailed("validation failed") from error
        if not validation.passed:
            self.report_sink.write(
                _report(
                    candidate,
                    status="failed",
                    stages=("candidate_built",),
                    smoke_results=validation.smoke_results,
                    failure_stage="validation",
                    failure_code=validation.failure_code or "VALIDATION_FAILED",
                )
            )
            raise IndexBuildFailed("validation failed")

        published = self.publisher.publish(candidate)
        self.report_sink.write(
            _report(
                candidate,
                status="published",
                stages=("candidate_built", "validated", "published"),
                smoke_results=validation.smoke_results,
            )
        )
        return published


def _report(
    candidate: CandidateIndex,
    *,
    status: Literal["failed", "published"],
    stages: tuple[str, ...],
    smoke_results: tuple[str, ...],
    failure_stage: str | None = None,
    failure_code: str | None = None,
) -> IndexBuildReport:
    return IndexBuildReport(
        data_version=candidate.data_version,
        status=status,
        stages=stages,
        input_hash=candidate.input_hash,
        document_count=candidate.document_count,
        item_count=candidate.item_count,
        config_ref=candidate.config_ref,
        duration_ms=candidate.duration_ms,
        smoke_results=smoke_results,
        failure_stage=failure_stage,
        failure_code=failure_code,
    )
