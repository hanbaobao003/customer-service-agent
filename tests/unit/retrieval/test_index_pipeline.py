import pytest

from customer_service_agent.retrieval.indexing import (
    CandidateIndex,
    IndexBuildFailed,
    IndexPipeline,
    PublishedIndexRef,
    ValidationResult,
)


class Builder:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def build_candidate(self, data_version: str) -> CandidateIndex:
        if self.error is not None:
            raise self.error
        return CandidateIndex(
            data_version=data_version,
            index_ref=f"candidate/{data_version}",
            input_hash="sha256:fixture",
            document_count=3,
            item_count=9,
            config_ref="retrieval-config-v1",
            duration_ms=12.5,
        )


class Validator:
    def __init__(
        self,
        *,
        passed: bool,
        error: Exception | None = None,
    ) -> None:
        self.passed = passed
        self.error = error

    def validate(self, candidate: CandidateIndex) -> ValidationResult:
        if self.error is not None:
            raise self.error
        return ValidationResult(
            passed=self.passed,
            smoke_results=("faq-hit", "missing-answer"),
            failure_code=None if self.passed else "SMOKE_FAILED",
        )


class Publisher:
    def __init__(self, current: str) -> None:
        self.current = current
        self.calls: list[CandidateIndex] = []

    def publish(self, candidate: CandidateIndex) -> PublishedIndexRef:
        self.calls.append(candidate)
        self.current = candidate.index_ref
        return PublishedIndexRef(
            data_version=candidate.data_version,
            index_ref=candidate.index_ref,
        )


class ReportSink:
    def __init__(self) -> None:
        self.reports: list[object] = []

    def write(self, report: object) -> None:
        self.reports.append(report)


def pipeline(
    *,
    builder: Builder | None = None,
    passed: bool = True,
    validator_error: Exception | None = None,
) -> tuple[IndexPipeline, Publisher, ReportSink]:
    publisher = Publisher(current="published/v1")
    sink = ReportSink()
    return (
        IndexPipeline(
            builder=builder or Builder(),
            validator=Validator(passed=passed, error=validator_error),
            publisher=publisher,
            report_sink=sink,
        ),
        publisher,
        sink,
    )


@pytest.mark.unit
def test_failed_candidate_never_replaces_published_version() -> None:
    index_pipeline, publisher, sink = pipeline(passed=False)

    with pytest.raises(IndexBuildFailed, match="validation"):
        index_pipeline.run(data_version="catalog-2026-09")

    assert publisher.current == "published/v1"
    assert publisher.calls == []
    report = sink.reports[0]
    assert report.status == "failed"
    assert report.stages == ("candidate_built",)
    assert report.failure_stage == "validation"
    assert report.failure_code == "SMOKE_FAILED"


@pytest.mark.unit
def test_successful_build_publishes_only_after_validation() -> None:
    index_pipeline, publisher, sink = pipeline()

    published = index_pipeline.run(data_version="catalog-2026-09")

    assert published == PublishedIndexRef(
        data_version="catalog-2026-09",
        index_ref="candidate/catalog-2026-09",
    )
    assert publisher.current == "candidate/catalog-2026-09"
    report = sink.reports[0]
    assert report.status == "published"
    assert report.stages == ("candidate_built", "validated", "published")
    assert report.input_hash == "sha256:fixture"
    assert report.document_count == 3
    assert report.item_count == 9
    assert report.smoke_results == ("faq-hit", "missing-answer")


@pytest.mark.unit
def test_exception_message_is_not_copied_into_failed_report() -> None:
    secret = "sensitive-provider-detail"
    index_pipeline, publisher, sink = pipeline(
        builder=Builder(error=RuntimeError(secret)),
    )

    with pytest.raises(IndexBuildFailed, match="candidate build"):
        index_pipeline.run(data_version="catalog-2026-09")

    assert publisher.calls == []
    report = sink.reports[0]
    assert report.status == "failed"
    assert report.stages == ()
    assert report.failure_stage == "candidate_build"
    assert report.failure_code == "RuntimeError"
    assert secret not in repr(report)


@pytest.mark.unit
def test_validator_exception_is_reported_without_publishing() -> None:
    index_pipeline, publisher, sink = pipeline(
        validator_error=ConnectionError("driver unavailable"),
    )

    with pytest.raises(IndexBuildFailed, match="validation"):
        index_pipeline.run(data_version="catalog-2026-09")

    assert publisher.calls == []
    report = sink.reports[0]
    assert report.status == "failed"
    assert report.stages == ("candidate_built",)
    assert report.failure_stage == "validation"
    assert report.failure_code == "ConnectionError"
