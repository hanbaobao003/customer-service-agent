from pathlib import Path

import pytest

from customer_service_agent.quality.portfolio_eval import (
    load_portfolio_cases,
    run_portfolio_experiment,
    score_selected_tool,
    sync_portfolio_dataset,
)


DATASET = Path(__file__).parents[3] / "evals/datasets/portfolio_v1.jsonl"


@pytest.mark.unit
def test_score_selected_tool_marks_expected_tool_as_passed() -> None:
    assert score_selected_tool(
        {},
        {"selected_tool": "get_order"},
        {"expected_tool": "get_order"},
    ) == {"key": "tool_route", "score": 1}


@pytest.mark.unit
def test_portfolio_dataset_has_one_case_for_each_demo_capability() -> None:
    cases = load_portfolio_cases(DATASET)

    assert {case.group for case in cases} == {
        "route",
        "rag_hybrid",
        "rag_raptor",
        "rag_graph",
        "sql",
        "order",
    }
    assert len({case.case_id for case in cases}) == 6


@pytest.mark.unit
def test_sync_reuses_existing_dataset_without_creating_examples() -> None:
    class ExistingDatasetClient:
        def __init__(self) -> None:
            self.created_examples: list[object] = []

        def has_dataset(self, *, dataset_name: str) -> bool:
            return dataset_name == "wang-agent-portfolio-v1"

        def create_dataset(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("existing dataset must not be recreated")

        def create_examples(self, **kwargs: object) -> None:
            self.created_examples.append(kwargs)

    client = ExistingDatasetClient()

    assert sync_portfolio_dataset(
        client,
        "wang-agent-portfolio-v1",
        load_portfolio_cases(DATASET),
    ) == "wang-agent-portfolio-v1"
    assert client.created_examples == []


@pytest.mark.unit
def test_run_portfolio_experiment_uses_the_fixed_dataset_and_scorer() -> None:
    class ExperimentClient:
        def __init__(self) -> None:
            self.arguments: dict[str, object] | None = None

        def evaluate(self, target: object, /, **kwargs: object) -> str:
            self.arguments = {"target": target, **kwargs}
            return "experiment-result"

    def target(_inputs: dict[str, str]) -> dict[str, str]:
        return {"selected_tool": "get_order"}

    client = ExperimentClient()

    assert run_portfolio_experiment(client, "wang-agent-portfolio-v1", target) == "experiment-result"
    assert client.arguments is not None
    assert client.arguments["data"] == "wang-agent-portfolio-v1"
    assert client.arguments["experiment_prefix"] == "wang-agent-portfolio"
    assert client.arguments["max_concurrency"] == 1
