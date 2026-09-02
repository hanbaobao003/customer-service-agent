"""Deterministic RAPTOR tree contracts."""

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pymilvus import DataType

from customer_service_agent.retrieval.models import RetrievalArtifact, RetrievalHit


class RaptorIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class RaptorBuildConfig:
    summary_levels: int = 3
    cluster_size: int = 8
    seed: int = 42
    prompt_version: str = "raptor-summary-v1"

    def __post_init__(self) -> None:
        if (
            self.summary_levels != 3
            or self.cluster_size != 8
            or self.seed != 42
            or self.prompt_version != "raptor-summary-v1"
        ):
            raise RaptorIntegrityError("only approved RAPTOR build parameters are allowed")


@dataclass(frozen=True)
class RaptorNode:
    node_id: str
    document_id: str
    level: int
    text: str
    child_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    data_version: str
    locator: str | None = None

    @classmethod
    def leaf(
        cls,
        *,
        document_id: str,
        text: str,
        source_id: str,
        locator: str,
        data_version: str,
    ) -> "RaptorNode":
        return cls(
            node_id=_node_id(
                data_version=data_version,
                level=0,
                child_ids=(),
                text=text,
            ),
            document_id=document_id,
            level=0,
            text=text,
            child_ids=(),
            source_ids=(source_id,),
            data_version=data_version,
            locator=locator,
        )

    @classmethod
    def summary(
        cls,
        *,
        document_id: str,
        level: int,
        text: str,
        child_ids: Sequence[str],
        source_ids: Sequence[str],
        data_version: str,
    ) -> "RaptorNode":
        sorted_children = tuple(sorted(child_ids))
        return cls(
            node_id=_node_id(
                data_version=data_version,
                level=level,
                child_ids=sorted_children,
                text=text,
            ),
            document_id=document_id,
            level=level,
            text=text,
            child_ids=sorted_children,
            source_ids=tuple(sorted(source_ids)),
            data_version=data_version,
        )


@dataclass(frozen=True)
class RaptorLeaf:
    node: RaptorNode
    dense_vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.node.level != 0:
            raise RaptorIntegrityError("RAPTOR input must contain leaf nodes")
        if len(self.dense_vector) != 1024:
            raise RaptorIntegrityError("RAPTOR leaf vector must have 1024 dimensions")


@dataclass(frozen=True)
class RaptorHit:
    node_id: str
    score: float


@dataclass(frozen=True)
class RaptorPath:
    root_node_id: str
    root_level: int
    root_score: float
    node_ids: tuple[str, ...]
    source_id: str
    leaf_text: str
    leaf_locator: str
    root_text: str


class NodeStorePort(Protocol):
    def get(self, node_id: str) -> RaptorNode | None: ...


class SummarizerPort(Protocol):
    async def summarize(
        self,
        texts: Sequence[str],
        *,
        source_ids: Sequence[str],
        level: int,
        prompt_version: str,
    ) -> str: ...


def create_raptor_collection(client: object, collection_name: str) -> None:
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(
        field_name="node_id",
        datatype=DataType.VARCHAR,
        is_primary=True,
        max_length=256,
    )
    schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="level", datatype=DataType.INT64)
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
    schema.add_field(field_name="child_ids", datatype=DataType.JSON)
    schema.add_field(field_name="source_ids", datatype=DataType.JSON)
    schema.add_field(field_name="data_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="locator", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="is_root", datatype=DataType.BOOL)
    schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=1024)
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="dense_vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )


class MilvusRaptorStore:
    def __init__(
        self,
        *,
        client: object,
        collection_name: str,
        data_version: str,
        embed_query: Callable[[str], Sequence[float]],
    ) -> None:
        if not collection_name or not data_version:
            raise RaptorIntegrityError("collection name and data version must not be empty")
        self._client = client
        self._collection_name = collection_name
        self._data_version = data_version
        self._embed_query = embed_query

    def insert(
        self,
        *,
        nodes: Sequence[RaptorNode],
        dense_vectors: Mapping[str, Sequence[float]],
    ) -> None:
        validate_tree(nodes)
        node_ids = {node.node_id for node in nodes}
        if set(dense_vectors) != node_ids:
            raise RaptorIntegrityError("RAPTOR vectors must match tree nodes")
        if any(len(dense_vectors[node_id]) != 1024 for node_id in node_ids):
            raise RaptorIntegrityError("RAPTOR vectors must have 1024 dimensions")
        child_ids = {child_id for node in nodes for child_id in node.child_ids}
        self._client.insert(
            collection_name=self._collection_name,
            data=[
                {
                    "node_id": node.node_id,
                    "document_id": node.document_id,
                    "level": node.level,
                    "text": node.text,
                    "child_ids": list(node.child_ids),
                    "source_ids": list(node.source_ids),
                    "data_version": node.data_version,
                    "locator": node.locator or "",
                    "is_root": node.node_id not in child_ids,
                    "dense_vector": list(dense_vectors[node.node_id]),
                }
                for node in nodes
            ],
        )
        self._client.flush(self._collection_name)

    async def search_roots(self, query: str, *, limit: int) -> Sequence[RaptorHit]:
        if limit < 1:
            raise RaptorIntegrityError("search limit must be positive")
        result = self._client.search(
            collection_name=self._collection_name,
            data=[list(self._embed_query(query))],
            anns_field="dense_vector",
            filter=(
                f'data_version == {_milvus_string(self._data_version)} and is_root == true'
            ),
            limit=limit,
            output_fields=["node_id"],
            search_params={"metric_type": "COSINE", "params": {}},
        )
        return [
            RaptorHit(node_id=str(_milvus_row(item)["node_id"]), score=float(item["distance"]))
            for item in result[0]
        ]

    def get(self, node_id: str) -> RaptorNode | None:
        rows = self._client.query(
            collection_name=self._collection_name,
            filter=(
                f'node_id == {_milvus_string(node_id)} and '
                f'data_version == {_milvus_string(self._data_version)}'
            ),
            output_fields=[
                "node_id",
                "document_id",
                "level",
                "text",
                "child_ids",
                "source_ids",
                "data_version",
                "locator",
            ],
        )
        if not rows:
            return None
        row = rows[0]
        return RaptorNode(
            node_id=str(row["node_id"]),
            document_id=str(row["document_id"]),
            level=int(row["level"]),
            text=str(row["text"]),
            child_ids=tuple(str(value) for value in row["child_ids"]),
            source_ids=tuple(str(value) for value in row["source_ids"]),
            data_version=str(row["data_version"]),
            locator=str(row["locator"]) or None,
        )


@dataclass(frozen=True)
class _IndexedNode:
    node: RaptorNode
    dense_vector: tuple[float, ...]


async def build_raptor_tree(
    leaves: Sequence[RaptorLeaf],
    *,
    config: RaptorBuildConfig,
    summarizer: SummarizerPort,
) -> tuple[RaptorNode, ...]:
    if not leaves:
        raise RaptorIntegrityError("RAPTOR tree requires at least one leaf")
    current = [
        _IndexedNode(node=item.node, dense_vector=item.dense_vector) for item in leaves
    ]
    nodes = [item.node for item in current]
    for level in range(1, config.summary_levels + 1):
        next_level: list[_IndexedNode] = []
        for document_id in sorted({item.node.document_id for item in current}):
            document_nodes = [
                item for item in current if item.node.document_id == document_id
            ]
            for siblings in _cosine_groups(document_nodes, cluster_size=config.cluster_size):
                source_ids = tuple(
                    sorted(
                        {
                            source_id
                            for item in siblings
                            for source_id in item.node.source_ids
                        }
                    )
                )
                text = await summarizer.summarize(
                    tuple(item.node.text for item in siblings),
                    source_ids=source_ids,
                    level=level,
                    prompt_version=config.prompt_version,
                )
                if not text.strip():
                    raise RaptorIntegrityError("RAPTOR summary must not be empty")
                node = RaptorNode.summary(
                    document_id=document_id,
                    level=level,
                    text=text,
                    child_ids=[item.node.node_id for item in siblings],
                    source_ids=source_ids,
                    data_version=siblings[0].node.data_version,
                )
                next_level.append(
                    _IndexedNode(node=node, dense_vector=_centroid(siblings))
                )
                nodes.append(node)
        current = next_level
    validate_tree(nodes)
    return tuple(nodes)


def _cosine_groups(
    nodes: Sequence[_IndexedNode],
    *,
    cluster_size: int,
) -> list[list[_IndexedNode]]:
    remaining = sorted(
        nodes,
        key=lambda item: (
            item.node.document_id,
            item.node.source_ids,
            item.node.node_id,
        ),
    )
    groups: list[list[_IndexedNode]] = []
    while remaining:
        anchor = remaining.pop(0)
        ranked = sorted(
            remaining,
            key=lambda item: (-_cosine(anchor.dense_vector, item.dense_vector), item.node.node_id),
        )
        siblings = [anchor, *ranked[: cluster_size - 1]]
        sibling_ids = {item.node.node_id for item in siblings}
        remaining = [item for item in remaining if item.node.node_id not in sibling_ids]
        groups.append(siblings)
    if len(groups) > 1 and len(groups[-1]) == 1:
        groups[-2].extend(groups.pop())
    return groups


def _cosine(first: Sequence[float], second: Sequence[float]) -> float:
    numerator = sum(left * right for left, right in zip(first, second, strict=True))
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    if first_norm == 0 or second_norm == 0:
        return 0.0
    return numerator / (first_norm * second_norm)


def _centroid(nodes: Sequence[_IndexedNode]) -> tuple[float, ...]:
    return tuple(
        sum(item.dense_vector[index] for item in nodes) / len(nodes)
        for index in range(1024)
    )


def _milvus_row(item: Mapping[str, object]) -> Mapping[str, object]:
    entity = item.get("entity")
    return entity if isinstance(entity, Mapping) else item


def _milvus_string(value: str) -> str:
    return f'"{value.replace("\\", "\\\\").replace(chr(34), "\\\"")}"'


def validate_tree(nodes: Sequence[RaptorNode]) -> None:
    by_id: dict[str, RaptorNode] = {}
    for node in nodes:
        if node.node_id in by_id:
            raise RaptorIntegrityError(f"duplicate node: {node.node_id}")
        _validate_node_fields(node)
        by_id[node.node_id] = node

    for node in nodes:
        if node.level == 0:
            continue
        child_sources: set[str] = set()
        for child_id in node.child_ids:
            child = by_id.get(child_id)
            if child is None:
                raise RaptorIntegrityError(f"missing child: {child_id}")
            if child.level != node.level - 1:
                raise RaptorIntegrityError(
                    f"child level mismatch for node: {node.node_id}"
                )
            if (
                child.document_id != node.document_id
                or child.data_version != node.data_version
            ):
                raise RaptorIntegrityError(
                    f"child lineage mismatch for node: {node.node_id}"
                )
            child_sources.update(child.source_ids)
        if tuple(sorted(child_sources)) != node.source_ids:
            raise RaptorIntegrityError(
                f"source mismatch for node: {node.node_id}"
            )


def descend_hits(
    root_hits: Sequence[RaptorHit],
    node_store: NodeStorePort,
) -> list[RaptorPath]:
    paths: list[RaptorPath] = []
    seen_leaves: set[str] = set()

    for hit in root_hits:
        root = _require_node(node_store, hit.node_id)
        _descend(
            node=root,
            root=root,
            root_score=hit.score,
            node_store=node_store,
            node_ids=(root.node_id,),
            seen_leaves=seen_leaves,
            paths=paths,
        )
    return paths


def build_raptor_artifact(
    *,
    query: str,
    index_version: str,
    root_hits: Sequence[RaptorHit],
    paths: Sequence[RaptorPath],
) -> RetrievalArtifact:
    paths_by_root = {path.root_node_id: path for path in paths}
    root_artifacts: list[RetrievalHit] = []
    for hit in root_hits:
        path = paths_by_root.get(hit.node_id)
        if path is None:
            raise RaptorIntegrityError(f"root has no leaf evidence: {hit.node_id}")
        root_artifacts.append(
            RetrievalHit(
                source_id=hit.node_id,
                score=hit.score,
                raw_text=path.root_text,
                metadata={
                    "channel": "raptor_root",
                    "node_id": hit.node_id,
                    "hit_level": path.root_level,
                },
            )
        )

    leaf_artifacts = [
        RetrievalHit(
            source_id=path.source_id,
            parent_id=path.root_node_id,
            score=path.root_score,
            raw_text=path.leaf_text,
            metadata={
                "channel": "raptor_leaf",
                "node_id": path.node_ids[-1],
                "hit_level": 0,
                "leaf_locator": path.leaf_locator,
                "path": list(path.node_ids),
            },
        )
        for path in paths
    ]
    return RetrievalArtifact(
        retriever="raptor",
        query=query,
        index_version=index_version,
        hits=tuple((*root_artifacts, *leaf_artifacts)),
    )


def _node_id(
    *,
    data_version: str,
    level: int,
    child_ids: Sequence[str],
    text: str,
) -> str:
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    payload = json.dumps(
        [data_version, level, sorted(child_ids), content_hash],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"raptor-{hashlib.sha256(payload.encode()).hexdigest()}"


def _validate_node_fields(node: RaptorNode) -> None:
    if not node.document_id or not node.text or not node.data_version:
        raise RaptorIntegrityError("node fields must not be empty")
    if node.level < 0:
        raise RaptorIntegrityError("node level must not be negative")
    if len(set(node.child_ids)) != len(node.child_ids):
        raise RaptorIntegrityError("node children must be unique")
    if len(set(node.source_ids)) != len(node.source_ids):
        raise RaptorIntegrityError("node sources must be unique")
    expected_id = _node_id(
        data_version=node.data_version,
        level=node.level,
        child_ids=node.child_ids,
        text=node.text,
    )
    if node.node_id != expected_id:
        raise RaptorIntegrityError(f"node id mismatch: {node.node_id}")
    if node.level == 0:
        if node.child_ids:
            raise RaptorIntegrityError("leaf children must be empty")
        if len(node.source_ids) != 1 or not node.locator:
            raise RaptorIntegrityError("leaf source and locator are required")
        return
    if not node.child_ids:
        raise RaptorIntegrityError("summary children must not be empty")
    if not node.source_ids:
        raise RaptorIntegrityError("summary sources must not be empty")
    if node.locator is not None:
        raise RaptorIntegrityError("summary locator must be empty")


def _require_node(node_store: NodeStorePort, node_id: str) -> RaptorNode:
    node = node_store.get(node_id)
    if node is None:
        raise RaptorIntegrityError(f"missing node: {node_id}")
    return node


def _descend(
    *,
    node: RaptorNode,
    root: RaptorNode,
    root_score: float,
    node_store: NodeStorePort,
    node_ids: tuple[str, ...],
    seen_leaves: set[str],
    paths: list[RaptorPath],
) -> None:
    _validate_node_fields(node)
    if node.level == 0:
        if node.node_id in seen_leaves:
            return
        seen_leaves.add(node.node_id)
        paths.append(
            RaptorPath(
                root_node_id=root.node_id,
                root_level=root.level,
                root_score=root_score,
                node_ids=node_ids,
                source_id=node.source_ids[0],
                leaf_text=node.text,
                leaf_locator=node.locator or "",
                root_text=root.text,
            )
        )
        return

    children = [_require_node(node_store, child_id) for child_id in node.child_ids]
    child_sources = tuple(
        sorted({source_id for child in children for source_id in child.source_ids})
    )
    if child_sources != node.source_ids:
        raise RaptorIntegrityError(f"source mismatch for node: {node.node_id}")

    for child in children:
        if child.level != node.level - 1:
            raise RaptorIntegrityError(f"child level mismatch: {child.node_id}")
        if (
            child.document_id != node.document_id
            or child.data_version != node.data_version
        ):
            raise RaptorIntegrityError(f"child lineage mismatch: {child.node_id}")
        _descend(
            node=child,
            root=root,
            root_score=root_score,
            node_store=node_store,
            node_ids=(*node_ids, child.node_id),
            seen_leaves=seen_leaves,
            paths=paths,
        )
