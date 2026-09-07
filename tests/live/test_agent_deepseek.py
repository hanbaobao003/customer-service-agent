import os

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from customer_service_agent.agent_api.service import build_deepseek_agent_model


@pytest.mark.live_model
@pytest.mark.asyncio
async def test_deepseek_agent_model_returns_a_bound_tool_call() -> None:
    required = ("Deepseek_API_KEY", "Deepseek_BASE_URL")
    if any(not os.getenv(name) for name in required):
        pytest.skip("set DeepSeek environment variables to run this live test")

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

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["name"] == "echo_test"
    assert response.tool_calls[0]["args"] == {"text": "ping"}
