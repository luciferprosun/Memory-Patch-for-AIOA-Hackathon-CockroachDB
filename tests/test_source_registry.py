"""Step 9 source-registry, provenance, and publication safety tests."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT, SOURCE_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.contracts import MemoryTargetScope  # noqa: E402
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    OperationStatus,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    ALLOWED_PUBLICATION_TRANSITIONS,
    PUBLICATION_ELIGIBILITY_POLICY_VERSION,
    PUBLICATION_GENESIS_DIGEST,
    CockroachSourceRegistryRepository,
    OriginMetadata,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    ProvenanceConflictError,
    ProvenanceCycleError,
    ProvenanceEdge,
    ProvenanceGraph,
    PublicationEligibilityError,
    PublicationEventChainError,
    PublicationStateEvent,
    PublicationTransitionError,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourceLicenseAssessment,
    SourceLicenseStatus,
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryActorType,
    SourceRegistryConflictError,
    SourceRegistryRecord,
    SourceRegistryService,
    SourceRegistryValidationError,
    SourceScopeDimensions,
    TransformationIdentity,
    advance_registry_state,
    build_publication_event,
    evaluate_publication_eligibility,
    event_from_row,
    require_publication_transition,
    verify_publication_event_chain,
)
from aioa_memory_kernel.contracts.serialization import canonical_json  # noqa: E402


NOW = datetime(2039, 4, 5, 6, 7, 8, tzinfo=UTC)
LATER = NOW + timedelta(seconds=1)
TENANT_A = "tenant-step9-a"
TENANT_B = "tenant-step9-b"
USER_A1 = "user-step9-a1"
USER_A2 = "user-step9-a2"
DIGEST_A = hashlib.sha256(b"step9-root").hexdigest()
DIGEST_B = hashlib.sha256(b"step9-exact-source").hexdigest()
DIGEST_C = hashlib.sha256(b"step9-derived").hexdigest()
TRUSTED_ACTOR = SourceRegistryActor(
    SourceRegistryActorType.TRUSTED_APPLICATION,
    "trusted-step9-test-boundary",
)
SQL_ROOT = REPOSITORY_ROOT / "sql" / "cockroachdb" / "migrations"
STEP9_SQL_PATH = (
    SQL_ROOT / "0006_step9_source_registry_provenance_publication_states.sql"
)
STEP9_SQL = STEP9_SQL_PATH.read_text(encoding="utf-8")
POLICY_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "source-registry"
    / "source-registry-policy-1a.json"
)
SOURCES_ROOT = SOURCE_ROOT / "aioa_memory_kernel" / "sources"


def make_parser(
    name: str = "synthetic-parser",
    version: str = "1.2.3",
    contract: str = "1.0.0",
) -> ParserIdentity:
    return ParserIdentity(name, version, contract)


def make_transformation(
    name: str = "exact-byte-registration",
    version: str = "1.0.0",
    contract: str = "1.0.0",
) -> TransformationIdentity:
    return TransformationIdentity(name, version, contract)


def make_origin(*, external_ref: str | None = "synthetic:step9") -> OriginMetadata:
    return OriginMetadata(
        origin_kind="SYNTHETIC_FIXTURE",
        origin_system="memory-patch-step9-tests",
        origin_version="1.0.0",
        adapter_version="1.0.0",
        external_ref=external_ref,
        observed_at=NOW,
    )


def make_scope(
    *,
    tenant_id: str = TENANT_A,
    personal: bool = False,
    user_id: str = USER_A1,
    collection: tuple[str, ...] = ("synthetic-a", "synthetic-b"),
) -> SourceScopeDimensions:
    return SourceScopeDimensions(
        tenant_id=tenant_id,
        hat_scope_id=(
            f"scope-{tenant_id}-{user_id}"
            if personal
            else f"scope-{tenant_id}-shared"
        ),
        target_scope=(
            MemoryTargetScope.USER_PERSONAL_HAT
            if personal
            else MemoryTargetScope.SHARED_KNOWLEDGE_HAT
        ),
        owner_user_id=user_id if personal else None,
        personal_memory_space_id=f"space-{tenant_id}-{user_id}" if personal else None,
        domain="synthetic",
        jurisdiction="test",
        language="en",
        temporal_policy_reference="synthetic-time-policy-1",
        source_collection=collection,
        additional_dimensions={"fixture": "step9", "revision": 1},
    )


def make_record(
    *,
    tenant_id: str = TENANT_A,
    personal: bool = False,
    user_id: str = USER_A1,
    source_id: str | None = None,
    authority_level: SourceAuthorityLevel = SourceAuthorityLevel.OFFICIAL_PRIMARY,
    authority_basis: dict[str, object] | None = None,
    license_status: SourceLicenseStatus = SourceLicenseStatus.PUBLIC_DOMAIN,
    redaction_state: RedactionState = RedactionState.NOT_REQUIRED,
    artifact_digest: str = DIGEST_B,
    exact_source_bytes: bool = True,
    model_generated: bool = False,
    byte_length: int | None = 19,
    snapshot_id: str | None = "snapshot-step9",
    knowledge_version_id: str | None = "version-step9",
    external_ref: str | None = "synthetic:step9",
) -> SourceRegistryRecord:
    parser = make_parser()
    transformation = make_transformation()
    origin = make_origin(external_ref=external_ref)
    scope = make_scope(
        tenant_id=tenant_id,
        personal=personal,
        user_id=user_id,
    )
    return SourceRegistryRecord(
        tenant_id=tenant_id,
        source_id=source_id or f"source-{tenant_id}-{'private' if personal else 'shared'}",
        hat_scope_id=scope.hat_scope_id,
        source_kind="SYNTHETIC",
        source_reference=f"synthetic:{tenant_id}:{'private' if personal else 'shared'}",
        scope=scope,
        authority=SourceAuthorityAssessment(
            authority_level,
            (
                {"assessment": "deterministic-step9-fixture"}
                if authority_basis is None
                and authority_level is not SourceAuthorityLevel.UNKNOWN
                else (authority_basis or {})
            ),
        ),
        license=SourceLicenseAssessment(
            license_status,
            "synthetic-license-1",
            "synthetic:license",
        ),
        access_class=(
            SourceAccessClass.USER_PRIVATE
            if personal
            else SourceAccessClass.TENANT_RESTRICTED
        ),
        redaction_state=redaction_state,
        parser=parser,
        transformation=transformation,
        origin=origin,
        artifact=ProvenanceArtifactIdentity(
            artifact_kind="EXACT_SOURCE_BYTES",
            artifact_digest=artifact_digest,
            byte_length=byte_length,
            media_type="application/octet-stream",
            origin=origin,
            parser=parser,
            transformation=transformation,
            created_at=NOW,
            exact_source_bytes=exact_source_bytes,
            model_generated=model_generated,
        ),
        snapshot_id=snapshot_id,
        knowledge_version_id=knowledge_version_id,
        current_publication_state=SourcePublicationState.REGISTERED,
        current_publication_sequence=0,
        current_publication_event_digest=PUBLICATION_GENESIS_DIGEST,
        created_at=NOW,
        updated_at=NOW,
    )


def make_edge(
    *,
    edge_id: str = "edge-step9-1",
    tenant_id: str = TENANT_A,
    source_id: str = "source-tenant-step9-a-shared",
    hat_scope_id: str = "scope-tenant-step9-a-shared",
    parent: str = DIGEST_A,
    child: str = DIGEST_B,
) -> ProvenanceEdge:
    return ProvenanceEdge(
        tenant_id=tenant_id,
        source_id=source_id,
        hat_scope_id=hat_scope_id,
        edge_id=edge_id,
        parent_artifact_digest=parent,
        child_artifact_digest=child,
        edge_kind="EXACT_SOURCE_TO_REGISTERED_ARTIFACT",
        parser=make_parser(),
        transformation=make_transformation(),
        metadata={"fixture": "step9"},
        created_at=NOW,
    )


def eligible_decision(record: SourceRegistryRecord):
    return evaluate_publication_eligibility(
        record,
        ProvenanceGraph(),
        evaluated_at=NOW,
    )


def registry_row(record: SourceRegistryRecord) -> dict[str, object]:
    scope_data = {
        "additional_dimensions": dict(record.scope.additional_dimensions),
        "domain": record.scope.domain,
        "jurisdiction": record.scope.jurisdiction,
        "language": record.scope.language,
        "source_collection": list(record.scope.source_collection),
        "temporal_policy_reference": record.scope.temporal_policy_reference,
    }
    return {
        "tenant_id": record.tenant_id,
        "source_id": record.source_id,
        "hat_scope_id": record.hat_scope_id,
        "schema_version": record.schema_version,
        "source_kind": record.source_kind,
        "source_reference": record.source_reference,
        "target_scope": record.scope.target_scope.value,
        "owner_user_id": record.scope.owner_user_id,
        "personal_memory_space_id": record.scope.personal_memory_space_id,
        "authority_level": record.authority.authority_level.value,
        "authority_basis": canonical_json(record.authority.authority_basis),
        "license_status": record.license.license_status.value,
        "license_identifier": record.license.license_identifier,
        "license_reference": record.license.license_reference,
        "access_class": record.access_class.value,
        "redaction_state": record.redaction_state.value,
        "scope_dimensions": canonical_json(scope_data),
        "scope_digest": record.scope.scope_digest,
        "parser_name": record.parser.parser_name,
        "parser_version": record.parser.parser_version,
        "parser_contract_version": record.parser.parser_contract_version,
        "transformation_name": record.transformation.transformation_name,
        "transformation_version": record.transformation.transformation_version,
        "transformation_contract_version": (
            record.transformation.transformation_contract_version
        ),
        "origin_kind": record.origin.origin_kind,
        "origin_system": record.origin.origin_system,
        "origin_version": record.origin.origin_version,
        "adapter_version": record.origin.adapter_version,
        "external_ref": record.origin.external_ref,
        "observed_at": record.origin.observed_at,
        "artifact_kind": record.artifact.artifact_kind,
        "artifact_digest": record.artifact.artifact_digest,
        "artifact_byte_length": record.artifact.byte_length,
        "artifact_media_type": record.artifact.media_type,
        "artifact_created_at": record.artifact.created_at,
        "exact_source_bytes": record.artifact.exact_source_bytes,
        "model_generated": record.artifact.model_generated,
        "snapshot_id": record.snapshot_id,
        "knowledge_version_id": record.knowledge_version_id,
        "current_publication_state": record.current_publication_state.value,
        "current_publication_sequence": record.current_publication_sequence,
        "current_publication_event_digest": (
            record.current_publication_event_digest
        ),
        "registry_digest": record.registry_digest,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


class ScriptedTransaction:
    def __init__(
        self,
        *,
        one: list[dict[str, object] | None] | None = None,
        many: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self.one = list(one or [])
        self.many = list(many or [])
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql: str, parameters: object = None) -> None:
        self.calls.append((sql, parameters))

    def fetch_one(
        self,
        sql: str,
        parameters: object = None,
    ) -> dict[str, object] | None:
        self.calls.append((sql, parameters))
        return self.one.pop(0) if self.one else None

    def fetch_all(
        self,
        sql: str,
        parameters: object = None,
    ) -> tuple[dict[str, object], ...]:
        self.calls.append((sql, parameters))
        return tuple(self.many.pop(0) if self.many else [])


class RegistrationAndIdentityTests(unittest.TestCase):
    def test_01_valid_shared_source_registration(self) -> None:
        record = make_record()
        self.assertIs(record.access_class, SourceAccessClass.TENANT_RESTRICTED)
        self.assertIs(
            record.scope.target_scope,
            MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
        )

    def test_02_valid_personal_source_registration(self) -> None:
        record = make_record(personal=True)
        self.assertIs(record.access_class, SourceAccessClass.USER_PRIVATE)
        self.assertEqual(record.scope.owner_user_id, USER_A1)

    def test_03_exact_registration_replay_returns_same_record(self) -> None:
        record = make_record()
        transaction = ScriptedTransaction(one=[registry_row(record)])
        result = CockroachSourceRegistryRepository().put_registry(
            transaction,
            record,
        )
        self.assertEqual(result, record)
        self.assertEqual(len(transaction.calls), 1)

    def test_04_conflicting_registration_replay_fails(self) -> None:
        record = make_record()
        changed = make_record(license_status=SourceLicenseStatus.CONFIRMED_PERMISSIVE)
        transaction = ScriptedTransaction(one=[registry_row(record)])
        with self.assertRaises(SourceRegistryConflictError):
            CockroachSourceRegistryRepository().put_registry(transaction, changed)

    def test_05_unknown_authority_enum_fails(self) -> None:
        with self.assertRaises(ValueError):
            SourceAuthorityLevel("NOT_A_SOURCE_AUTHORITY")

    def test_06_known_authority_without_basis_fails(self) -> None:
        with self.assertRaises(SourceRegistryValidationError):
            SourceAuthorityAssessment(
                SourceAuthorityLevel.OFFICIAL_PRIMARY,
                {},
            )

    def test_07_derived_without_parent_lineage_is_ineligible(self) -> None:
        record = make_record(authority_level=SourceAuthorityLevel.DERIVED)
        decision = eligible_decision(record)
        self.assertFalse(decision.eligible)
        self.assertIn("DERIVED_PARENT_LINEAGE_MISSING", decision.reason_codes)

    def test_08_prohibited_license_is_ineligible(self) -> None:
        decision = eligible_decision(
            make_record(license_status=SourceLicenseStatus.PROHIBITED)
        )
        self.assertIn("LICENSE_PROHIBITED", decision.reason_codes)

    def test_09_unknown_license_is_ineligible(self) -> None:
        decision = eligible_decision(
            make_record(license_status=SourceLicenseStatus.UNKNOWN)
        )
        self.assertIn("LICENSE_UNKNOWN", decision.reason_codes)

    def test_10_private_authorization_remains_owner_scoped(self) -> None:
        record = make_record(
            personal=True,
            license_status=SourceLicenseStatus.PRIVATE_AUTHORIZED,
        )
        self.assertEqual(record.scope.owner_user_id, USER_A1)
        with self.assertRaises(SourceRegistryValidationError):
            replace(
                record,
                scope=make_scope(personal=False),
                hat_scope_id=make_scope(personal=False).hat_scope_id,
            )

    def test_11_pending_redaction_is_ineligible(self) -> None:
        decision = eligible_decision(
            make_record(redaction_state=RedactionState.PENDING)
        )
        self.assertIn("REDACTION_PENDING", decision.reason_codes)

    def test_12_rejected_redaction_is_ineligible(self) -> None:
        decision = eligible_decision(
            make_record(redaction_state=RedactionState.REJECTED)
        )
        self.assertIn("REDACTION_REJECTED", decision.reason_codes)

    def test_13_parser_identity_fields_are_required(self) -> None:
        for values in (
            ("", "1.0.0", "1.0.0"),
            ("parser", "", "1.0.0"),
            ("parser", "1.0.0", ""),
        ):
            with self.subTest(values=values):
                with self.assertRaises(SourceRegistryValidationError):
                    ParserIdentity(*values)

    def test_14_transformation_identity_fields_are_required(self) -> None:
        for values in (
            ("", "1.0.0", "1.0.0"),
            ("transform", "", "1.0.0"),
            ("transform", "1.0.0", ""),
        ):
            with self.subTest(values=values):
                with self.assertRaises(SourceRegistryValidationError):
                    TransformationIdentity(*values)

    def test_15_mutable_latest_alias_is_rejected(self) -> None:
        for factory in (
            lambda: ParserIdentity("parser", "latest", "1"),
            lambda: TransformationIdentity("transform", "1", "LATEST"),
            lambda: OriginMetadata("kind", "system", "latest", "1", None),
        ):
            with self.subTest(factory=factory):
                with self.assertRaises(SourceRegistryValidationError):
                    factory()

    def test_16_scope_digest_is_deterministic(self) -> None:
        self.assertEqual(make_scope().scope_digest, make_scope().scope_digest)

    def test_17_canonical_scope_ignores_collection_input_order(self) -> None:
        left = make_scope(collection=("synthetic-b", "synthetic-a"))
        right = make_scope(collection=("synthetic-a", "synthetic-b"))
        self.assertEqual(left.scope_digest, right.scope_digest)

    def test_18_meaningful_scope_change_changes_digest(self) -> None:
        self.assertNotEqual(
            make_scope().scope_digest,
            replace(make_scope(), language="pl", scope_digest="").scope_digest,
        )

    def test_19_personal_shared_scope_substitution_is_rejected(self) -> None:
        personal = make_scope(personal=True)
        with self.assertRaises(SourceRegistryValidationError):
            replace(
                personal,
                target_scope=MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
                scope_digest="",
            )

    def test_20_external_reference_alone_does_not_authorize(self) -> None:
        record = make_record(
            authority_level=SourceAuthorityLevel.UNKNOWN,
            authority_basis={},
            external_ref="external:untrusted-reference",
        )
        self.assertIn(
            "AUTHORITY_UNKNOWN",
            eligible_decision(record).reason_codes,
        )

    def test_21_model_summary_is_not_exact_source_snapshot(self) -> None:
        record = make_record(
            exact_source_bytes=False,
            model_generated=True,
        )
        decision = eligible_decision(record)
        self.assertIn("EXACT_SOURCE_BYTES_UNVERIFIED", decision.reason_codes)


class ProvenanceTests(unittest.TestCase):
    def test_22_provenance_edge_exact_replay_is_idempotent(self) -> None:
        edge = make_edge()
        graph = ProvenanceGraph((edge,))
        self.assertEqual(graph.add_edge(edge), edge)
        self.assertEqual(len(graph.edges), 1)

    def test_23_provenance_edge_digest_conflict_fails(self) -> None:
        graph = ProvenanceGraph((make_edge(),))
        with self.assertRaises(ProvenanceConflictError):
            graph.add_edge(make_edge(child=DIGEST_C))

    def test_24_provenance_self_edge_is_rejected(self) -> None:
        with self.assertRaises(SourceRegistryValidationError):
            make_edge(parent=DIGEST_A, child=DIGEST_A)

    def test_25_provenance_cycle_is_rejected(self) -> None:
        graph = ProvenanceGraph((make_edge(parent=DIGEST_A, child=DIGEST_B),))
        with self.assertRaises(ProvenanceCycleError):
            graph.add_edge(
                make_edge(
                    edge_id="edge-step9-2",
                    parent=DIGEST_B,
                    child=DIGEST_A,
                )
            )

    def test_26_derived_artifact_missing_parent_is_rejected_by_policy(self) -> None:
        record = make_record(
            authority_level=SourceAuthorityLevel.DERIVED,
            artifact_digest=DIGEST_C,
        )
        self.assertFalse(eligible_decision(record).eligible)


class PublicationTests(unittest.TestCase):
    def test_27_legal_publication_transition_passes(self) -> None:
        require_publication_transition(
            SourcePublicationState.REGISTERED,
            SourcePublicationState.REVIEW_REQUIRED,
        )

    def test_28_illegal_publication_transition_fails(self) -> None:
        with self.assertRaises(PublicationTransitionError):
            require_publication_transition(
                SourcePublicationState.REGISTERED,
                SourcePublicationState.WITHDRAWN,
            )

    def test_29_direct_registered_to_published_fails(self) -> None:
        record = make_record()
        with self.assertRaises(PublicationTransitionError):
            build_publication_event(
                record,
                event_id="event-direct-publish",
                target_state=SourcePublicationState.PUBLISHED,
                eligibility=eligible_decision(record),
                actor=TRUSTED_ACTOR,
                reason_codes=(),
                reviewer_reference="reviewer-step9",
                created_at=NOW,
            )

    def test_30_eligible_state_requires_deterministic_eligibility_pass(self) -> None:
        record = replace(
            make_record(license_status=SourceLicenseStatus.PROHIBITED),
            current_publication_state=SourcePublicationState.REVIEW_REQUIRED,
            current_publication_sequence=1,
            current_publication_event_digest=DIGEST_A,
            updated_at=LATER,
        )
        with self.assertRaises(PublicationEligibilityError):
            build_publication_event(
                record,
                event_id="event-ineligible",
                target_state=SourcePublicationState.ELIGIBLE,
                eligibility=eligible_decision(record),
                actor=TRUSTED_ACTOR,
                reason_codes=("LICENSE_PROHIBITED",),
                reviewer_reference="reviewer-step9",
                created_at=LATER,
            )

    def test_31_published_state_revalidates_eligibility(self) -> None:
        record = replace(
            make_record(license_status=SourceLicenseStatus.PROHIBITED),
            current_publication_state=SourcePublicationState.ELIGIBLE,
            current_publication_sequence=2,
            current_publication_event_digest=DIGEST_A,
            updated_at=LATER,
        )
        with self.assertRaises(PublicationEligibilityError):
            build_publication_event(
                record,
                event_id="event-publish-ineligible",
                target_state=SourcePublicationState.PUBLISHED,
                eligibility=eligible_decision(record),
                actor=TRUSTED_ACTOR,
                reason_codes=("LICENSE_PROHIBITED",),
                reviewer_reference="reviewer-step9",
                created_at=LATER,
            )

    def test_32_quarantined_to_published_fails(self) -> None:
        with self.assertRaises(PublicationTransitionError):
            require_publication_transition(
                SourcePublicationState.QUARANTINED,
                SourcePublicationState.PUBLISHED,
            )

    def test_33_rejected_is_terminal(self) -> None:
        self.assertEqual(
            ALLOWED_PUBLICATION_TRANSITIONS[SourcePublicationState.REJECTED],
            frozenset(),
        )

    def test_34_exact_transition_replay_returns_same_event(self) -> None:
        record = make_record()
        arguments = {
            "event_id": "event-step9-1",
            "target_state": SourcePublicationState.REVIEW_REQUIRED,
            "eligibility": eligible_decision(record),
            "actor": TRUSTED_ACTOR,
            "reason_codes": ("HUMAN_REVIEW_REQUIRED",),
            "reviewer_reference": "reviewer-step9",
            "created_at": LATER,
        }
        self.assertEqual(
            build_publication_event(record, **arguments),
            build_publication_event(record, **arguments),
        )

    def test_35_changed_transition_request_changes_digest_and_conflicts(self) -> None:
        record = make_record()
        base = build_publication_event(
            record,
            event_id="event-step9-1",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=("FIRST",),
            reviewer_reference="reviewer-step9",
            created_at=LATER,
        )
        changed = build_publication_event(
            record,
            event_id="event-step9-1",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=("DIFFERENT",),
            reviewer_reference="reviewer-step9",
            created_at=LATER,
        )
        self.assertNotEqual(base.event_digest, changed.event_digest)

    def test_36_event_sequence_is_monotonic(self) -> None:
        record = make_record()
        event = build_publication_event(
            record,
            event_id="event-step9-1",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference="reviewer-step9",
            created_at=LATER,
        )
        self.assertEqual(event.sequence_number, 1)
        self.assertEqual(advance_registry_state(record, event).current_publication_sequence, 1)

    def test_37_previous_event_digest_chain_is_valid(self) -> None:
        record = make_record()
        first = build_publication_event(
            record,
            event_id="event-step9-1",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference="reviewer-step9",
            created_at=LATER,
        )
        current = advance_registry_state(record, first)
        second = build_publication_event(
            current,
            event_id="event-step9-2",
            target_state=SourcePublicationState.ELIGIBLE,
            eligibility=eligible_decision(current),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference="reviewer-step9",
            created_at=LATER + timedelta(seconds=1),
        )
        terminal = advance_registry_state(current, second)
        self.assertEqual(
            verify_publication_event_chain(terminal, (first, second)),
            (first, second),
        )

    def test_38_event_tampering_is_detected(self) -> None:
        record = make_record()
        event = build_publication_event(
            record,
            event_id="event-step9-tamper",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference="reviewer-step9",
            created_at=LATER,
        )
        terminal = advance_registry_state(record, event)
        object.__setattr__(event, "reviewer_reference", "tampered")
        with self.assertRaises(PublicationEventChainError):
            verify_publication_event_chain(terminal, (event,))

    def test_39_event_gap_is_detected(self) -> None:
        record = make_record()
        gap = PublicationStateEvent(
            tenant_id=record.tenant_id,
            source_id=record.source_id,
            hat_scope_id=record.hat_scope_id,
            event_id="event-gap",
            sequence_number=2,
            from_state=SourcePublicationState.REGISTERED,
            to_state=SourcePublicationState.REVIEW_REQUIRED,
            actor_type=TRUSTED_ACTOR.actor_type,
            actor_reference=TRUSTED_ACTOR.actor_reference,
            policy_version=PUBLICATION_ELIGIBILITY_POLICY_VERSION,
            eligibility_decision_digest=eligible_decision(record).decision_digest,
            reason_codes=(),
            reviewer_reference="reviewer-step9",
            previous_event_digest=PUBLICATION_GENESIS_DIGEST,
            created_at=LATER,
        )
        terminal = replace(
            record,
            current_publication_state=gap.to_state,
            current_publication_sequence=2,
            current_publication_event_digest=gap.event_digest,
            updated_at=LATER,
        )
        with self.assertRaises(PublicationEventChainError):
            verify_publication_event_chain(terminal, (gap,))

    def test_40_current_state_event_mismatch_is_detected(self) -> None:
        record = make_record()
        event = build_publication_event(
            record,
            event_id="event-terminal-mismatch",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference="reviewer-step9",
            created_at=LATER,
        )
        with self.assertRaises(PublicationEventChainError):
            verify_publication_event_chain(record, (event,))


class SqlIsolationAndAuthorityTests(unittest.TestCase):
    def test_41_cross_tenant_registry_read_is_rls_constrained(self) -> None:
        self.assertIn("source_registry_entries_s9_select", STEP9_SQL)
        self.assertIn(
            "hat_scope_context_matches(tenant_id, hat_scope_id)",
            STEP9_SQL,
        )

    def test_42_cross_tenant_registry_write_is_rls_constrained(self) -> None:
        self.assertIn("source_registry_entries_s9_insert", STEP9_SQL)
        self.assertIn("WITH CHECK", STEP9_SQL)

    def test_43_same_tenant_cross_user_private_read_is_denied_by_scope(self) -> None:
        self.assertIn("source_registry_entries_personal_scope_fk", STEP9_SQL)
        self.assertIn("USER_PERSONAL_HAT", STEP9_SQL)

    def test_44_same_tenant_cross_user_private_write_is_denied_by_scope(self) -> None:
        self.assertIn("source_registry_entries_s9_update", STEP9_SQL)
        self.assertGreaterEqual(STEP9_SQL.count("hat_scope_context_matches"), 7)

    def test_45_unset_tenant_context_fails_closed(self) -> None:
        helper_sql = (
            SQL_ROOT / "0004_step5_tenant_roles_session_context_rls.sql"
        ).read_text(encoding="utf-8")
        tenant_helper = helper_sql[
            helper_sql.index(
                "CREATE OR REPLACE FUNCTION memory_patch.tenant_context_matches"
            ) :
            helper_sql.index(
                "CREATE OR REPLACE FUNCTION memory_patch.user_context_matches"
            )
        ]
        self.assertIn("SELECT EXISTS", tenant_helper)
        self.assertIn("request_contexts", tenant_helper)
        self.assertNotIn("COALESCE", tenant_helper.upper())

    def test_46_unset_user_context_cannot_access_personal_records(self) -> None:
        helper_sql = (
            SQL_ROOT / "0004_step5_tenant_roles_session_context_rls.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("USER_PRIVATE", helper_sql)
        self.assertIn("owner_user_id", helper_sql)

    def test_47_table_owner_does_not_bypass_force_rls(self) -> None:
        for table in (
            "source_registry_entries",
            "source_provenance_edges",
            "source_publication_events",
        ):
            self.assertIn(
                f"ALTER TABLE memory_patch.{table}\n  FORCE ROW LEVEL SECURITY",
                STEP9_SQL,
            )
            self.assertIn(f"ALTER TABLE memory_patch.{table} OWNER TO mp_schema_owner", STEP9_SQL)

    def test_48_runtime_roles_have_no_bypassrls(self) -> None:
        all_sql = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SQL_ROOT.glob("*.sql"))
        )
        self.assertNotIn("WITH BYPASSRLS", all_sql.upper())
        self.assertNotIn("SET BYPASSRLS", all_sql.upper())
        self.assertIn("NOBYPASSRLS", all_sql.upper())
        self.assertNotIn("OWNER TO mp_app_runtime", STEP9_SQL)

    def test_49_model_hat_or_critic_actor_cannot_publish(self) -> None:
        allowed = {member.value for member in SourceRegistryActorType}
        self.assertTrue(
            {"MODEL", "HAT", "CRITIC", "PROVIDER"}.isdisjoint(allowed)
        )
        with self.assertRaises(SourceRegistryValidationError):
            SourceRegistryActor("MODEL", "model-step9")  # type: ignore[arg-type]

    def test_50_publication_state_grants_no_answer_permission(self) -> None:
        names = {field.name for field in fields(SourceRegistryRecord)}
        self.assertNotIn("answer_permission", names)

    def test_51_publication_state_grants_no_approval(self) -> None:
        names = {field.name for field in fields(PublicationStateEvent)}
        self.assertNotIn("approval", names)
        self.assertNotIn("approved", names)

    def test_52_publication_state_grants_no_commit_authority(self) -> None:
        names = {field.name for field in fields(PublicationStateEvent)}
        self.assertNotIn("commit_authority", names)

    def test_53_publication_state_grants_no_execution_authority(self) -> None:
        names = {field.name for field in fields(SourceRegistryRecord)}
        self.assertNotIn("execution_authority", names)


class ScopeAndClosureTests(unittest.TestCase):
    def test_54_no_aws_dependency_is_imported(self) -> None:
        imported: set[str] = set()
        for path in SOURCES_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported.update(
                alias.name.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            imported.update(
                (node.module or "").casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
        self.assertFalse(
            any(
                token in module
                for module in imported
                for token in ("boto", "botocore", "aws")
            )
        )

    def test_55_no_aws_command_is_invoked(self) -> None:
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SOURCES_ROOT.glob("*.py")
        ).casefold()
        for forbidden in ("aws s3", "aws s3api", "subprocess", "os.system"):
            self.assertNotIn(forbidden, source_text)

    def test_56_no_step10_ingestion_state_machine_exists(self) -> None:
        names = {path.name.casefold() for path in SOURCES_ROOT.glob("*.py")}
        self.assertNotIn("ingestion.py", names)
        self.assertNotIn("saga.py", names)
        self.assertNotIn("downloader.py", names)

    def test_57_migrations_0001_through_0005_are_unchanged(self) -> None:
        expected = {
            "0001_step4_identity_and_hat_scopes.sql": (
                "a7d6e835d16debc77830cbcb2803c3b01400622c3186a64f7d627f4bf0a767a0"
            ),
            "0002_step4_knowledge_lineage_and_retrieval.sql": (
                "a8ddf1d342e58c8e12ecb082443b6367921fa5ec0e6edce92401f560300058d4"
            ),
            "0003_step4_kernel_memory_and_audit_evidence.sql": (
                "f25865e958d9bb352b8fc03512474fb848a688cfa6c73a5790664745779de881"
            ),
            "0004_step5_tenant_roles_session_context_rls.sql": (
                "6a8968dab3aa063b2d6f34bb31ecd26039e50f2f9351d961140c3a739106fbcd"
            ),
            "0005_step6_persistence_idempotency_retry_foundation.sql": (
                "c6ad8cfe2b56b4bb59c6e604ae9f6281242e1baed135b94feaea6087f1651173"
            ),
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    hashlib.sha256((SQL_ROOT / filename).read_bytes()).hexdigest(),
                    digest,
                )

    def test_58_manifest_contains_exact_step9_checksum(self) -> None:
        manifest = json.loads(
            (SQL_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        final = next(
            row
            for row in manifest["migrations"]
            if row["migration_id"]
            == "0006_step9_source_registry_provenance_publication_states"
        )
        self.assertEqual(
            final["migration_id"],
            "0006_step9_source_registry_provenance_publication_states",
        )
        self.assertEqual(
            final["sha256"],
            hashlib.sha256(STEP9_SQL_PATH.read_bytes()).hexdigest(),
        )

    def test_59_second_migration_run_is_checksum_verified_noop(self) -> None:
        script = REPOSITORY_ROOT / "scripts" / "run_cockroachdb_migrations.py"
        spec = importlib.util.spec_from_file_location("step9_migrations_test", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded = module.load_migrations()
        existing = {item.migration_id: item.sha256 for item in loaded}
        client = mock.Mock()
        with mock.patch.object(
            module,
            "applied_migrations",
            return_value=existing,
        ):
            result = module.apply_migrations(client, "mp_step9_noop")
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["skipped_count"], 7)
        client.execute.assert_not_called()

    def test_60_two_database_reproduction_is_required_by_harness(self) -> None:
        harness = REPOSITORY_ROOT / "scripts" / "run_source_registry_validation.py"
        self.assertTrue(harness.is_file())
        text = harness.read_text(encoding="utf-8")
        self.assertIn("reproduction_database", text)
        self.assertIn("schema_security_digest", text)
        self.assertIn("reproduction_digest", text)


class Step9HardeningTests(unittest.TestCase):
    def test_61_publication_pointer_requires_exact_genesis_equivalence(self) -> None:
        record = make_record()
        with self.assertRaises(SourceRegistryValidationError):
            replace(
                record,
                current_publication_sequence=1,
                current_publication_event_digest=DIGEST_A,
            )
        with self.assertRaises(SourceRegistryValidationError):
            replace(
                record,
                current_publication_state=SourcePublicationState.REVIEW_REQUIRED,
                current_publication_sequence=1,
                current_publication_event_digest=PUBLICATION_GENESIS_DIGEST,
            )
        transitioned = replace(
            record,
            current_publication_state=SourcePublicationState.REVIEW_REQUIRED,
            current_publication_sequence=1,
            current_publication_event_digest=DIGEST_A,
            updated_at=LATER,
        )
        self.assertEqual(transitioned.current_publication_sequence, 1)

    def test_62_repository_rejects_non_genesis_registration_before_sql(self) -> None:
        transitioned = replace(
            make_record(),
            current_publication_state=SourcePublicationState.PUBLISHED,
            current_publication_sequence=3,
            current_publication_event_digest=DIGEST_A,
            updated_at=LATER,
        )
        transaction = ScriptedTransaction()
        with self.assertRaises(SourceRegistryValidationError):
            CockroachSourceRegistryRepository().put_registry(
                transaction,
                transitioned,
            )
        self.assertEqual(transaction.calls, [])

    def test_63_service_reuses_step6_runner_and_idempotency(self) -> None:
        record = make_record()
        context = RequestContext(TENANT_A, None, AccessMode.TENANT_SHARED)
        transaction = ScriptedTransaction()
        runner = SerializableTransactionRunner(lambda: None)

        def run_inline(
            received_context: RequestContext,
            callback,
            *,
            operation_kind: str,
        ):
            self.assertEqual(received_context, context)
            self.assertEqual(operation_kind, "SOURCE_REGISTER")
            return callback(transaction)

        runner.run = mock.Mock(side_effect=run_inline)  # type: ignore[method-assign]
        repository = mock.Mock()
        repository.source_scope_exists.return_value = True
        repository.put_registry.return_value = record
        claim = mock.Mock()
        claim.may_proceed = True
        claim.operation.operation_id = "operation-step9-register"
        claim.operation.attempt_count = 1
        idempotency = mock.Mock()
        idempotency.begin_or_resume_operation.return_value = claim
        service = SourceRegistryService(
            runner,
            repository=repository,
            idempotency=idempotency,
            clock=lambda: NOW,
        )

        stored = service.register_source(
            context,
            record,
            operation_id="operation-step9-register",
            idempotency_key="source-register-step9",
        )

        self.assertEqual(stored, record)
        begin = idempotency.begin_or_resume_operation.call_args.args[1]
        self.assertEqual(begin.operation_kind, "SOURCE_REGISTER")
        self.assertEqual(begin.request_digest, record.registry_digest)
        self.assertEqual(begin.scope_digest, record.scope.scope_digest)
        repository.put_registry.assert_called_once_with(transaction, record)
        idempotency.complete_operation.assert_called_once()
        runner.run.assert_called_once()

    def test_64_service_rejects_non_genesis_before_step6_claim(self) -> None:
        transitioned = replace(
            make_record(),
            current_publication_state=SourcePublicationState.PUBLISHED,
            current_publication_sequence=3,
            current_publication_event_digest=DIGEST_A,
            updated_at=LATER,
        )
        runner = SerializableTransactionRunner(lambda: None)
        runner.run = mock.Mock()  # type: ignore[method-assign]
        repository = mock.Mock()
        idempotency = mock.Mock()
        service = SourceRegistryService(
            runner,
            repository=repository,
            idempotency=idempotency,
        )
        with self.assertRaises(SourceRegistryValidationError):
            service.register_source(
                RequestContext(TENANT_A, None, AccessMode.TENANT_SHARED),
                transitioned,
                operation_id="operation-bypass",
                idempotency_key="key-bypass",
            )
        runner.run.assert_not_called()
        idempotency.begin_or_resume_operation.assert_not_called()
        repository.put_registry.assert_not_called()

    def test_65_sql_insert_policy_requires_exact_genesis(self) -> None:
        policy = STEP9_SQL[
            STEP9_SQL.index("CREATE POLICY source_registry_entries_s9_insert") :
            STEP9_SQL.index("CREATE POLICY source_registry_entries_s9_update")
        ]
        self.assertIn("current_publication_state = 'REGISTERED'", policy)
        self.assertIn("current_publication_sequence = 0", policy)
        self.assertIn(PUBLICATION_GENESIS_DIGEST, policy)

    def test_66_publication_event_roundtrip_preserves_actor(self) -> None:
        record = make_record()
        event = build_publication_event(
            record,
            event_id="event-actor-roundtrip",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference="review-evidence-step9",
            created_at=LATER,
        )
        row = {
            "tenant_id": event.tenant_id,
            "source_id": event.source_id,
            "hat_scope_id": event.hat_scope_id,
            "event_id": event.event_id,
            "sequence_number": event.sequence_number,
            "from_state": event.from_state.value,
            "to_state": event.to_state.value,
            "actor_type": event.actor_type.value,
            "actor_reference": event.actor_reference,
            "policy_version": event.policy_version,
            "eligibility_decision_digest": event.eligibility_decision_digest,
            "reason_codes": canonical_json(event.reason_codes),
            "reviewer_reference": event.reviewer_reference,
            "previous_event_digest": event.previous_event_digest,
            "event_digest": event.event_digest,
            "created_at": event.created_at,
        }
        self.assertEqual(event_from_row(row), event)

    def test_67_actor_reference_changes_event_digest_and_tampering_fails(self) -> None:
        record = make_record()
        first = build_publication_event(
            record,
            event_id="event-actor-binding",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference=None,
            created_at=LATER,
        )
        second = build_publication_event(
            record,
            event_id="event-actor-binding",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=SourceRegistryActor(
                SourceRegistryActorType.TRUSTED_APPLICATION,
                "different-trusted-boundary",
            ),
            reason_codes=(),
            reviewer_reference=None,
            created_at=LATER,
        )
        self.assertNotEqual(first.event_digest, second.event_digest)
        terminal = advance_registry_state(record, first)
        object.__setattr__(first, "actor_reference", "tampered-boundary")
        with self.assertRaises(PublicationEventChainError):
            verify_publication_event_chain(terminal, (first,))

    def test_68_lineage_digest_binds_all_branches_order_independently(self) -> None:
        root_a = hashlib.sha256(b"root-a").hexdigest()
        root_b = hashlib.sha256(b"root-b").hexdigest()
        terminal = DIGEST_C
        first = make_edge(edge_id="edge-branch-a", parent=root_a, child=terminal)
        second = make_edge(edge_id="edge-branch-b", parent=root_b, child=terminal)
        left = ProvenanceGraph((first, second))
        right = ProvenanceGraph((second, first))
        self.assertEqual(left.root_digests(terminal), (root_a, root_b))
        self.assertEqual(left.lineage_digest(terminal), right.lineage_digest(terminal))
        record = make_record(artifact_digest=terminal)
        left_decision = evaluate_publication_eligibility(
            record,
            left,
            evaluated_at=NOW,
        )
        right_decision = evaluate_publication_eligibility(
            record,
            right,
            evaluated_at=NOW,
        )
        self.assertEqual(left_decision.decision_digest, right_decision.decision_digest)

    def test_69_lineage_digest_changes_with_nonfirst_branch(self) -> None:
        root_a = hashlib.sha256(b"root-a").hexdigest()
        root_b = hashlib.sha256(b"root-b").hexdigest()
        terminal = DIGEST_C
        first = make_edge(edge_id="edge-branch-a", parent=root_a, child=terminal)
        original = make_edge(
            edge_id="edge-branch-b",
            parent=root_b,
            child=terminal,
        )
        changed = replace(original, metadata={"fixture": "changed"}, edge_digest="")
        self.assertNotEqual(
            ProvenanceGraph((first, original)).lineage_digest(terminal),
            ProvenanceGraph((first, changed)).lineage_digest(terminal),
        )

    def test_70_lineage_digest_ignores_disconnected_edges(self) -> None:
        terminal_edge = make_edge(
            edge_id="edge-terminal",
            parent=DIGEST_A,
            child=DIGEST_C,
        )
        disconnected = make_edge(
            edge_id="edge-disconnected",
            parent=hashlib.sha256(b"other-root").hexdigest(),
            child=hashlib.sha256(b"other-terminal").hexdigest(),
        )
        base = ProvenanceGraph((terminal_edge,))
        extended = ProvenanceGraph((terminal_edge, disconnected))
        self.assertEqual(
            base.lineage_digest(DIGEST_C),
            extended.lineage_digest(DIGEST_C),
        )

    def test_71_eligibility_rejects_provenance_from_another_scope(self) -> None:
        record = make_record()
        foreign = make_edge(
            edge_id="edge-foreign-scope",
            tenant_id=TENANT_B,
            source_id=f"source-{TENANT_B}-shared",
            hat_scope_id=f"scope-{TENANT_B}-shared",
            parent=DIGEST_A,
            child=record.artifact.artifact_digest,
        )
        with self.assertRaises(SourceRegistryValidationError) as raised:
            evaluate_publication_eligibility(
                record,
                ProvenanceGraph((foreign,)),
                evaluated_at=NOW,
            )
        self.assertEqual(
            raised.exception.sanitized_code,
            "PROVENANCE_SCOPE_MISMATCH",
        )

    def test_72_transition_retry_omits_generated_time_from_request_digest(
        self,
    ) -> None:
        record = make_record()
        event = build_publication_event(
            record,
            event_id="event-stable-retry",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference=None,
            created_at=NOW,
        )
        context = RequestContext(TENANT_A, None, AccessMode.TENANT_SHARED)
        transaction = ScriptedTransaction()
        runner = SerializableTransactionRunner(lambda: None)
        runner.run = mock.Mock(  # type: ignore[method-assign]
            side_effect=lambda received, callback, *, operation_kind: callback(
                transaction
            )
        )
        repository = mock.Mock()
        repository.get_registry.return_value = record
        repository.get_event.return_value = event
        claim = mock.Mock()
        claim.may_proceed = False
        claim.operation.status = OperationStatus.COMPLETED
        claim.operation.result_digest = event.event_digest
        idempotency = mock.Mock()
        idempotency.begin_or_resume_operation.return_value = claim
        clock_values = iter((NOW, LATER))
        service = SourceRegistryService(
            runner,
            repository=repository,
            idempotency=idempotency,
            clock=lambda: next(clock_values),
        )
        arguments = {
            "tenant_id": record.tenant_id,
            "source_id": record.source_id,
            "hat_scope_id": record.hat_scope_id,
            "expected_state": SourcePublicationState.REGISTERED,
            "expected_registry_digest": record.registry_digest,
            "expected_scope_digest": record.scope.scope_digest,
            "target_state": SourcePublicationState.REVIEW_REQUIRED,
            "event_id": event.event_id,
            "operation_id": "operation-stable-retry",
            "idempotency_key": "publication-stable-retry",
            "reason_codes": (),
            "actor": TRUSTED_ACTOR,
        }

        first = service.transition_publication_state(context, **arguments)
        second = service.transition_publication_state(context, **arguments)

        self.assertEqual(first, event)
        self.assertEqual(second, event)
        requests = [
            call.args[1]
            for call in idempotency.begin_or_resume_operation.call_args_list
        ]
        self.assertEqual(requests[0].request_digest, requests[1].request_digest)
        self.assertNotEqual(requests[0].created_at, requests[1].created_at)

    def test_73_actor_reference_changes_transition_request_digest(self) -> None:
        record = make_record()
        event = build_publication_event(
            record,
            event_id="event-actor-request-binding",
            target_state=SourcePublicationState.REVIEW_REQUIRED,
            eligibility=eligible_decision(record),
            actor=TRUSTED_ACTOR,
            reason_codes=(),
            reviewer_reference=None,
            created_at=NOW,
        )
        context = RequestContext(TENANT_A, None, AccessMode.TENANT_SHARED)
        transaction = ScriptedTransaction()
        runner = SerializableTransactionRunner(lambda: None)
        runner.run = mock.Mock(  # type: ignore[method-assign]
            side_effect=lambda received, callback, *, operation_kind: callback(
                transaction
            )
        )
        repository = mock.Mock()
        repository.get_registry.return_value = record
        repository.get_event.return_value = event
        claim = mock.Mock()
        claim.may_proceed = False
        claim.operation.status = OperationStatus.COMPLETED
        claim.operation.result_digest = event.event_digest
        idempotency = mock.Mock()
        idempotency.begin_or_resume_operation.return_value = claim
        service = SourceRegistryService(
            runner,
            repository=repository,
            idempotency=idempotency,
            clock=lambda: NOW,
        )
        common = {
            "tenant_id": record.tenant_id,
            "source_id": record.source_id,
            "hat_scope_id": record.hat_scope_id,
            "expected_state": SourcePublicationState.REGISTERED,
            "expected_registry_digest": record.registry_digest,
            "expected_scope_digest": record.scope.scope_digest,
            "target_state": SourcePublicationState.REVIEW_REQUIRED,
            "event_id": event.event_id,
            "operation_id": "operation-actor-request-binding",
            "idempotency_key": "publication-actor-request-binding",
            "reason_codes": (),
            "created_at": NOW,
        }
        service.transition_publication_state(
            context,
            actor=TRUSTED_ACTOR,
            **common,
        )
        service.transition_publication_state(
            context,
            actor=SourceRegistryActor(
                SourceRegistryActorType.TRUSTED_APPLICATION,
                "different-trusted-step9-boundary",
            ),
            **common,
        )
        requests = [
            call.args[1]
            for call in idempotency.begin_or_resume_operation.call_args_list
        ]
        self.assertNotEqual(requests[0].request_digest, requests[1].request_digest)

    def test_74_provenance_service_reuses_step6_runner_and_idempotency(
        self,
    ) -> None:
        record = make_record()
        edge = make_edge()
        context = RequestContext(TENANT_A, None, AccessMode.TENANT_SHARED)
        transaction = ScriptedTransaction()
        runner = SerializableTransactionRunner(lambda: None)

        def run_inline(
            received_context: RequestContext,
            callback,
            *,
            operation_kind: str,
        ):
            self.assertEqual(received_context, context)
            self.assertEqual(operation_kind, "PROVENANCE_EDGE_APPEND")
            return callback(transaction)

        runner.run = mock.Mock(side_effect=run_inline)  # type: ignore[method-assign]
        repository = mock.Mock()
        repository.get_registry.return_value = record
        repository.put_edge.return_value = edge
        claim = mock.Mock()
        claim.may_proceed = True
        claim.operation.operation_id = "operation-step9-provenance"
        claim.operation.attempt_count = 1
        idempotency = mock.Mock()
        idempotency.begin_or_resume_operation.return_value = claim
        service = SourceRegistryService(
            runner,
            repository=repository,
            idempotency=idempotency,
            clock=lambda: NOW,
        )

        stored = service.record_provenance_edge(
            context,
            edge,
            operation_id="operation-step9-provenance",
            idempotency_key="provenance-step9",
        )

        self.assertEqual(stored, edge)
        begin = idempotency.begin_or_resume_operation.call_args.args[1]
        self.assertEqual(begin.operation_kind, "PROVENANCE_EDGE_APPEND")
        self.assertEqual(begin.request_digest, edge.edge_digest)
        repository.put_edge.assert_called_once_with(transaction, edge)
        idempotency.complete_operation.assert_called_once()


if __name__ == "__main__":
    unittest.main()
