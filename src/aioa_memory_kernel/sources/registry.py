"""Retry-safe CockroachDB source-registry service and repository."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (
    BeginOperation,
    IdempotencyService,
    OperationStatus,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .eligibility import evaluate_publication_eligibility
from .errors import (
    ProvenanceConflictError,
    PublicationEligibilityError,
    PublicationTransitionError,
    SourceRegistryConflictError,
    SourceRegistryValidationError,
)
from .models import (
    OriginMetadata,
    PUBLICATION_GENESIS_DIGEST,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    ProvenanceEdge,
    PublicationEligibilityDecision,
    PublicationStateEvent,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourceLicenseAssessment,
    SourceLicenseStatus,
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryActorType,
    SourceRegistryRecord,
    SourceScopeDimensions,
    TransformationIdentity,
    utc_now,
)
from .provenance import ProvenanceGraph
from .protocols import SourceRegistryRepositoryProtocol
from .states import build_publication_event, verify_publication_event_chain


REGISTRY_COLUMNS = """
tenant_id, source_id, hat_scope_id, schema_version, source_kind,
source_reference, target_scope, owner_user_id, personal_memory_space_id,
authority_level, authority_basis, license_status, license_identifier,
license_reference, access_class, redaction_state, scope_dimensions,
scope_digest, parser_name, parser_version, parser_contract_version,
transformation_name, transformation_version,
transformation_contract_version, origin_kind, origin_system,
origin_version, adapter_version, external_ref, observed_at, artifact_kind,
artifact_digest, artifact_byte_length, artifact_media_type,
artifact_created_at, exact_source_bytes, model_generated, snapshot_id,
knowledge_version_id, current_publication_state,
current_publication_sequence, current_publication_event_digest,
registry_digest, created_at, updated_at
""".replace("\n", " ")

EDGE_COLUMNS = """
tenant_id, source_id, hat_scope_id, edge_id, parent_artifact_digest,
child_artifact_digest, edge_kind, parser_name, parser_version,
parser_contract_version, transformation_name, transformation_version,
transformation_contract_version, metadata, edge_digest, created_at
""".replace("\n", " ")

EVENT_COLUMNS = """
tenant_id, source_id, hat_scope_id, event_id, sequence_number, from_state,
to_state, actor_type, actor_reference, policy_version,
eligibility_decision_digest, reason_codes, reviewer_reference,
previous_event_digest, event_digest, created_at
""".replace("\n", " ")


def _json(value: Any) -> str:
    return canonical_json(value)


def _json_value(value: object, expected: type, field: str) -> Any:
    if isinstance(value, expected):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SourceRegistryValidationError(
                f"database returned invalid {field}",
                sanitized_code="INVALID_SOURCE_DATABASE_ROW",
            ) from exc
        if isinstance(parsed, expected):
            return parsed
    raise SourceRegistryValidationError(
        f"database returned invalid {field}",
        sanitized_code="INVALID_SOURCE_DATABASE_ROW",
    )


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SourceRegistryValidationError(
                f"database returned invalid {field}",
                sanitized_code="INVALID_SOURCE_DATABASE_ROW",
            ) from exc
    raise SourceRegistryValidationError(
        f"database returned invalid {field}",
        sanitized_code="INVALID_SOURCE_DATABASE_ROW",
    )


def _optional_timestamp(value: object | None, field: str) -> datetime | None:
    return None if value is None else _timestamp(value, field)


def _bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in {"t", "true", "TRUE", "1", 1}:
        return True
    if value in {"f", "false", "FALSE", "0", 0}:
        return False
    raise SourceRegistryValidationError(
        f"database returned invalid {field}",
        sanitized_code="INVALID_SOURCE_DATABASE_ROW",
    )


def _require_registration_genesis(record: SourceRegistryRecord) -> None:
    if (
        record.current_publication_state is not SourcePublicationState.REGISTERED
        or record.current_publication_sequence != 0
        or record.current_publication_event_digest != PUBLICATION_GENESIS_DIGEST
    ):
        raise SourceRegistryValidationError(
            "new source registration must start at the exact publication genesis",
            sanitized_code="INVALID_PUBLICATION_GENESIS",
        )


def registry_from_row(row: Mapping[str, object]) -> SourceRegistryRecord:
    scope_data = _json_value(row["scope_dimensions"], dict, "scope_dimensions")
    origin = OriginMetadata(
        origin_kind=str(row["origin_kind"]),
        origin_system=str(row["origin_system"]),
        origin_version=str(row["origin_version"]),
        adapter_version=str(row["adapter_version"]),
        external_ref=(
            None if row.get("external_ref") is None else str(row["external_ref"])
        ),
        observed_at=_optional_timestamp(row.get("observed_at"), "observed_at"),
    )
    parser = ParserIdentity(
        str(row["parser_name"]),
        str(row["parser_version"]),
        str(row["parser_contract_version"]),
    )
    transformation = TransformationIdentity(
        str(row["transformation_name"]),
        str(row["transformation_version"]),
        str(row["transformation_contract_version"]),
    )
    scope = SourceScopeDimensions(
        tenant_id=str(row["tenant_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        target_scope=MemoryTargetScope(str(row["target_scope"])),
        owner_user_id=(
            None if row.get("owner_user_id") is None else str(row["owner_user_id"])
        ),
        personal_memory_space_id=(
            None
            if row.get("personal_memory_space_id") is None
            else str(row["personal_memory_space_id"])
        ),
        domain=scope_data.get("domain"),
        jurisdiction=scope_data.get("jurisdiction"),
        language=scope_data.get("language"),
        temporal_policy_reference=scope_data.get("temporal_policy_reference"),
        source_collection=tuple(scope_data.get("source_collection", ())),
        additional_dimensions=scope_data.get("additional_dimensions", {}),
        scope_digest=str(row["scope_digest"]),
    )
    artifact = ProvenanceArtifactIdentity(
        artifact_kind=str(row["artifact_kind"]),
        artifact_digest=str(row["artifact_digest"]),
        byte_length=(
            None
            if row.get("artifact_byte_length") is None
            else int(row["artifact_byte_length"])
        ),
        media_type=(
            None
            if row.get("artifact_media_type") is None
            else str(row["artifact_media_type"])
        ),
        origin=origin,
        parser=parser,
        transformation=transformation,
        created_at=_timestamp(row["artifact_created_at"], "artifact_created_at"),
        exact_source_bytes=_bool(row["exact_source_bytes"], "exact_source_bytes"),
        model_generated=_bool(row["model_generated"], "model_generated"),
    )
    return SourceRegistryRecord(
        tenant_id=str(row["tenant_id"]),
        source_id=str(row["source_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        source_kind=str(row["source_kind"]),
        source_reference=str(row["source_reference"]),
        scope=scope,
        authority=SourceAuthorityAssessment(
            SourceAuthorityLevel(str(row["authority_level"])),
            _json_value(row["authority_basis"], dict, "authority_basis"),
        ),
        license=SourceLicenseAssessment(
            SourceLicenseStatus(str(row["license_status"])),
            None
            if row.get("license_identifier") is None
            else str(row["license_identifier"]),
            None
            if row.get("license_reference") is None
            else str(row["license_reference"]),
        ),
        access_class=SourceAccessClass(str(row["access_class"])),
        redaction_state=RedactionState(str(row["redaction_state"])),
        parser=parser,
        transformation=transformation,
        origin=origin,
        artifact=artifact,
        snapshot_id=(
            None if row.get("snapshot_id") is None else str(row["snapshot_id"])
        ),
        knowledge_version_id=(
            None
            if row.get("knowledge_version_id") is None
            else str(row["knowledge_version_id"])
        ),
        current_publication_state=SourcePublicationState(
            str(row["current_publication_state"])
        ),
        current_publication_sequence=int(row["current_publication_sequence"]),
        current_publication_event_digest=str(
            row["current_publication_event_digest"]
        ),
        created_at=_timestamp(row["created_at"], "created_at"),
        updated_at=_timestamp(row["updated_at"], "updated_at"),
        registry_digest=str(row["registry_digest"]),
        schema_version=str(row["schema_version"]),
    )


def edge_from_row(row: Mapping[str, object]) -> ProvenanceEdge:
    return ProvenanceEdge(
        tenant_id=str(row["tenant_id"]),
        source_id=str(row["source_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        edge_id=str(row["edge_id"]),
        parent_artifact_digest=str(row["parent_artifact_digest"]),
        child_artifact_digest=str(row["child_artifact_digest"]),
        edge_kind=str(row["edge_kind"]),
        parser=ParserIdentity(
            str(row["parser_name"]),
            str(row["parser_version"]),
            str(row["parser_contract_version"]),
        ),
        transformation=TransformationIdentity(
            str(row["transformation_name"]),
            str(row["transformation_version"]),
            str(row["transformation_contract_version"]),
        ),
        metadata=_json_value(row["metadata"], dict, "edge metadata"),
        edge_digest=str(row["edge_digest"]),
        created_at=_timestamp(row["created_at"], "created_at"),
    )


def event_from_row(row: Mapping[str, object]) -> PublicationStateEvent:
    reasons = _json_value(row["reason_codes"], list, "reason_codes")
    return PublicationStateEvent(
        tenant_id=str(row["tenant_id"]),
        source_id=str(row["source_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        event_id=str(row["event_id"]),
        sequence_number=int(row["sequence_number"]),
        from_state=SourcePublicationState(str(row["from_state"])),
        to_state=SourcePublicationState(str(row["to_state"])),
        actor_type=SourceRegistryActorType(str(row["actor_type"])),
        actor_reference=str(row["actor_reference"]),
        policy_version=str(row["policy_version"]),
        eligibility_decision_digest=str(row["eligibility_decision_digest"]),
        reason_codes=tuple(str(value) for value in reasons),
        reviewer_reference=(
            None
            if row.get("reviewer_reference") is None
            else str(row["reviewer_reference"])
        ),
        previous_event_digest=str(row["previous_event_digest"]),
        event_digest=str(row["event_digest"]),
        created_at=_timestamp(row["created_at"], "created_at"),
    )


class CockroachSourceRegistryRepository:
    """Typed SQL behind the existing bounded transaction facade."""

    def source_scope_exists(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        source_kind: str,
        source_reference: str,
    ) -> bool:
        row = transaction.fetch_one(
            """
            SELECT 1 AS found
              FROM memory_patch.knowledge_sources AS source
              JOIN memory_patch.hat_scopes AS scope
                ON scope.tenant_id = source.tenant_id
               AND scope.hat_scope_id = source.hat_scope_id
             WHERE source.tenant_id = %s
               AND source.source_id = %s
               AND source.hat_scope_id = %s
               AND source.source_kind = %s
               AND source.source_reference = %s
            """,
            (
                tenant_id,
                source_id,
                hat_scope_id,
                source_kind,
                source_reference,
            ),
        )
        return row is not None

    def get_registry(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> SourceRegistryRecord | None:
        row = transaction.fetch_one(
            f"""
            SELECT {REGISTRY_COLUMNS}
              FROM memory_patch.source_registry_entries
             WHERE tenant_id = %s
               AND source_id = %s
               AND hat_scope_id = %s
            """,
            (tenant_id, source_id, hat_scope_id),
        )
        return None if row is None else registry_from_row(row)

    def put_registry(
        self,
        transaction: TransactionProtocol,
        record: SourceRegistryRecord,
    ) -> SourceRegistryRecord:
        _require_registration_genesis(record)
        existing = self.get_registry(
            transaction,
            record.tenant_id,
            record.source_id,
            record.hat_scope_id,
        )
        if existing is not None:
            if existing == record:
                return existing
            raise SourceRegistryConflictError(
                "registry identity is bound to different canonical facts",
                sanitized_code="SOURCE_REGISTRY_IMMUTABLE_CONFLICT",
            )
        scope_data = {
            "additional_dimensions": record.scope.additional_dimensions,
            "domain": record.scope.domain,
            "jurisdiction": record.scope.jurisdiction,
            "language": record.scope.language,
            "source_collection": record.scope.source_collection,
            "temporal_policy_reference": record.scope.temporal_policy_reference,
        }
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.source_registry_entries (
              tenant_id, source_id, hat_scope_id, schema_version, source_kind,
              source_reference, target_scope, owner_user_id,
              personal_memory_space_id, authority_level, authority_basis,
              license_status, license_identifier, license_reference,
              access_class, redaction_state, scope_dimensions, scope_digest,
              parser_name, parser_version, parser_contract_version,
              transformation_name, transformation_version,
              transformation_contract_version, origin_kind, origin_system,
              origin_version, adapter_version, external_ref, observed_at,
              artifact_kind, artifact_digest, artifact_byte_length,
              artifact_media_type, artifact_created_at, exact_source_bytes,
              model_generated, snapshot_id, knowledge_version_id,
              current_publication_state, current_publication_sequence,
              current_publication_event_digest, registry_digest, created_at,
              updated_at
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB,
              %s, %s, %s, %s, %s, %s::JSONB, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {REGISTRY_COLUMNS}
            """,
            (
                record.tenant_id,
                record.source_id,
                record.hat_scope_id,
                record.schema_version,
                record.source_kind,
                record.source_reference,
                record.scope.target_scope.value,
                record.scope.owner_user_id,
                record.scope.personal_memory_space_id,
                record.authority.authority_level.value,
                _json(record.authority.authority_basis),
                record.license.license_status.value,
                record.license.license_identifier,
                record.license.license_reference,
                record.access_class.value,
                record.redaction_state.value,
                _json(scope_data),
                record.scope.scope_digest,
                record.parser.parser_name,
                record.parser.parser_version,
                record.parser.parser_contract_version,
                record.transformation.transformation_name,
                record.transformation.transformation_version,
                record.transformation.transformation_contract_version,
                record.origin.origin_kind,
                record.origin.origin_system,
                record.origin.origin_version,
                record.origin.adapter_version,
                record.origin.external_ref,
                record.origin.observed_at,
                record.artifact.artifact_kind,
                record.artifact.artifact_digest,
                record.artifact.byte_length,
                record.artifact.media_type,
                record.artifact.created_at,
                record.artifact.exact_source_bytes,
                record.artifact.model_generated,
                record.snapshot_id,
                record.knowledge_version_id,
                record.current_publication_state.value,
                record.current_publication_sequence,
                record.current_publication_event_digest,
                record.registry_digest,
                record.created_at,
                record.updated_at,
            ),
        )
        if row is not None:
            inserted = registry_from_row(row)
            if inserted == record:
                return inserted
        raced = self.get_registry(
            transaction,
            record.tenant_id,
            record.source_id,
            record.hat_scope_id,
        )
        if raced == record:
            return raced
        raise SourceRegistryConflictError(
            "registry identity collided with different canonical facts",
            sanitized_code="SOURCE_REGISTRY_IMMUTABLE_CONFLICT",
        )

    def list_edges(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> tuple[ProvenanceEdge, ...]:
        rows = transaction.fetch_all(
            f"""
            SELECT {EDGE_COLUMNS}
              FROM memory_patch.source_provenance_edges
             WHERE tenant_id = %s
               AND source_id = %s
               AND hat_scope_id = %s
             ORDER BY created_at, edge_id
            """,
            (tenant_id, source_id, hat_scope_id),
        )
        return tuple(edge_from_row(row) for row in rows)

    def put_edge(
        self,
        transaction: TransactionProtocol,
        edge: ProvenanceEdge,
    ) -> ProvenanceEdge:
        graph = ProvenanceGraph(
            self.list_edges(
                transaction,
                edge.tenant_id,
                edge.source_id,
                edge.hat_scope_id,
            )
        )
        accepted = graph.add_edge(edge)
        if accepted is not edge:
            return accepted
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.source_provenance_edges (
              tenant_id, source_id, hat_scope_id, edge_id,
              parent_artifact_digest, child_artifact_digest, edge_kind,
              parser_name, parser_version, parser_contract_version,
              transformation_name, transformation_version,
              transformation_contract_version, metadata, edge_digest,
              created_at
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s::JSONB, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {EDGE_COLUMNS}
            """,
            (
                edge.tenant_id,
                edge.source_id,
                edge.hat_scope_id,
                edge.edge_id,
                edge.parent_artifact_digest,
                edge.child_artifact_digest,
                edge.edge_kind,
                edge.parser.parser_name,
                edge.parser.parser_version,
                edge.parser.parser_contract_version,
                edge.transformation.transformation_name,
                edge.transformation.transformation_version,
                edge.transformation.transformation_contract_version,
                _json(edge.metadata),
                edge.edge_digest,
                edge.created_at,
            ),
        )
        if row is not None:
            inserted = edge_from_row(row)
            if inserted == edge:
                return inserted
        for current in self.list_edges(
            transaction,
            edge.tenant_id,
            edge.source_id,
            edge.hat_scope_id,
        ):
            if current.edge_id == edge.edge_id and current == edge:
                return current
        raise ProvenanceConflictError(
            "provenance edge collided with different canonical facts",
            sanitized_code="PROVENANCE_EDGE_CONFLICT",
        )

    def get_event(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        event_id: str,
    ) -> PublicationStateEvent | None:
        row = transaction.fetch_one(
            f"""
            SELECT {EVENT_COLUMNS}
              FROM memory_patch.source_publication_events
             WHERE tenant_id = %s
               AND source_id = %s
               AND hat_scope_id = %s
               AND event_id = %s
            """,
            (tenant_id, source_id, hat_scope_id, event_id),
        )
        return None if row is None else event_from_row(row)

    def list_events(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> tuple[PublicationStateEvent, ...]:
        rows = transaction.fetch_all(
            f"""
            SELECT {EVENT_COLUMNS}
              FROM memory_patch.source_publication_events
             WHERE tenant_id = %s
               AND source_id = %s
               AND hat_scope_id = %s
             ORDER BY sequence_number
            """,
            (tenant_id, source_id, hat_scope_id),
        )
        return tuple(event_from_row(row) for row in rows)

    def append_event(
        self,
        transaction: TransactionProtocol,
        event: PublicationStateEvent,
    ) -> PublicationStateEvent:
        existing = self.get_event(
            transaction,
            event.tenant_id,
            event.source_id,
            event.hat_scope_id,
            event.event_id,
        )
        if existing is not None:
            if existing == event:
                return existing
            raise SourceRegistryConflictError(
                "publication event identity is bound to different facts",
                sanitized_code="PUBLICATION_EVENT_CONFLICT",
            )
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.source_publication_events (
              tenant_id, source_id, hat_scope_id, event_id, sequence_number,
              from_state, to_state, actor_type, actor_reference, policy_version,
              eligibility_decision_digest, reason_codes, reviewer_reference,
              previous_event_digest, event_digest, created_at
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB,
              %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {EVENT_COLUMNS}
            """,
            (
                event.tenant_id,
                event.source_id,
                event.hat_scope_id,
                event.event_id,
                event.sequence_number,
                event.from_state.value,
                event.to_state.value,
                event.actor_type.value,
                event.actor_reference,
                event.policy_version,
                event.eligibility_decision_digest,
                _json(event.reason_codes),
                event.reviewer_reference,
                event.previous_event_digest,
                event.event_digest,
                event.created_at,
            ),
        )
        if row is not None:
            inserted = event_from_row(row)
            if inserted == event:
                return inserted
        raced = self.get_event(
            transaction,
            event.tenant_id,
            event.source_id,
            event.hat_scope_id,
            event.event_id,
        )
        if raced == event:
            return raced
        raise SourceRegistryConflictError(
            "publication event collided with different canonical facts",
            sanitized_code="PUBLICATION_EVENT_CONFLICT",
        )

    def compare_and_set_publication(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        expected_state: SourcePublicationState,
        expected_sequence: int,
        expected_event_digest: str,
        event: PublicationStateEvent,
    ) -> SourceRegistryRecord:
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.source_registry_entries
               SET current_publication_state = %s,
                   current_publication_sequence = %s,
                   current_publication_event_digest = %s,
                   updated_at = %s
             WHERE tenant_id = %s
               AND source_id = %s
               AND hat_scope_id = %s
               AND current_publication_state = %s
               AND current_publication_sequence = %s
               AND current_publication_event_digest = %s
            RETURNING {REGISTRY_COLUMNS}
            """,
            (
                event.to_state.value,
                event.sequence_number,
                event.event_digest,
                event.created_at,
                tenant_id,
                source_id,
                hat_scope_id,
                expected_state.value,
                expected_sequence,
                expected_event_digest,
            ),
        )
        if row is None:
            raise PublicationTransitionError(
                "publication compare-and-set observed stale state",
                sanitized_code="STALE_PUBLICATION_STATE",
            )
        updated = registry_from_row(row)
        if (
            updated.current_publication_state is not event.to_state
            or updated.current_publication_sequence != event.sequence_number
            or updated.current_publication_event_digest != event.event_digest
        ):
            raise PublicationTransitionError(
                "publication compare-and-set returned inconsistent state",
                sanitized_code="PUBLICATION_TERMINAL_MISMATCH",
            )
        return updated


class SourceRegistryService:
    """Trusted application boundary; models and HATs never receive this object."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        *,
        repository: SourceRegistryRepositoryProtocol | None = None,
        idempotency: IdempotencyService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise SourceRegistryValidationError(
                "source registry requires the Step 6 transaction runner",
                sanitized_code="INVALID_SOURCE_TRANSACTION_RUNNER",
            )
        self._runner = runner
        self._repository = repository or CockroachSourceRegistryRepository()
        self._clock = clock
        self._idempotency = idempotency or IdempotencyService(clock=clock)

    @staticmethod
    def _require_context(
        context: RequestContext,
        record: SourceRegistryRecord,
    ) -> None:
        if context.tenant_id != record.tenant_id:
            raise SourceRegistryValidationError(
                "source operation crosses tenant context",
                sanitized_code="SOURCE_CONTEXT_MISMATCH",
            )
        if record.access_class is SourceAccessClass.USER_PRIVATE:
            if context.user_id != record.scope.owner_user_id:
                raise SourceRegistryValidationError(
                    "source operation crosses user context",
                    sanitized_code="SOURCE_CONTEXT_MISMATCH",
                )
        elif context.user_id is not None:
            raise SourceRegistryValidationError(
                "shared source requires tenant-shared context",
                sanitized_code="SOURCE_CONTEXT_MISMATCH",
            )

    def register_source(
        self,
        context: RequestContext,
        record: SourceRegistryRecord,
        *,
        operation_id: str,
        idempotency_key: str,
    ) -> SourceRegistryRecord:
        self._require_context(context, record)
        _require_registration_genesis(record)
        request = BeginOperation(
            operation_id=operation_id,
            tenant_id=record.tenant_id,
            owner_user_id=record.scope.owner_user_id,
            operation_kind="SOURCE_REGISTER",
            idempotency_key=idempotency_key,
            request_digest=record.registry_digest,
            scope_digest=record.scope.scope_digest,
            created_at=record.created_at,
        )

        def operation(transaction: TransactionProtocol) -> SourceRegistryRecord:
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                request,
            )
            if not claim.may_proceed:
                existing = self._repository.get_registry(
                    transaction,
                    record.tenant_id,
                    record.source_id,
                    record.hat_scope_id,
                )
                if (
                    claim.operation.status is OperationStatus.COMPLETED
                    and existing is not None
                    and existing.registry_digest == record.registry_digest
                ):
                    return existing
                raise SourceRegistryConflictError(
                    "registration operation is not a completed exact replay",
                    sanitized_code="SOURCE_REGISTRATION_REPLAY_CONFLICT",
                )
            if not self._repository.source_scope_exists(
                transaction,
                record.tenant_id,
                record.source_id,
                record.hat_scope_id,
                record.source_kind,
                record.source_reference,
            ):
                raise SourceRegistryValidationError(
                    "source and HAT scope must exist before registration",
                    sanitized_code="SOURCE_SCOPE_NOT_FOUND",
                )
            stored = self._repository.put_registry(transaction, record)
            self._idempotency.complete_operation(
                transaction,
                tenant_id=record.tenant_id,
                operation_id=claim.operation.operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=record.source_id,
                result_digest=stored.registry_digest,
            )
            return stored

        return self._runner.run(
            context,
            operation,
            operation_kind="SOURCE_REGISTER",
        )

    def get_source_registry(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> SourceRegistryRecord | None:
        return self._runner.run(
            context,
            lambda transaction: self._repository.get_registry(
                transaction,
                tenant_id,
                source_id,
                hat_scope_id,
            ),
            operation_kind="SOURCE_REGISTRY_READ",
        )

    def record_provenance_edge(
        self,
        context: RequestContext,
        edge: ProvenanceEdge,
        *,
        operation_id: str,
        idempotency_key: str,
    ) -> ProvenanceEdge:
        def operation(transaction: TransactionProtocol) -> ProvenanceEdge:
            record = self._repository.get_registry(
                transaction,
                edge.tenant_id,
                edge.source_id,
                edge.hat_scope_id,
            )
            if record is None:
                raise SourceRegistryValidationError(
                    "registry entry must exist before provenance",
                    sanitized_code="SOURCE_REGISTRY_NOT_FOUND",
                )
            self._require_context(context, record)
            request = BeginOperation(
                operation_id=operation_id,
                tenant_id=edge.tenant_id,
                owner_user_id=record.scope.owner_user_id,
                operation_kind="PROVENANCE_EDGE_APPEND",
                idempotency_key=idempotency_key,
                request_digest=edge.edge_digest,
                scope_digest=record.scope.scope_digest,
                created_at=edge.created_at,
            )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                request,
            )
            if not claim.may_proceed:
                for existing in self._repository.list_edges(
                    transaction,
                    edge.tenant_id,
                    edge.source_id,
                    edge.hat_scope_id,
                ):
                    if (
                        claim.operation.status is OperationStatus.COMPLETED
                        and existing.edge_id == edge.edge_id
                        and existing == edge
                    ):
                        return existing
                raise ProvenanceConflictError(
                    "provenance operation is not a completed exact replay",
                    sanitized_code="PROVENANCE_REPLAY_CONFLICT",
                )
            stored = self._repository.put_edge(transaction, edge)
            self._idempotency.complete_operation(
                transaction,
                tenant_id=edge.tenant_id,
                operation_id=claim.operation.operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=edge.edge_id,
                result_digest=stored.edge_digest,
            )
            return stored

        return self._runner.run(
            context,
            operation,
            operation_kind="PROVENANCE_EDGE_APPEND",
        )

    def evaluate_publication_eligibility(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        evaluated_at: datetime | None = None,
    ) -> PublicationEligibilityDecision:
        def operation(
            transaction: TransactionProtocol,
        ) -> PublicationEligibilityDecision:
            record = self._repository.get_registry(
                transaction,
                tenant_id,
                source_id,
                hat_scope_id,
            )
            if record is None:
                raise SourceRegistryValidationError(
                    "source registry entry was not found",
                    sanitized_code="SOURCE_REGISTRY_NOT_FOUND",
                )
            self._require_context(context, record)
            graph = ProvenanceGraph(
                self._repository.list_edges(
                    transaction,
                    tenant_id,
                    source_id,
                    hat_scope_id,
                )
            )
            return evaluate_publication_eligibility(
                record,
                graph,
                evaluated_at=evaluated_at or self._clock(),
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="PUBLICATION_ELIGIBILITY_EVALUATE",
        )

    def transition_publication_state(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        expected_state: SourcePublicationState,
        expected_registry_digest: str,
        expected_scope_digest: str,
        target_state: SourcePublicationState,
        event_id: str,
        operation_id: str,
        idempotency_key: str,
        reason_codes: tuple[str, ...],
        actor: SourceRegistryActor,
        reviewer_reference: str | None = None,
        created_at: datetime | None = None,
    ) -> PublicationStateEvent:
        if not isinstance(actor, SourceRegistryActor):
            raise SourceRegistryValidationError(
                "publication transition requires a trusted typed actor",
                sanitized_code="UNTRUSTED_PUBLICATION_ACTOR",
            )
        timestamp = created_at or self._clock()
        request_facts = {
            "actor_type": actor.actor_type,
            "actor_reference": actor.actor_reference,
            "event_id": event_id,
            "expected_registry_digest": expected_registry_digest,
            "expected_scope_digest": expected_scope_digest,
            "expected_state": expected_state,
            "hat_scope_id": hat_scope_id,
            "reason_codes": tuple(sorted(reason_codes)),
            "reviewer_reference": reviewer_reference,
            "source_id": source_id,
            "target_state": target_state,
            "tenant_id": tenant_id,
        }
        if created_at is not None:
            request_facts["requested_created_at"] = created_at
        request_digest = canonical_sha256(request_facts)

        def operation(transaction: TransactionProtocol) -> PublicationStateEvent:
            record = self._repository.get_registry(
                transaction,
                tenant_id,
                source_id,
                hat_scope_id,
            )
            if record is None:
                raise SourceRegistryValidationError(
                    "source registry entry was not found",
                    sanitized_code="SOURCE_REGISTRY_NOT_FOUND",
                )
            self._require_context(context, record)
            if (
                record.registry_digest != expected_registry_digest
                or record.scope.scope_digest != expected_scope_digest
            ):
                raise PublicationTransitionError(
                    "publication request observed different registry facts",
                    sanitized_code="STALE_PUBLICATION_INPUT",
                )
            request = BeginOperation(
                operation_id=operation_id,
                tenant_id=tenant_id,
                owner_user_id=record.scope.owner_user_id,
                operation_kind="PUBLICATION_STATE_TRANSITION",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                scope_digest=record.scope.scope_digest,
                created_at=timestamp,
            )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                request,
            )
            if not claim.may_proceed:
                existing = self._repository.get_event(
                    transaction,
                    tenant_id,
                    source_id,
                    hat_scope_id,
                    event_id,
                )
                if (
                    claim.operation.status is OperationStatus.COMPLETED
                    and existing is not None
                    and existing.event_digest == claim.operation.result_digest
                ):
                    return existing
                raise PublicationTransitionError(
                    "transition operation is not a completed exact replay",
                    sanitized_code="PUBLICATION_REPLAY_CONFLICT",
                )
            if record.current_publication_state is not expected_state:
                raise PublicationTransitionError(
                    "publication request observed stale state",
                    sanitized_code="STALE_PUBLICATION_STATE",
                )
            graph = ProvenanceGraph(
                self._repository.list_edges(
                    transaction,
                    tenant_id,
                    source_id,
                    hat_scope_id,
                )
            )
            decision = evaluate_publication_eligibility(
                record,
                graph,
                evaluated_at=timestamp,
            )
            if target_state in {
                SourcePublicationState.ELIGIBLE,
                SourcePublicationState.PUBLISHED,
            } and not decision.eligible:
                raise PublicationEligibilityError(
                    "target publication state requires a fresh eligible decision",
                    sanitized_code="PUBLICATION_NOT_ELIGIBLE",
                )
            event = build_publication_event(
                record,
                event_id=event_id,
                target_state=target_state,
                eligibility=decision,
                actor=actor,
                reason_codes=reason_codes,
                reviewer_reference=reviewer_reference,
                created_at=timestamp,
            )
            stored = self._repository.append_event(transaction, event)
            self._repository.compare_and_set_publication(
                transaction,
                tenant_id=tenant_id,
                source_id=source_id,
                hat_scope_id=hat_scope_id,
                expected_state=record.current_publication_state,
                expected_sequence=record.current_publication_sequence,
                expected_event_digest=record.current_publication_event_digest,
                event=stored,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=tenant_id,
                operation_id=claim.operation.operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=event_id,
                result_digest=stored.event_digest,
            )
            return stored

        return self._runner.run(
            context,
            operation,
            operation_kind="PUBLICATION_STATE_TRANSITION",
        )

    def list_publication_events(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> tuple[PublicationStateEvent, ...]:
        return self._runner.run(
            context,
            lambda transaction: self._repository.list_events(
                transaction,
                tenant_id,
                source_id,
                hat_scope_id,
            ),
            operation_kind="PUBLICATION_EVENT_READ",
        )

    def verify_publication_event_chain(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> tuple[PublicationStateEvent, ...]:
        def operation(
            transaction: TransactionProtocol,
        ) -> tuple[PublicationStateEvent, ...]:
            record = self._repository.get_registry(
                transaction,
                tenant_id,
                source_id,
                hat_scope_id,
            )
            if record is None:
                raise SourceRegistryValidationError(
                    "source registry entry was not found",
                    sanitized_code="SOURCE_REGISTRY_NOT_FOUND",
                )
            self._require_context(context, record)
            return verify_publication_event_chain(
                record,
                self._repository.list_events(
                    transaction,
                    tenant_id,
                    source_id,
                    hat_scope_id,
                ),
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="PUBLICATION_EVENT_CHAIN_VERIFY",
        )
