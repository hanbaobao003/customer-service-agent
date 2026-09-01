"""Deterministic RAPTOR tree contracts."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from customer_service_agent.retrieval.models import RetrievalArtifact, RetrievalHit


class RaptorIntegrityError(ValueError):
    pass


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
    async def summarize(self, texts: Sequence[str], *, level: int) -> str: ...


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
