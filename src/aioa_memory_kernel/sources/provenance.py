"""Bounded deterministic provenance DAG operations."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .errors import (
    ProvenanceConflictError,
    ProvenanceCycleError,
    SourceRegistryValidationError,
)
from .models import ProvenanceEdge


MAX_PROVENANCE_NODES = 1024


class ProvenanceGraph:
    """A source-scoped graph with idempotent immutable edge identities."""

    def __init__(self, edges: Iterable[ProvenanceEdge] = ()) -> None:
        self._edges: dict[str, ProvenanceEdge] = {}
        self._scope: tuple[str, str, str] | None = None
        for edge in sorted(edges, key=lambda item: (item.created_at, item.edge_id)):
            self.add_edge(edge)

    @property
    def edges(self) -> tuple[ProvenanceEdge, ...]:
        return tuple(
            sorted(
                self._edges.values(),
                key=lambda edge: (edge.created_at, edge.edge_id),
            )
        )

    @property
    def scope(self) -> tuple[str, str, str] | None:
        """Return the immutable tenant/source/HAT scope of a nonempty graph."""

        return self._scope

    def add_edge(self, edge: ProvenanceEdge) -> ProvenanceEdge:
        if not isinstance(edge, ProvenanceEdge):
            raise SourceRegistryValidationError(
                "provenance edge has the wrong type",
                sanitized_code="INVALID_PROVENANCE_EDGE",
            )
        scope = (edge.tenant_id, edge.source_id, edge.hat_scope_id)
        if self._scope is None:
            self._scope = scope
        elif self._scope != scope:
            raise SourceRegistryValidationError(
                "one provenance graph cannot mix source scopes",
                sanitized_code="PROVENANCE_SCOPE_MISMATCH",
            )
        existing = self._edges.get(edge.edge_id)
        if existing is not None:
            if existing == edge:
                return existing
            raise ProvenanceConflictError(
                "edge identity is bound to different canonical facts",
                sanitized_code="PROVENANCE_EDGE_CONFLICT",
            )
        if any(
            current.edge_digest == edge.edge_digest and current != edge
            for current in self._edges.values()
        ):
            raise ProvenanceConflictError(
                "edge digest is bound to different canonical facts",
                sanitized_code="PROVENANCE_DIGEST_CONFLICT",
            )
        nodes = {
            digest
            for current in (*self._edges.values(), edge)
            for digest in (
                current.parent_artifact_digest,
                current.child_artifact_digest,
            )
        }
        if len(nodes) > MAX_PROVENANCE_NODES:
            raise SourceRegistryValidationError(
                "provenance graph exceeds its bounded node limit",
                sanitized_code="PROVENANCE_GRAPH_TOO_LARGE",
            )
        if self._path_exists(
            edge.child_artifact_digest,
            edge.parent_artifact_digest,
        ):
            raise ProvenanceCycleError(
                "provenance edge would create a cycle",
                sanitized_code="PROVENANCE_CYCLE",
            )
        self._edges[edge.edge_id] = edge
        return edge

    def _adjacency(self) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self._edges.values():
            adjacency[edge.parent_artifact_digest].add(
                edge.child_artifact_digest
            )
        return adjacency

    def _path_exists(self, start: str, target: str) -> bool:
        adjacency = self._adjacency()
        queue: deque[str] = deque([start])
        seen: set[str] = set()
        while queue:
            node = queue.popleft()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            if len(seen) > MAX_PROVENANCE_NODES:
                raise SourceRegistryValidationError(
                    "provenance traversal exceeded its bounded node limit",
                    sanitized_code="PROVENANCE_GRAPH_TOO_LARGE",
                )
            queue.extend(sorted(adjacency.get(node, ())))
        return False

    def parent_digests(self, child_digest: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    edge.parent_artifact_digest
                    for edge in self._edges.values()
                    if edge.child_artifact_digest == child_digest
                }
            )
        )

    def root_digests(self, terminal_digest: str) -> tuple[str, ...]:
        """Return deterministic roots reachable backward from a terminal."""

        parents: dict[str, set[str]] = defaultdict(set)
        for edge in self._edges.values():
            parents[edge.child_artifact_digest].add(
                edge.parent_artifact_digest
            )
        roots: set[str] = set()
        queue: deque[str] = deque([terminal_digest])
        seen: set[str] = set()
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            direct = parents.get(node, set())
            if not direct:
                roots.add(node)
            else:
                queue.extend(sorted(direct))
            if len(seen) > MAX_PROVENANCE_NODES:
                raise SourceRegistryValidationError(
                    "provenance traversal exceeded its bounded node limit",
                    sanitized_code="PROVENANCE_GRAPH_TOO_LARGE",
                )
        return tuple(sorted(roots))

    def lineage_edges(self, terminal_digest: str) -> tuple[ProvenanceEdge, ...]:
        """Return every edge in the bounded ancestry of one terminal."""

        parents: dict[str, list[ProvenanceEdge]] = defaultdict(list)
        for edge in self._edges.values():
            parents[edge.child_artifact_digest].append(edge)
        selected: dict[str, ProvenanceEdge] = {}
        queue: deque[str] = deque([terminal_digest])
        seen: set[str] = set()
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            for edge in sorted(
                parents.get(node, ()),
                key=lambda item: (item.edge_digest, item.edge_id),
            ):
                selected[edge.edge_digest] = edge
                queue.append(edge.parent_artifact_digest)
            if len(seen) > MAX_PROVENANCE_NODES:
                raise SourceRegistryValidationError(
                    "provenance traversal exceeded its bounded node limit",
                    sanitized_code="PROVENANCE_GRAPH_TOO_LARGE",
                )
        return tuple(
            sorted(
                selected.values(),
                key=lambda edge: (edge.edge_digest, edge.edge_id),
            )
        )

    def lineage_digest(self, terminal_digest: str) -> str:
        """Bind every reachable ancestry edge and root deterministically."""

        roots = self.root_digests(terminal_digest)
        edges = self.lineage_edges(terminal_digest)
        return canonical_sha256(
            {
                "contract_type": "SOURCE_PROVENANCE_LINEAGE",
                "contract_version": "1.0.0",
                "edge_digests": tuple(edge.edge_digest for edge in edges),
                "root_digests": roots,
                "terminal_digest": terminal_digest,
            }
        )
