"""Step 10 durable saga, reconciliation, isolation, and authority tests."""

from __future__ import annotations

import ast
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._support import REPOSITORY_ROOT, SOURCE_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_sha256,
    sha256_hex,
)
from aioa_memory_kernel.ingestion import (  # noqa: E402
    INGESTION_GENESIS_DIGEST,
    MILESTONE_ORDER,
    ExternalEffectIntent,
    ExternalEffectKind,
    ExternalEffectReceipt,
    ExternalEffectRecord,
    ExternalEffectStatus,
    FailureDecision,
    IngestionClaimError,
    IngestionConflictError,
    IngestionExecutionError,
    IngestionFailureClass,
    IngestionOrchestrator,
    IngestionReceiptError,
    IngestionSagaService,
    IngestionTransitionError,
    IngestionValidationError,
    OrphanBackend,
    OrphanClassification,
    OrphanRecord,
    OrphanResolution,
    ParseReceipt,
    PublicationReceipt,
    SagaExecutionDisposition,
    SagaMilestone,
    ValidationReceipt,
    advance_saga,
    build_initial_saga,
    build_transition_event,
    classify_ingestion_failure,
    next_milestone,
    require_milestone_transition,
    verify_saga_event_chain,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    IdempotencyConflictError,
    PersistenceError,
    RequestContext,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    EXACT_BYTES_SERIALIZATION_VERSION,
    ExternalVolumeConflictError,
    ExternalVolumeIdentityError,
    ExternalVolumeOperation,
    ExternalVolumeUnavailableError,
    S3ObjectLockMode,
    SnapshotAccessDeniedError,
    SnapshotConflictError,
    SnapshotEnvelope,
    SnapshotIntegrityError,
    SnapshotServiceUnavailableError,
    SnapshotSessionError,
    SnapshotStorageEvidence,
    SnapshotStoragePlan,
)


NOW = datetime(2042, 2, 3, 4, 5, 6, tzinfo=UTC)
PAYLOAD = b"memory-patch-step10-synthetic-validation\n"
TENANT = "tenant-step10"
SOURCE = "source-step10"
SCOPE = "scope-step10-shared"
VERSION = "version-step10"
REGISTRY_DIGEST = hashlib.sha256(b"registry-step10").hexdigest()
SCOPE_DIGEST = hashlib.sha256(b"scope-step10").hexdigest()
CONTEXT = RequestContext(TENANT, None, AccessMode.TENANT_SHARED)


def make_snapshot(
    *,
    payload: bytes = PAYLOAD,
    tenant_id: str = TENANT,
) -> SnapshotEnvelope:
    return SnapshotEnvelope(
        tenant_id=tenant_id,
        source_id=SOURCE,
        hat_scope_id=SCOPE,
        payload=payload,
        serialization_version=EXACT_BYTES_SERIALIZATION_VERSION,
        media_type="application/octet-stream",
        captured_at=NOW,
        retain_until=NOW + timedelta(days=8),
        retention_mode=S3ObjectLockMode.GOVERNANCE,
        authority_metadata={"authority": "synthetic-test-only"},
        provenance_metadata={"producer": "step10-tests"},
        source_artifact_digest=sha256_hex(payload),
    )


def make_saga(
    *,
    snapshot: SnapshotEnvelope | None = None,
    idempotency_key: str = "step10-idempotency-key",
    owner_user_id: str | None = None,
) -> object:
    snapshot = snapshot or make_snapshot()
    return build_initial_saga(
        tenant_id=snapshot.tenant_id,
        source_id=snapshot.source_id,
        hat_scope_id=snapshot.hat_scope_id,
        owner_user_id=owner_user_id,
        knowledge_version_id=VERSION,
        idempotency_key=idempotency_key,
        scope_digest=SCOPE_DIGEST,
        source_registry_digest=REGISTRY_DIGEST,
        content_sha256=snapshot.content_sha256,
        content_length=snapshot.content_length,
        media_type=snapshot.media_type,
        local_relative_path=(
            f"step10/v1/{snapshot.scope_digest[:2]}/"
            f"{snapshot.snapshot_id}.bin"
        ),
        snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        retain_until=snapshot.retain_until,
        created_at=NOW,
    )


def make_intent(saga: object, kind: ExternalEffectKind) -> ExternalEffectIntent:
    return ExternalEffectIntent(
        tenant_id=saga.tenant_id,
        saga_id=saga.saga_id,
        effect_kind=kind,
        deterministic_locator=f"synthetic:{kind.value.lower()}",
        expected_snapshot_id=saga.snapshot_id,
        expected_sha256=saga.content_sha256,
        expected_length=saga.content_length,
        created_at=saga.created_at,
    )


def make_receipt(
    saga: object,
    intent: ExternalEffectIntent,
    **extra: object,
) -> ExternalEffectReceipt:
    evidence = {
        "snapshot_id": saga.snapshot_id,
        "canonical_sha256": saga.content_sha256,
        "content_length": saga.content_length,
        **extra,
    }
    return ExternalEffectReceipt(
        tenant_id=saga.tenant_id,
        saga_id=saga.saga_id,
        effect_id=intent.effect_id,
        effect_kind=intent.effect_kind,
        intent_digest=intent.intent_digest,
        evidence_digest=canonical_sha256(evidence),
        evidence=evidence,
        completed_at=NOW,
    )


class IsolationContextTests(unittest.TestCase):
    def test_shared_saga_rejects_cross_tenant_and_user_private_contexts(
        self,
    ) -> None:
        saga = make_saga()
        IngestionSagaService._require_context(CONTEXT, saga)
        with self.assertRaises(IngestionValidationError):
            IngestionSagaService._require_context(
                RequestContext(
                    "tenant-step10-other",
                    None,
                    AccessMode.TENANT_SHARED,
                ),
                saga,
            )
        with self.assertRaises(IngestionValidationError):
            IngestionSagaService._require_context(
                RequestContext(
                    TENANT,
                    "user-step10-a",
                    AccessMode.USER_PRIVATE,
                ),
                saga,
            )

    def test_private_saga_requires_exact_tenant_and_owner_user(self) -> None:
        saga = make_saga(owner_user_id="user-step10-owner")
        IngestionSagaService._require_context(
            RequestContext(
                TENANT,
                "user-step10-owner",
                AccessMode.USER_PRIVATE,
            ),
            saga,
        )
        for context in (
            RequestContext(
                TENANT,
                "user-step10-other",
                AccessMode.USER_PRIVATE,
            ),
            RequestContext(
                "tenant-step10-other",
                "user-step10-owner",
                AccessMode.USER_PRIVATE,
            ),
            CONTEXT,
        ):
            with self.subTest(context=context):
                with self.assertRaises(IngestionValidationError):
                    IngestionSagaService._require_context(context, saga)


class MemoryControl:
    """Deterministic fake of the transaction-owning application service."""

    def __init__(self) -> None:
        self.saga = None
        self.keys: dict[str, str] = {}
        self.effects: dict[ExternalEffectKind, ExternalEffectRecord] = {}
        self.events = []
        self.failures = []
        self.orphans = []

    def register_saga(self, context: object, saga: object) -> object:
        bound = self.keys.get(saga.idempotency_key)
        if bound is not None and bound != saga.run_digest:
            raise IngestionConflictError(
                sanitized_code="CONFLICTING_REPLAY"
            )
        if self.saga is not None and self.saga.run_digest != saga.run_digest:
            raise IngestionConflictError(
                sanitized_code="CONFLICTING_REPLAY"
            )
        self.keys[saga.idempotency_key] = saga.run_digest
        self.saga = self.saga or saga
        return self.saga

    def get_saga(self, context: object, **identity: object) -> object:
        return self.saga

    def claim_saga(
        self,
        context: object,
        *,
        claim_token_digest: str,
        lease_seconds: int,
        **identity: object,
    ) -> object:
        if self.saga.execution_disposition is SagaExecutionDisposition.CLAIMED:
            raise IngestionClaimError(
                sanitized_code="INGESTION_CLAIM_CONFLICT"
            )
        self.saga = replace(
            self.saga,
            execution_disposition=SagaExecutionDisposition.CLAIMED,
            state_version=self.saga.state_version + 1,
            attempt_count=self.saga.attempt_count + 1,
            claim_token_digest=claim_token_digest,
            claimed_at=NOW,
            claim_expires_at=NOW + timedelta(seconds=lease_seconds),
        )
        return self.saga

    def release_saga(self, context: object, **values: object) -> object:
        self.saga = replace(
            self.saga,
            execution_disposition=SagaExecutionDisposition.READY,
            state_version=self.saga.state_version + 1,
            claim_token_digest=None,
            claimed_at=None,
            claim_expires_at=None,
        )
        return self.saga

    def record_effect_intent(
        self,
        context: object,
        intent: ExternalEffectIntent,
    ) -> ExternalEffectRecord:
        existing = self.effects.get(intent.effect_kind)
        if existing is not None:
            if existing.intent != intent:
                raise IngestionConflictError(
                    sanitized_code="EXTERNAL_INTENT_CONFLICT"
                )
            return existing
        record = ExternalEffectRecord(
            intent,
            ExternalEffectStatus.INTENT_RECORDED,
        )
        self.effects[intent.effect_kind] = record
        return record

    def record_effect_receipt(
        self,
        context: object,
        receipt: ExternalEffectReceipt,
    ) -> ExternalEffectRecord:
        existing = self.effects[receipt.effect_kind]
        if existing.receipt is not None:
            if existing.receipt != receipt:
                raise IngestionConflictError(
                    sanitized_code="EXTERNAL_RECEIPT_CONFLICT"
                )
            return existing
        record = ExternalEffectRecord(
            existing.intent,
            ExternalEffectStatus.RECEIPT_RECORDED,
            receipt,
        )
        self.effects[receipt.effect_kind] = record
        return record

    def transition(
        self,
        context: object,
        *,
        target_milestone: SagaMilestone,
        reason_code: str,
        actor_boundary: str,
        idempotency_reference: str,
        prerequisite_receipt_digests: tuple[str, ...],
        **identity: object,
    ) -> object:
        if self.saga.current_milestone is target_milestone:
            return self.saga
        event = build_transition_event(
            self.saga,
            target_milestone=target_milestone,
            reason_code=reason_code,
            actor_boundary=actor_boundary,
            idempotency_reference=idempotency_reference,
            prerequisite_receipt_digests=prerequisite_receipt_digests,
            created_at=NOW,
        )
        self.events.append(event)
        self.saga = advance_saga(self.saga, event)
        return self.saga

    def record_failure(
        self,
        context: object,
        *,
        disposition: SagaExecutionDisposition,
        reason_code: str,
        retry_delay_seconds: int | None,
        **identity: object,
    ) -> object:
        self.failures.append((disposition, reason_code))
        self.saga = replace(
            self.saga,
            execution_disposition=disposition,
            state_version=self.saga.state_version + 1,
            claim_token_digest=None,
            claimed_at=None,
            claim_expires_at=None,
            next_retry_at=(
                NOW + timedelta(seconds=retry_delay_seconds)
                if retry_delay_seconds is not None
                else None
            ),
            quarantine_reason=(
                reason_code
                if disposition is SagaExecutionDisposition.QUARANTINED
                else None
            ),
        )
        return self.saga

    def record_orphan(self, context: object, orphan: object) -> object:
        self.orphans.append(orphan)
        return orphan


class FakeVolume:
    system_drive_fallback_allowed = False

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.writes = 0
        self.incomplete: tuple[str, ...] = ()
        self.identity_failure = False

    def verify(self, *, require_write: bool = False) -> object:
        if self.identity_failure:
            raise ExternalVolumeIdentityError(
                sanitized_code="EXTERNAL_DEVICE_IDENTITY_MISMATCH"
            )
        return SimpleNamespace(
            device_reference="external-volume-sha256:" + "a" * 64,
            authority_status="STORAGE_EVIDENCE_ONLY",
        )

    def resolve_path(self, *args: object, **kwargs: object) -> Path:
        raise AssertionError("orchestrator never needs a raw local path")

    def incomplete_atomic_artifacts(
        self,
        operation: object,
        relative_path: str,
    ) -> tuple[str, ...]:
        self.verify()
        return self.incomplete

    def read_exact(
        self,
        operation: object,
        relative_path: str,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> bytes:
        self.verify()
        if relative_path not in self.files:
            raise ExternalVolumeUnavailableError(
                sanitized_code="EXTERNAL_READ_FAILED"
            )
        payload = self.files[relative_path]
        if (
            len(payload) != expected_length
            or sha256_hex(payload) != expected_sha256
        ):
            from aioa_memory_kernel.storage import ExternalVolumeIntegrityError

            raise ExternalVolumeIntegrityError(
                sanitized_code="EXTERNAL_READBACK_MISMATCH"
            )
        return payload

    def atomic_write_exact(
        self,
        operation: object,
        relative_path: str,
        payload: bytes,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> object:
        self.verify(require_write=True)
        if relative_path in self.files:
            raise ExternalVolumeConflictError(
                sanitized_code="EXTERNAL_TARGET_EXISTS"
            )
        self.files[relative_path] = payload
        self.writes += 1
        return SimpleNamespace(
            to_dict=lambda: {
                "schema_version": "1.0.0",
                "operation": "INGESTION_STAGING",
                "relative_path": f"ingestion/downloads/{relative_path}",
                "content_sha256": expected_sha256,
                "content_length": expected_length,
                "device_reference": "external-volume-sha256:" + "a" * 64,
                "atomic_no_replace": True,
                "exact_read_back": True,
                "storage_class": "EXTERNAL_DERIVED",
                "authority_status": "STORAGE_EVIDENCE_ONLY",
                "system_drive_fallback_allowed": False,
            }
        )


class FakeAcquisition:
    def __init__(self, payload: bytes = PAYLOAD) -> None:
        self.payload = payload
        self.calls = 0

    def acquire(self, saga: object) -> bytes:
        self.calls += 1
        return self.payload


class FakeStorage:
    def __init__(self, snapshot: SnapshotEnvelope) -> None:
        self.snapshot = snapshot
        self.evidence: SnapshotStorageEvidence | None = None
        self.puts = 0
        self.verifies = 0
        self.transient_failures = 0

    def plan_snapshot(self, snapshot: SnapshotEnvelope) -> SnapshotStoragePlan:
        return SnapshotStoragePlan(
            snapshot_id=snapshot.snapshot_id,
            canonical_sha256=snapshot.content_sha256,
            content_length=snapshot.content_length,
            bucket_reference="s3-bucket-sha256:" + "b" * 64,
            object_key=f"memory-patch/snapshots/{snapshot.snapshot_id}.bin",
            retention_mode=snapshot.retention_mode,
            retain_until=snapshot.retain_until,
        )

    def reconcile_snapshot(
        self,
        snapshot: SnapshotEnvelope,
    ) -> SnapshotStorageEvidence | None:
        return self.evidence

    def persist_snapshot(
        self,
        snapshot: SnapshotEnvelope,
    ) -> SnapshotStorageEvidence:
        self.puts += 1
        if self.transient_failures:
            self.transient_failures -= 1
            raise SnapshotServiceUnavailableError(
                sanitized_code="S3_UNAVAILABLE"
            )
        self.evidence = self._evidence(content_verified=False)
        return self.evidence

    def verify_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> SnapshotStorageEvidence:
        self.verifies += 1
        if self.evidence is None or version_id != self.evidence.version_id:
            raise SnapshotIntegrityError(
                sanitized_code="S3_SNAPSHOT_NOT_FOUND"
            )
        self.evidence = self._evidence(content_verified=True)
        return self.evidence

    def _evidence(self, *, content_verified: bool) -> SnapshotStorageEvidence:
        return SnapshotStorageEvidence(
            snapshot_id=self.snapshot.snapshot_id,
            canonical_sha256=self.snapshot.content_sha256,
            content_length=self.snapshot.content_length,
            bucket_reference="s3-bucket-sha256:" + "b" * 64,
            object_key=(
                f"memory-patch/snapshots/{self.snapshot.snapshot_id}.bin"
            ),
            version_id="version-step10-1",
            retention_mode=self.snapshot.retention_mode,
            retain_until=self.snapshot.retain_until,
            checksum_sha256_base64=base64.b64encode(
                hashlib.sha256(self.snapshot.canonical_payload).digest()
            ).decode("ascii"),
            metadata_verified=True,
            content_verified=content_verified,
            idempotent_replay=self.puts == 0,
        )


class FakeParser:
    def __init__(self) -> None:
        self.receipt: ParseReceipt | None = None
        self.calls = 0
        self.wrong_snapshot = False

    def reconcile(self, saga: object, **values: object) -> ParseReceipt | None:
        return self.receipt

    def parse(
        self,
        saga: object,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt:
        self.calls += 1
        self.receipt = ParseReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=(
                "wrong-snapshot" if self.wrong_snapshot else saga.snapshot_id
            ),
            s3_version_id=s3_version_id,
            input_sha256=saga.content_sha256,
            parser_name="synthetic-step10-validation-port",
            parser_version="1.0.0",
            parser_contract_version="1.0.0",
            output_artifact_digest=canonical_sha256(
                {
                    "input": saga.content_sha256,
                    "parser": "synthetic-step10-validation-port",
                }
            ),
            completed_at=NOW,
            synthetic_validation_boundary=True,
        )
        return self.receipt


class FakeValidator:
    def __init__(self) -> None:
        self.receipt: ValidationReceipt | None = None
        self.calls = 0
        self.wrong_parse = False

    def reconcile(
        self,
        saga: object,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt | None:
        return self.receipt

    def validate(
        self,
        saga: object,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt:
        self.calls += 1
        self.receipt = ValidationReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=saga.snapshot_id,
            parse_output_digest=(
                "f" * 64
                if self.wrong_parse
                else parse_receipt.output_artifact_digest
            ),
            validator_name="synthetic-step10-validation-port",
            validator_version="1.0.0",
            validator_contract_version="1.0.0",
            accepted=True,
            reason_codes=(),
            output_artifact_digest=canonical_sha256(
                {
                    "parse": parse_receipt.output_artifact_digest,
                    "validator": "synthetic-step10-validation-port",
                }
            ),
            completed_at=NOW,
            synthetic_validation_boundary=True,
        )
        return self.receipt


class FakePublication:
    def __init__(self) -> None:
        self.receipt: PublicationReceipt | None = None
        self.calls = 0

    def reconcile(
        self,
        context: object,
        saga: object,
        validation_receipt: object,
    ) -> PublicationReceipt | None:
        return self.receipt

    def publish(
        self,
        context: object,
        saga: object,
        validation_receipt: object,
    ) -> PublicationReceipt:
        self.calls += 1
        self.receipt = PublicationReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=saga.snapshot_id,
            source_registry_digest=saga.source_registry_digest,
            publication_event_id="step9-publication-event",
            publication_event_digest=canonical_sha256(
                {"saga": saga.saga_id, "event": "PUBLISHED"}
            ),
            publication_sequence=3,
            completed_at=NOW,
        )
        return self.receipt


def make_orchestrator(
    *,
    control: MemoryControl | None = None,
    volume: FakeVolume | None = None,
    acquisition: FakeAcquisition | None = None,
    storage: FakeStorage | None = None,
    parser: FakeParser | None = None,
    validator: FakeValidator | None = None,
    publication: FakePublication | None = None,
    snapshot: SnapshotEnvelope | None = None,
) -> tuple[IngestionOrchestrator, tuple[object, ...]]:
    snapshot = snapshot or make_snapshot()
    boundaries = (
        control or MemoryControl(),
        volume or FakeVolume(),
        acquisition or FakeAcquisition(snapshot.canonical_payload),
        storage or FakeStorage(snapshot),
        parser or FakeParser(),
        validator or FakeValidator(),
        publication or FakePublication(),
    )
    orchestrator = IngestionOrchestrator(
        control=boundaries[0],
        external_volume=boundaries[1],
        acquisition=boundaries[2],
        snapshot_storage=boundaries[3],
        parser=boundaries[4],
        validator=boundaries[5],
        publication=boundaries[6],
        clock=lambda: NOW,
        token_bytes=lambda count: b"x" * count,
    )
    return orchestrator, boundaries


class StateMachineTests(unittest.TestCase):
    def test_canonical_progression_and_chain(self) -> None:
        saga = make_saga()
        events = []
        for target in MILESTONE_ORDER[1:]:
            event = build_transition_event(
                saga,
                target_milestone=target,
                reason_code=f"STEP10_{target.value}",
                actor_boundary="TEST_BOUNDARY",
                idempotency_reference=f"test:{target.value}",
                prerequisite_receipt_digests=(
                    canonical_sha256({"target": target.value}),
                ),
                created_at=NOW + timedelta(seconds=len(events)),
            )
            events.append(event)
            saga = advance_saga(saga, event)
        self.assertIs(saga.current_milestone, SagaMilestone.PUBLISHED)
        self.assertIs(
            saga.execution_disposition,
            SagaExecutionDisposition.COMPLETED,
        )
        self.assertEqual(verify_saga_event_chain(saga, events), tuple(events))

    def test_every_skip_and_backward_edge_is_rejected(self) -> None:
        for current in MILESTONE_ORDER:
            for target in MILESTONE_ORDER:
                if target is next_milestone(current):
                    continue
                with self.subTest(current=current, target=target):
                    with self.assertRaises(IngestionTransitionError):
                        require_milestone_transition(
                            current,
                            target,
                            ("a" * 64,),
                        )

    def test_each_transition_requires_exactly_one_prerequisite(self) -> None:
        for target in MILESTONE_ORDER[1:]:
            current = MILESTONE_ORDER[MILESTONE_ORDER.index(target) - 1]
            for receipts in ((), ("a" * 64, "b" * 64)):
                with self.subTest(target=target, receipts=receipts):
                    with self.assertRaises(IngestionTransitionError):
                        require_milestone_transition(
                            current,
                            target,
                            receipts,
                        )

    def test_event_digest_is_stable_and_tampering_is_rejected(self) -> None:
        saga = make_saga()
        kwargs = {
            "target_milestone": SagaMilestone.ACQUIRED_LOCAL,
            "reason_code": "STEP10_ACQUIRED_LOCAL",
            "actor_boundary": "TEST_BOUNDARY",
            "idempotency_reference": "test:acquire",
            "prerequisite_receipt_digests": ("a" * 64,),
            "created_at": NOW,
        }
        first = build_transition_event(saga, **kwargs)
        second = build_transition_event(saga, **kwargs)
        self.assertEqual(first, second)
        advanced = advance_saga(saga, first)
        tampered = copy.deepcopy(first)
        object.__setattr__(tampered, "event_digest", "f" * 64)
        with self.assertRaises(IngestionTransitionError):
            verify_saga_event_chain(advanced, (tampered,))

    def test_genesis_and_terminal_pointer_are_exact(self) -> None:
        saga = make_saga()
        self.assertEqual(saga.current_event_digest, INGESTION_GENESIS_DIGEST)
        with self.assertRaises(IngestionValidationError):
            replace(
                saga,
                execution_disposition=SagaExecutionDisposition.COMPLETED,
            )

    def test_quarantined_saga_cannot_claim_terminal_completion(self) -> None:
        saga = replace(
            make_saga(),
            execution_disposition=SagaExecutionDisposition.QUARANTINED,
            quarantine_reason="INTEGRITY_CONFLICT",
        )
        with self.assertRaises(IngestionValidationError):
            replace(
                saga,
                current_milestone=SagaMilestone.PUBLISHED,
                terminal_at=NOW,
            )


class IdentityReceiptAndFailureTests(unittest.TestCase):
    def test_saga_and_effect_identities_are_deterministic(self) -> None:
        self.assertEqual(make_saga(), make_saga())
        self.assertEqual(
            make_intent(make_saga(), ExternalEffectKind.ACQUISITION),
            make_intent(make_saga(), ExternalEffectKind.ACQUISITION),
        )

    def test_conflicting_run_digest_and_unsafe_local_path_fail(self) -> None:
        saga = make_saga()
        with self.assertRaises(IngestionValidationError):
            replace(saga, run_digest="f" * 64)
        with self.assertRaises(IngestionValidationError):
            replace(saga, local_relative_path="../escape.bin")

    def test_effect_receipt_binds_evidence_digest(self) -> None:
        saga = make_saga()
        intent = make_intent(saga, ExternalEffectKind.ACQUISITION)
        with self.assertRaises(IngestionValidationError):
            ExternalEffectReceipt(
                tenant_id=saga.tenant_id,
                saga_id=saga.saga_id,
                effect_id=intent.effect_id,
                effect_kind=intent.effect_kind,
                intent_digest=intent.intent_digest,
                evidence_digest="f" * 64,
                evidence={
                    "snapshot_id": saga.snapshot_id,
                    "canonical_sha256": saga.content_sha256,
                    "content_length": saga.content_length,
                },
                completed_at=NOW,
            )

    def test_parse_and_validation_receipts_reject_mutable_versions(self) -> None:
        saga = make_saga()
        with self.assertRaises(IngestionValidationError):
            ParseReceipt(
                tenant_id=saga.tenant_id,
                saga_id=saga.saga_id,
                source_id=saga.source_id,
                snapshot_id=saga.snapshot_id,
                s3_version_id="v1",
                input_sha256=saga.content_sha256,
                parser_name="parser",
                parser_version="latest",
                parser_contract_version="1",
                output_artifact_digest="a" * 64,
                completed_at=NOW,
            )
        with self.assertRaises(IngestionValidationError):
            ValidationReceipt(
                tenant_id=saga.tenant_id,
                saga_id=saga.saga_id,
                source_id=saga.source_id,
                snapshot_id=saga.snapshot_id,
                parse_output_digest="a" * 64,
                validator_name="validator",
                validator_version="1",
                validator_contract_version="latest",
                accepted=True,
                reason_codes=(),
                output_artifact_digest="b" * 64,
                completed_at=NOW,
            )

    def test_rejected_validation_requires_reason_and_cannot_claim_success(
        self,
    ) -> None:
        saga = make_saga()
        with self.assertRaises(IngestionValidationError):
            ValidationReceipt(
                tenant_id=saga.tenant_id,
                saga_id=saga.saga_id,
                source_id=saga.source_id,
                snapshot_id=saga.snapshot_id,
                parse_output_digest="a" * 64,
                validator_name="validator",
                validator_version="1",
                validator_contract_version="1",
                accepted=False,
                reason_codes=(),
                output_artifact_digest="b" * 64,
                completed_at=NOW,
            )

    def test_failure_classification_matrix(self) -> None:
        cases = (
            (
                IdempotencyConflictError(),
                IngestionFailureClass.CONFLICTING_REPLAY,
                False,
                True,
            ),
            (
                SnapshotSessionError(),
                IngestionFailureClass.CREDENTIALS_EXPIRED,
                False,
                False,
            ),
            (
                SnapshotAccessDeniedError(),
                IngestionFailureClass.AUTHORIZATION_FAILURE,
                False,
                False,
            ),
            (
                SnapshotServiceUnavailableError(),
                IngestionFailureClass.TRANSIENT_SERVICE,
                True,
                False,
            ),
            (
                SnapshotConflictError(),
                IngestionFailureClass.DATA_INTEGRITY_MISMATCH,
                False,
                True,
            ),
            (
                ExternalVolumeIdentityError(),
                IngestionFailureClass.UNSAFE_FILESYSTEM_IDENTITY,
                False,
                True,
            ),
            (
                PersistenceError(sqlstate="40001"),
                IngestionFailureClass.DATABASE_SERIALIZATION,
                True,
                False,
            ),
        )
        for error, failure_class, retryable, quarantine in cases:
            with self.subTest(error=type(error).__name__):
                decision = classify_ingestion_failure(error)
                self.assertIs(decision.failure_class, failure_class)
                self.assertIs(decision.retryable, retryable)
                self.assertIs(decision.quarantine_required, quarantine)

    def test_orphan_contract_is_non_destructive_and_sanitized(self) -> None:
        saga = make_saga()
        orphan = OrphanRecord(
            tenant_id=saga.tenant_id,
            orphan_id="",
            saga_id=saga.saga_id,
            backend=OrphanBackend.S3,
            deterministic_locator="s3-bucket-sha256:" + "a" * 64,
            expected_snapshot_id=saga.snapshot_id,
            observed_evidence_digest="b" * 64,
            classification=OrphanClassification.RETENTION_BLOCKED,
            resolution=OrphanResolution.CLEANUP_ELIGIBLE_AFTER_POLICY,
            reason_code="RETENTION_ACTIVE",
            retention_constraint="GOVERNANCE_ACTIVE",
            cleanup_performed=False,
            created_at=NOW,
        )
        self.assertFalse(orphan.cleanup_performed)
        with self.assertRaises(IngestionValidationError):
            replace(orphan, cleanup_performed=True)
        with self.assertRaises(IngestionValidationError):
            replace(orphan, deterministic_locator="/media/secret")


class OrchestrationTests(unittest.TestCase):
    def test_complete_path_and_exact_replay_have_no_duplicate_effects(self) -> None:
        snapshot = make_snapshot()
        saga = make_saga(snapshot=snapshot)
        orchestrator, boundaries = make_orchestrator(snapshot=snapshot)
        control, volume, acquisition, storage, parser, validator, publication = (
            boundaries
        )
        completed = orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertIs(completed.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(len(control.events), 8)
        self.assertEqual(len(control.effects), 7)
        self.assertEqual(volume.writes, 1)
        self.assertEqual(acquisition.calls, 1)
        self.assertEqual(storage.puts, 1)
        self.assertEqual(parser.calls, 1)
        self.assertEqual(validator.calls, 1)
        self.assertEqual(publication.calls, 1)
        replay = orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertEqual(replay, completed)
        self.assertEqual(volume.writes, 1)
        self.assertEqual(storage.puts, 1)
        self.assertEqual(publication.calls, 1)

    def test_existing_local_and_s3_evidence_are_reconciled_without_writes(
        self,
    ) -> None:
        snapshot = make_snapshot()
        saga = make_saga(snapshot=snapshot)
        volume = FakeVolume()
        volume.files[saga.local_relative_path] = snapshot.canonical_payload
        storage = FakeStorage(snapshot)
        storage.evidence = storage._evidence(content_verified=False)
        acquisition = FakeAcquisition(snapshot.canonical_payload)
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            volume=volume,
            storage=storage,
            acquisition=acquisition,
        )
        result = orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertIs(result.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(volume.writes, 0)
        self.assertEqual(acquisition.calls, 0)
        self.assertEqual(storage.puts, 0)

    def test_bounded_transient_s3_retry_does_not_duplicate_success(self) -> None:
        snapshot = make_snapshot()
        saga = make_saga(snapshot=snapshot)
        storage = FakeStorage(snapshot)
        storage.transient_failures = 2
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            storage=storage,
        )
        result = orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertIs(result.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(storage.puts, 3)
        self.assertEqual(storage.evidence.version_id, "version-step10-1")

    def test_active_worker_claim_conflict_does_not_clear_other_claim(
        self,
    ) -> None:
        snapshot = make_snapshot()
        claimed = replace(
            make_saga(snapshot=snapshot),
            execution_disposition=SagaExecutionDisposition.CLAIMED,
            state_version=1,
            attempt_count=1,
            claim_token_digest="d" * 64,
            claimed_at=NOW,
            claim_expires_at=NOW + timedelta(seconds=120),
        )
        control = MemoryControl()
        control.saga = claimed
        control.keys[claimed.idempotency_key] = claimed.run_digest
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            control=control,
        )
        with self.assertRaises(IngestionExecutionError) as captured:
            orchestrator.execute(CONTEXT, claimed, snapshot)
        self.assertIs(
            captured.exception.decision.failure_class,
            IngestionFailureClass.WORKER_CLAIM_CONFLICT,
        )
        self.assertTrue(captured.exception.decision.retryable)
        self.assertEqual(control.failures, [])
        self.assertEqual(control.saga, claimed)

    def test_conflicting_idempotency_replay_fails_closed(self) -> None:
        snapshot = make_snapshot()
        control = MemoryControl()
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            control=control,
        )
        saga = make_saga(snapshot=snapshot)
        orchestrator.execute(CONTEXT, saga, snapshot)
        changed_snapshot = make_snapshot(payload=b"different")
        changed = make_saga(
            snapshot=changed_snapshot,
            idempotency_key=saga.idempotency_key,
        )
        with self.assertRaises(IngestionConflictError):
            control.register_saga(CONTEXT, changed)

    def test_wrong_parse_receipt_quarantines_and_never_publishes(self) -> None:
        snapshot = make_snapshot()
        parser = FakeParser()
        parser.wrong_snapshot = True
        publication = FakePublication()
        control = MemoryControl()
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            control=control,
            parser=parser,
            publication=publication,
        )
        with self.assertRaises(IngestionExecutionError) as captured:
            orchestrator.execute(CONTEXT, make_saga(snapshot=snapshot), snapshot)
        self.assertIs(
            captured.exception.decision.failure_class,
            IngestionFailureClass.PARSE_FAILURE,
        )
        self.assertTrue(captured.exception.decision.quarantine_required)
        self.assertIs(
            control.saga.execution_disposition,
            SagaExecutionDisposition.QUARANTINED,
        )
        self.assertEqual(publication.calls, 0)

    def test_wrong_validation_binding_never_publishes(self) -> None:
        snapshot = make_snapshot()
        validator = FakeValidator()
        validator.wrong_parse = True
        publication = FakePublication()
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            validator=validator,
            publication=publication,
        )
        with self.assertRaises(IngestionExecutionError):
            orchestrator.execute(CONTEXT, make_saga(snapshot=snapshot), snapshot)
        self.assertEqual(publication.calls, 0)

    def test_mount_identity_failure_uses_no_system_drive_fallback(self) -> None:
        snapshot = make_snapshot()
        volume = FakeVolume()
        volume.identity_failure = True
        control = MemoryControl()
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            volume=volume,
            control=control,
        )
        with self.assertRaises(IngestionExecutionError) as captured:
            orchestrator.execute(CONTEXT, make_saga(snapshot=snapshot), snapshot)
        self.assertTrue(captured.exception.decision.quarantine_required)
        self.assertEqual(volume.writes, 0)

    def test_incomplete_atomic_artifact_stops_without_cleanup(self) -> None:
        snapshot = make_snapshot()
        volume = FakeVolume()
        volume.incomplete = (
            "ingestion/downloads/.aioa-step8-atomic-safe.tmp",
        )
        orchestrator, _ = make_orchestrator(
            snapshot=snapshot,
            volume=volume,
        )
        with self.assertRaises(IngestionExecutionError):
            orchestrator.execute(CONTEXT, make_saga(snapshot=snapshot), snapshot)
        self.assertEqual(volume.incomplete, (
            "ingestion/downloads/.aioa-step8-atomic-safe.tmp",
        ))
        self.assertEqual(volume.writes, 0)

    def test_storage_success_alone_cannot_publish(self) -> None:
        snapshot = make_snapshot()
        parser = FakeParser()
        parser.wrong_snapshot = True
        publication = FakePublication()
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            parser=parser,
            publication=publication,
        )
        with self.assertRaises(IngestionExecutionError):
            orchestrator.execute(CONTEXT, make_saga(snapshot=snapshot), snapshot)
        storage = boundaries[3]
        self.assertIsNotNone(storage.evidence)
        self.assertEqual(publication.calls, 0)

    def test_orchestrator_rejects_any_fallback_capable_volume(self) -> None:
        snapshot = make_snapshot()
        volume = FakeVolume()
        volume.system_drive_fallback_allowed = True
        with self.assertRaises(IngestionValidationError):
            make_orchestrator(snapshot=snapshot, volume=volume)


class StaticPersistenceAndSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql_path = (
            REPOSITORY_ROOT
            / "sql"
            / "cockroachdb"
            / "migrations"
            / "0007_step10_idempotent_ingestion_saga.sql"
        )
        cls.sql = cls.sql_path.read_text(encoding="utf-8")
        cls.manifest = json.loads(
            (
                REPOSITORY_ROOT
                / "config"
                / "cockroachdb"
                / "ingestion-saga-security-1a.json"
            ).read_text(encoding="utf-8")
        )

    def test_four_tables_are_rls_and_force_rls_protected(self) -> None:
        for table in self.manifest["tables"]:
            name = table["table"]
            with self.subTest(table=name):
                self.assertIn(
                    f"ALTER TABLE memory_patch.{name}\n"
                    "  ENABLE ROW LEVEL SECURITY;",
                    self.sql,
                )
                self.assertIn(
                    f"ALTER TABLE memory_patch.{name}\n"
                    "  FORCE ROW LEVEL SECURITY;",
                    self.sql,
                )

    def test_runtime_has_no_delete_and_append_only_tables_have_no_update(
        self,
    ) -> None:
        self.assertNotRegex(self.sql, r"GRANT[^;]*\bDELETE\b")
        self.assertNotRegex(self.sql, r"^\s*DELETE\s+FROM\b")
        by_name = {row["table"]: row for row in self.manifest["tables"]}
        for name in ("ingestion_saga_events", "ingestion_orphans"):
            self.assertEqual(
                by_name[name]["runtime_privileges"],
                ["INSERT", "SELECT"],
            )

    def test_sql_has_cas_guards_and_no_retention_bypass(self) -> None:
        for token in (
            "guard_ingestion_saga_update",
            "guard_ingestion_effect_receipt_update",
            "state_version",
            "claim_token_digest",
            "previous_event_digest",
            "ON DELETE RESTRICT",
        ):
            self.assertIn(token, self.sql)
        self.assertNotIn("BypassGovernanceRetention", self.sql)
        self.assertNotIn("BYPASSRLS", self.sql)

    def test_manifest_and_migration_hash_are_exact(self) -> None:
        migration_manifest = json.loads(
            (
                self.sql_path.parent / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        row = migration_manifest["migrations"][-1]
        self.assertEqual(
            row["migration_id"],
            "0007_step10_idempotent_ingestion_saga",
        )
        self.assertEqual(
            row["sha256"],
            hashlib.sha256(self.sql_path.read_bytes()).hexdigest(),
        )

    def test_production_imports_are_side_effect_free(self) -> None:
        modules = (
            "aioa_memory_kernel.ingestion",
            "aioa_memory_kernel.ingestion.models",
            "aioa_memory_kernel.ingestion.repository",
            "aioa_memory_kernel.ingestion.service",
            "aioa_memory_kernel.ingestion.orchestrator",
        )
        program = "\n".join(
            (
                "import importlib",
                "from unittest import mock",
                "with mock.patch('subprocess.run', "
                "side_effect=AssertionError('process during import')), "
                "mock.patch('socket.create_connection', "
                "side_effect=AssertionError('network during import')), "
                "mock.patch('pathlib.Path.write_bytes', "
                "side_effect=AssertionError('write during import')):",
                *(f"    importlib.import_module({name!r})" for name in modules),
            )
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr or completed.stdout,
        )

    def test_no_step11_parser_or_model_authority_is_implemented(self) -> None:
        root = SOURCE_ROOT / "aioa_memory_kernel" / "ingestion"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(root.glob("*.py"))
        )
        self.assertNotIn("class GenericParser", source)
        self.assertNotIn("def chunk", source)
        self.assertNotIn("embedding", source.casefold())
        self.assertNotIn("model_call", source)
        self.assertNotIn("HAT_AUTHORITY", source)

    def test_external_calls_are_guarded_outside_transactions(self) -> None:
        tree = ast.parse(
            (
                SOURCE_ROOT
                / "aioa_memory_kernel"
                / "ingestion"
                / "orchestrator.py"
            ).read_text(encoding="utf-8")
        )
        names = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "assert_no_open_persistence_transaction"
        ]
        self.assertGreaterEqual(len(names), 7)

    def test_public_evidence_contains_no_absolute_local_path(self) -> None:
        saga = make_saga()
        intent = make_intent(saga, ExternalEffectKind.ACQUISITION)
        receipt = make_receipt(
            saga,
            intent,
            relative_path=f"ingestion/downloads/{saga.local_relative_path}",
        )
        encoded = json.dumps(dict(receipt.evidence), sort_keys=True)
        self.assertNotIn("/media/", encoded)
        self.assertNotIn("/home/", encoded)


class S3PlanningAndReconciliationTests(unittest.TestCase):
    def test_storage_plan_is_pure_and_digest_bound(self) -> None:
        snapshot = make_snapshot()
        plan = SnapshotStoragePlan(
            snapshot_id=snapshot.snapshot_id,
            canonical_sha256=snapshot.content_sha256,
            content_length=snapshot.content_length,
            bucket_reference="s3-bucket-sha256:" + "a" * 64,
            object_key=f"prefix/{snapshot.snapshot_id}.bin",
            retention_mode=S3ObjectLockMode.GOVERNANCE,
            retain_until=snapshot.retain_until,
        )
        self.assertEqual(
            plan.plan_digest,
            canonical_sha256(plan, exclude_fields=("plan_digest",)),
        )
        with self.assertRaises(Exception):
            replace(plan, plan_digest="f" * 64)

    def test_s3_protocol_exposes_no_delete_or_bypass_method(self) -> None:
        from aioa_memory_kernel.storage.protocols import S3ClientProtocol

        names = set(S3ClientProtocol.__dict__)
        self.assertNotIn("delete_object", names)
        self.assertNotIn("delete_bucket", names)
        self.assertNotIn("put_object_retention", names)


if __name__ == "__main__":
    unittest.main()
