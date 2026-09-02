"""Opt-in smoke test for the portfolio dataset, experiment, and model trace."""

import os
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langsmith import Client

from customer_service_agent.agent_api.service import build_deepseek_agent_model
from customer_service_agent.quality.portfolio_eval import (
    load_portfolio_cases,
    run_portfolio_experiment,
    sync_portfolio_dataset,
)


DATASET = Path(__file__).parents[2] / "evals/datasets/portfolio_v1.jsonl"
DATASET_NAME = "wang-agent-portfolio-v1"


@pytest.mark.live_langsmith
@pytest.mark.live_model
@pytest.mark.asyncio
async def test_langsmith_records_portfolio_experiment_and_model_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = ("LANGSMITH_API_KEY", "Deepseek_API_KEY", "Deepseek_BASE_URL")
    if any(not os.getenv(name) for name in required):
        pytest.skip("configure LangSmith and DeepSeek environment variables")

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "wang-agent-portfolio")
    cases = load_portfolio_cases(DATASET)
    expected_tools = {case.message: case.expected_tool for case in cases}
    client = Client()
    dataset_name = sync_portfolio_dataset(client, DATASET_NAME, cases)

    results = run_portfolio_experiment(
        client,
        dataset_name,
        lambda inputs: {"selected_tool": expected_tools[inputs["message"]]},
    )

    @tool
    def echo_test(text: str) -> str:
        """Echo a short test value."""

        return text

    model = build_deepseek_agent_model(
        api_key=os.environ["Deepseek_API_KEY"],
        base_url=os.environ["Deepseek_BASE_URL"],
    ).bind_tools([echo_test])
    response = await model.ainvoke(
        [HumanMessage(content="请调用 echo_test 工具，参数 text 必须为 ping。不要输出普通文本。")]
    )

    assert results is not None
    assert response.tool_calls[0]["name"] == "echo_test"
