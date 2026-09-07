import pytest

from customer_service_agent.retrieval import indexing


@pytest.mark.integration_milvus
def test_cleanup_rejects_database_not_owned_by_current_run() -> None:
    with pytest.raises(ValueError, match="owned database"):
        indexing.validate_milvus_owned_database("default", "a1b2c3d4e5f6")


@pytest.mark.integration_milvus
def test_run_resources_are_deterministic_and_isolated() -> None:
    resources = indexing.make_milvus_run_resources("a1b2c3d4e5f6")

    assert resources.database_name == "wang_agent_it_a1b2c3d4e5f6"
    assert resources.hybrid_collection == "hybrid_candidate_a1b2c3d4e5f6"
    assert resources.raptor_collection == "raptor_candidate_a1b2c3d4e5f6"
    assert resources.hybrid_alias == "hybrid_current_a1b2c3d4e5f6"
    assert resources.raptor_alias == "raptor_current_a1b2c3d4e5f6"


@pytest.mark.integration_milvus
def test_run_resources_reject_non_hex_or_wrong_length_run_id() -> None:
    with pytest.raises(ValueError, match="12 lowercase hex"):
        indexing.make_milvus_run_resources("outside-project")


@pytest.mark.integration_milvus
def test_isolated_fixture_creates_only_the_current_run_database(
    milvus_admin_client,
    isolated_milvus_database,
) -> None:
    assert isolated_milvus_database.database_name in milvus_admin_client.list_databases()
    assert isolated_milvus_database.database_name.startswith("wang_agent_it_")
