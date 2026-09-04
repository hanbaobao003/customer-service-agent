import pytest

from customer_service_agent.mvp.settings import MvpSettings, validate_owned_resource


def test_settings_rejects_missing_deepseek_api_key() -> None:
    with pytest.raises(ValueError, match="Deepseek_API_KEY"):
        MvpSettings.from_environment({})


def test_owned_resource_validation_rejects_unowned_name() -> None:
    with pytest.raises(ValueError, match="mvp resource"):
        validate_owned_resource("orders", "wang_agent_mvp_")
