"""Minimal deterministic evaluation helpers for the portfolio demo."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, field_validator


DEMO_GROUPS = frozenset(
    {"route", "rag_hybrid", "rag_raptor", "rag_graph", "sql", "order"}
)


class PortfolioEvalCase(BaseModel):
    """A sanitized case used to demonstrate the evaluation pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    group: str
    message: str
    expected_tool: str

    @field_validator("case_id", "message", "expected_tool")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("group")
    @classmethod
    def group_must_be_supported(cls, value: str) -> str:
        if value not in DEMO_GROUPS:
            raise ValueError(f"unsupported portfolio evaluation group: {value}")
        return value

    def as_example(self) -> dict[str, dict[str, str]]:
        return {
            "inputs": {"message": self.message},
            "outputs": {"expected_tool": self.expected_tool},
            "metadata": {"case_id": self.case_id, "group": self.group},
        }


class _Dataset(Protocol):
    id: object


class PortfolioDatasetClient(Protocol):
    def has_dataset(self, *, dataset_name: str) -> bool: ...

    def create_dataset(self, dataset_name: str, *, description: str) -> _Dataset: ...

    def create_examples(
        self,
        *,
        dataset_id: object,
        examples: list[dict[str, dict[str, str]]],
    ) -> object: ...


class PortfolioExperimentClient(Protocol):
    def evaluate(
        self,
        target: Callable[[dict[str, str]], dict[str, str]],
        /,
        **kwargs: object,
    ) -> object: ...


def load_portfolio_cases(path: Path) -> tuple[PortfolioEvalCase, ...]:
    """Load exactly one sanitized case for each portfolio capability."""

    cases = tuple(
        PortfolioEvalCase.model_validate_json(line)
        for line in _nonblank_lines(path.read_text(encoding="utf-8").splitlines())
    )
    case_ids = {case.case_id for case in cases}
    if len(case_ids) != len(cases):
        raise ValueError("portfolio evaluation case_id values must be unique")
    if {case.group for case in cases} != DEMO_GROUPS:
        raise ValueError("portfolio evaluation must contain every demo group exactly once")
    if len(cases) != len(DEMO_GROUPS):
        raise ValueError("portfolio evaluation must contain exactly six cases")
    return cases


def sync_portfolio_dataset(
    client: PortfolioDatasetClient,
    dataset_name: str,
    cases: tuple[PortfolioEvalCase, ...],
) -> str:
    """Create the versioned dataset once; later runs reuse it unchanged."""

    if client.has_dataset(dataset_name=dataset_name):
        return dataset_name
    dataset = client.create_dataset(
        dataset_name,
        description="wang-agent portfolio evaluation v1",
    )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[case.as_example() for case in cases],
    )
    return dataset_name


def run_portfolio_experiment(
    client: PortfolioExperimentClient,
    dataset_name: str,
    target: Callable[[dict[str, str]], dict[str, str]],
) -> object:
    """Run the small, deterministic portfolio experiment in LangSmith."""

    return client.evaluate(
        target,
        data=dataset_name,
        evaluators=[score_selected_tool],
        experiment_prefix="wang-agent-portfolio",
        max_concurrency=1,
        metadata={"dataset_version": "portfolio-v1"},
    )


def _nonblank_lines(lines: Iterable[str]) -> Iterable[str]:
    return (line for line in lines if line.strip())


def score_selected_tool(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, object]:
    """Score whether the observed tool equals the case's expected tool."""

    del inputs
    return {
        "key": "tool_route",
        "score": int(outputs.get("selected_tool") == reference_outputs.get("expected_tool")),
    }
