import os
from pathlib import Path

import pytest

from customer_service_agent.agent_api.service import PromptCatalog
from customer_service_agent.retrieval.raptor import DeepSeekRaptorSummarizer


@pytest.mark.live_model
@pytest.mark.asyncio
async def test_deepseek_summarizes_versioned_short_policy_fixture() -> None:
    from openai import AsyncOpenAI

    required = ("Deepseek_API_KEY", "Deepseek_BASE_URL", "Deepseek_flash_MODEL")
    if any(not os.getenv(name) for name in required):
        pytest.skip("set DeepSeek environment variables to run this live test")

    client = AsyncOpenAI(
        api_key=os.environ["Deepseek_API_KEY"],
        base_url=os.environ["Deepseek_BASE_URL"],
    )
    summarizer = DeepSeekRaptorSummarizer(
        client=client,
        prompt_catalog=PromptCatalog.from_path(
            Path(__file__).parents[2] / "config" / "prompts.yaml"
        ),
        model=os.environ["Deepseek_flash_MODEL"],
    )
    try:
        summary = await summarizer.summarize(
            ("未发货订单可取消；退款将原路返回。",),
            source_ids=("policy-v1#cancel",),
            level=1,
            prompt_version="raptor-summary-v1",
        )
    finally:
        await client.close()

    assert summary
