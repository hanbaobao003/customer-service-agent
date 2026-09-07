"""Offline index build, validation, and publication contracts."""

import re
from dataclasses import dataclass
from typing import Literal, Protocol


class IndexBuildFailed(RuntimeError):
    pass


_MILVUS_RUN_ID_RE = re.compile(r"[0-9a-f]{12}")
_MILVUS_DATABASE_RE = re.compile(r"wang_agent_it_[0-9a-f]{12}")


@dataclass(frozen=True)
class MilvusRunResources:
    run_id: str
    database_name: str
    hybrid_collection: str
    raptor_collection: str
    hybrid_alias: str
    raptor_alias: str


def make_milvus_run_resources(run_id: str) -> MilvusRunResources:
    if _MILVUS_RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("run_id must be 12 lowercase hex characters")
    return MilvusRunResources(
        run_id=run_id,
        database_name=f"wang_agent_it_{run_id}",
        hybrid_collection=f"hybrid_candidate_{run_id}",
        raptor_collection=f"raptor_candidate_{run_id}",
        hybrid_alias=f"hybrid_current_{run_id}",
        raptor_alias=f"raptor_current_{run_id}",
    )


def validate_milvus_owned_database(name: str, run_id: str) -> None:
    resources = make_milvus_run_resources(run_id)
    if (
        _MILVUS_DATABASE_RE.fullmatch(name) is None
        or name != resources.database_name
    ):
        raise ValueError("owned database does not match this test run")


@dataclass(frozen=True)
class CandidateIndex:
    data_version: str
    index_ref: str
    input_hash: str
    document_count: int
    item_count: int
    config_ref: str
    duration_ms: float
    kind: Literal["hybrid", "raptor"] = "hybrid"


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


class MilvusAliasClientPort(Protocol):
    def smoke(self, collection_name: str) -> bool: ...

    def alter_alias(self, *, collection_name: str, alias: str) -> None: ...


class MilvusIndexPublisher:
    def __init__(
        self,
        *,
        client: MilvusAliasClientPort,
        current_refs: dict[str, str],
    ) -> None:
        self._client = client
        self._current_refs = dict(current_refs)

    def current_ref(self, kind: Literal["hybrid", "raptor"]) -> str | None:
        return self._current_refs.get(kind)

    def publish(self, candidate: CandidateIndex) -> PublishedIndexRef:
        if not self._client.smoke(candidate.index_ref):
            raise IndexBuildFailed("validation failed")
        self._client.alter_alias(
            collection_name=candidate.index_ref,
            alias=f"{candidate.kind}_current",
        )
        self._current_refs[candidate.kind] = candidate.index_ref
        return PublishedIndexRef(
            data_version=candidate.data_version,
            index_ref=candidate.index_ref,
        )


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
