"""Owned resource names for local MVP seed data."""

from dataclasses import dataclass

from customer_service_agent.mvp.settings import validate_owned_resource


_MVP_PREFIX = "wang_agent_mvp_"


@dataclass(frozen=True)
class MvpResources:
    faq_collection: str
    raptor_collection: str
    graph_version: str

    def __post_init__(self) -> None:
        for name in (self.faq_collection, self.raptor_collection, self.graph_version):
            validate_owned_resource(name, _MVP_PREFIX)

    @classmethod
    def default(cls) -> "MvpResources":
        return cls(
            faq_collection="wang_agent_mvp_faq_v1",
            raptor_collection="wang_agent_mvp_raptor_v1",
            graph_version="wang_agent_mvp_v1",
        )
