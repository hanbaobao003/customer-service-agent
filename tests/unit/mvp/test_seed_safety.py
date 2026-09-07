import pytest

from customer_service_agent.mvp.seed import MvpResources


def test_mvp_resources_use_owned_names() -> None:
    resources = MvpResources.default()

    assert resources.faq_collection.startswith("wang_agent_mvp_")
    assert resources.raptor_collection.startswith("wang_agent_mvp_")
    assert resources.graph_version.startswith("wang_agent_mvp_")


def test_mvp_resources_reject_unowned_collection() -> None:
    with pytest.raises(ValueError, match="mvp resource"):
        MvpResources(
            faq_collection="faq",
            raptor_collection="wang_agent_mvp_raptor_v1",
            graph_version="wang_agent_mvp_v1",
        )
