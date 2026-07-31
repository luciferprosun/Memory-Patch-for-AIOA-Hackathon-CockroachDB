"""Crash-resumable Step 10 orchestration across injected storage boundaries."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    RequestContext,
    assert_no_open_persistence_transaction,
)
from aioa_memory_kernel.storage import (
    ExternalVolumeConflictError,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeProtocol,
    ExternalVolumeUnavailableError,
    SnapshotEnvelope,
    SnapshotServiceUnavailableError,
    SnapshotStorageEvidence,
)

from .errors import (
    IngestionClaimError,
    IngestionExecutionError,
    IngestionReceiptError,
    IngestionReconciliationError,
    IngestionValidationError,
    classify_ingestion_failure,
)
from .models import (
    ExternalEffectIntent,
    ExternalEffectKind,
    ExternalEffectReceipt,
    ExternalEffectRecord,
    ExternalEffectStatus,
    IngestionSaga,
    ParseReceipt,
    PublicationReceipt,
    SagaExecutionDisposition,
    SagaMilestone,
    ValidationReceipt,
)
from .protocols import (
    AcquisitionPort,
    ParserPort,
    PlannedSnapshotStorageProtocol,
    PublicationPort,
    SagaControlProtocol,
    ValidatorPort,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _effect_evidence(
    saga: IngestionSaga,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "authority_status": "STORAGE_OR_PROCESS_EVIDENCE_ONLY",
        "canonical_sha256": saga.content_sha256,
        "content_length": saga.content_length,
        "snapshot_id": saga.snapshot_id,
        **values,
    }


def _storage_evidence(
    saga: IngestionSaga,
    evidence: SnapshotStorageEvidence,
) -> dict[str, Any]:
    if (
        evidence.snapshot_id != saga.snapshot_id
        or evidence.canonical_sha256 != saga.content_sha256
        or evidence.content_length != saga.content_length
    ):
        raise IngestionReceiptError(
            "S3 evidence differs from immutable saga facts",
            sanitized_code="S3_RECEIPT_BINDING_MISMATCH",
        )
    return _effect_evidence(
        saga,
        {
            "bucket_reference": evidence.bucket_reference,
            "checksum_sha256_base64": evidence.checksum_sha256_base64,
            "content_verified": evidence.content_verified,
            "evidence_digest": evidence.evidence_digest,
            "idempotent_replay": evidence.idempotent_replay,
            "metadata_verified": evidence.metadata_verified,
            "object_key": evidence.object_key,
            "retain_until": evidence.retain_until.isoformat(),
            "retention_mode": evidence.retention_mode.value,
            "storage_authority_status": evidence.authority_status,
            "version_id": evidence.version_id,
        },
    )


class IngestionOrchestrator:
    """Advance one saga without claiming a distributed ACID transaction."""

    def __init__(
        self,
        *,
        control: SagaControlProtocol,
        external_volume: ExternalVolumeRuntimeProtocol,
        snapshot_storage: PlannedSnapshotStorageProtocol,
        acquisition: AcquisitionPort,
        parser: ParserPort,
        validator: ValidatorPort,
        publication: PublicationPort,
        clock: Callable[[], datetime] = _utc_now,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
        lease_seconds: int = 120,
        s3_attempt_limit: int = 3,
    ) -> None:
        required = {
            "control": (
                control,
                (
                    "register_saga",
                    "get_saga",
                    "claim_saga",
                    "record_effect_intent",
                    "record_effect_receipt",
                    "transition",
                    "record_failure",
                ),
            ),
            "external_volume": (
                external_volume,
                (
                    "verify",
                    "read_exact",
                    "incomplete_atomic_artifacts",
                    "atomic_write_exact",
                ),
            ),
            "snapshot_storage": (
                snapshot_storage,
                (
                    "plan_snapshot",
                    "reconcile_snapshot",
                    "persist_snapshot",
                    "verify_snapshot",
                ),
            ),
            "acquisition": (acquisition, ("acquire",)),
            "parser": (parser, ("parse", "reconcile")),
            "validator": (validator, ("validate", "reconcile")),
            "publication": (publication, ("publish", "reconcile")),
        }
        for boundary, (value, methods) in required.items():
            if any(not callable(getattr(value, method, None)) for method in methods):
                raise IngestionValidationError(
                    f"{boundary} does not implement the narrow Step 10 port",
                    sanitized_code="INVALID_INGESTION_PORT",
                )
        if (
            not callable(clock)
            or not callable(token_bytes)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 300
            or not isinstance(s3_attempt_limit, int)
            or not 1 <= s3_attempt_limit <= 5
        ):
            raise IngestionValidationError(
                "orchestrator retry or lease configuration is invalid",
                sanitized_code="INVALID_INGESTION_ORCHESTRATOR_CONFIG",
            )
        if external_volume.system_drive_fallback_allowed:
            raise IngestionValidationError(
                "external-volume fallback must remain disabled",
                sanitized_code="SYSTEM_DRIVE_FALLBACK_FORBIDDEN",
            )
        self._control = control
        self._volume = external_volume
        self._storage = snapshot_storage
        self._acquisition = acquisition
        self._parser = parser
        self._validator = validator
        self._publication = publication
        self._clock = clock
        self._token_bytes = token_bytes
        self._lease_seconds = lease_seconds
        self._s3_attempt_limit = s3_attempt_limit

    def execute(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        """Register or resume one exact saga until terminal or typed failure."""

        self._validate_snapshot_binding(saga, snapshot)
        current = self._control.register_saga(context, saga)
        while current.current_milestone is not SagaMilestone.PUBLISHED:
            if current.execution_disposition in {
                SagaExecutionDisposition.QUARANTINED,
                SagaExecutionDisposition.OPERATOR_REVIEW,
            }:
                return current
            worker_nonce = self._token_bytes(32)
            if not isinstance(worker_nonce, bytes) or len(worker_nonce) != 32:
                raise IngestionValidationError(
                    "worker claim entropy source returned invalid bytes",
                    sanitized_code="INVALID_WORKER_CLAIM_NONCE",
                )
            claim_digest = canonical_sha256(
                {
                    "saga_id": current.saga_id,
                    "worker_nonce_hex": worker_nonce.hex(),
                }
            )
            try:
                current = self._control.claim_saga(
                    context,
                    tenant_id=current.tenant_id,
                    saga_id=current.saga_id,
                    claim_token_digest=claim_digest,
                    lease_seconds=self._lease_seconds,
                )
                current = self._advance_one(
                    context,
                    current,
                    snapshot,
                )
            except Exception as error:
                decision = classify_ingestion_failure(error)
                disposition = (
                    SagaExecutionDisposition.QUARANTINED
                    if decision.quarantine_required
                    else (
                        SagaExecutionDisposition.RETRY_WAIT
                        if decision.retryable
                        else SagaExecutionDisposition.OPERATOR_REVIEW
                    )
                )
                if not isinstance(error, IngestionClaimError):
                    try:
                        self._control.record_failure(
                            context,
                            tenant_id=current.tenant_id,
                            saga_id=current.saga_id,
                            disposition=disposition,
                            reason_code=decision.sanitized_code,
                            retry_delay_seconds=(
                                30 if decision.retryable else None
                            ),
                        )
                    except Exception:
                        pass
                if isinstance(error, IngestionExecutionError):
                    raise
                raise IngestionExecutionError(
                    "ingestion saga stopped at a typed failure boundary",
                    decision=decision,
                ) from error
            observed = self._control.get_saga(
                context,
                tenant_id=current.tenant_id,
                saga_id=current.saga_id,
            )
            if observed is None:
                raise IngestionValidationError(
                    "durable saga disappeared after a transition",
                    sanitized_code="INGESTION_SAGA_NOT_FOUND",
                )
            current = observed
        return current

    @staticmethod
    def _validate_snapshot_binding(
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> None:
        if (
            snapshot.tenant_id != saga.tenant_id
            or snapshot.source_id != saga.source_id
            or snapshot.hat_scope_id != saga.hat_scope_id
            or snapshot.snapshot_id != saga.snapshot_id
            or snapshot.content_sha256 != saga.content_sha256
            or snapshot.content_length != saga.content_length
            or snapshot.media_type != saga.media_type
            or snapshot.captured_at != saga.captured_at
            or snapshot.retain_until != saga.retain_until
        ):
            raise IngestionValidationError(
                "snapshot envelope differs from immutable saga facts",
                sanitized_code="INGESTION_SNAPSHOT_BINDING_MISMATCH",
            )

    def _advance_one(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        handlers = {
            SagaMilestone.REGISTERED: self._acquire_local,
            SagaMilestone.ACQUIRED_LOCAL: self._verify_hash,
            SagaMilestone.HASH_VERIFIED: self._record_upload_pending,
            SagaMilestone.SNAPSHOT_UPLOAD_PENDING: self._upload_snapshot,
            SagaMilestone.SNAPSHOT_UPLOADED: self._verify_lock,
            SagaMilestone.SNAPSHOT_LOCK_VERIFIED: self._parse,
            SagaMilestone.PARSED: self._validate,
            SagaMilestone.VALIDATED: self._publish,
        }
        handler = handlers.get(saga.current_milestone)
        if handler is None:
            raise IngestionValidationError(
                "saga has no executable successor",
                sanitized_code="INVALID_INGESTION_MILESTONE",
            )
        return handler(context, saga, snapshot)

    def _intent(
        self,
        saga: IngestionSaga,
        effect_kind: ExternalEffectKind,
        locator: str,
    ) -> ExternalEffectIntent:
        return ExternalEffectIntent(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            effect_kind=effect_kind,
            deterministic_locator=locator,
            expected_snapshot_id=saga.snapshot_id,
            expected_sha256=saga.content_sha256,
            expected_length=saga.content_length,
            created_at=saga.created_at,
        )

    def _record_intent(
        self,
        context: RequestContext,
        intent: ExternalEffectIntent,
    ) -> ExternalEffectRecord:
        return self._control.record_effect_intent(context, intent)

    def _receipt(
        self,
        context: RequestContext,
        record: ExternalEffectRecord,
        evidence: Mapping[str, Any],
        *,
        completed_at: datetime | None = None,
    ) -> ExternalEffectRecord:
        if (
            record.status is ExternalEffectStatus.RECEIPT_RECORDED
            and record.receipt is not None
        ):
            return record
        frozen_evidence = dict(evidence)
        receipt = ExternalEffectReceipt(
            tenant_id=record.intent.tenant_id,
            saga_id=record.intent.saga_id,
            effect_id=record.intent.effect_id,
            effect_kind=record.intent.effect_kind,
            intent_digest=record.intent.intent_digest,
            evidence_digest=canonical_sha256(frozen_evidence),
            evidence=frozen_evidence,
            completed_at=completed_at or self._clock(),
        )
        return self._control.record_effect_receipt(context, receipt)

    def _transition(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        target: SagaMilestone,
        prerequisite_digest: str,
    ) -> IngestionSaga:
        return self._control.transition(
            context,
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            target_milestone=target,
            reason_code=f"STEP10_{target.value}",
            actor_boundary="MEMORY_PATCH_INGESTION_SAGA",
            idempotency_reference=(
                f"{saga.idempotency_key}:milestone:{target.value.lower()}"
            ),
            prerequisite_receipt_digests=(prerequisite_digest,),
        )

    def _acquire_local(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        intent = self._intent(
            saga,
            ExternalEffectKind.ACQUISITION,
            f"external-volume:INGESTION_STAGING:{saga.local_relative_path}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            assert_no_open_persistence_transaction()
            incomplete = self._volume.incomplete_atomic_artifacts(
                ExternalVolumeOperation.INGESTION_STAGING,
                saga.local_relative_path,
            )
            if incomplete:
                raise IngestionReconciliationError(
                    "target-bound incomplete atomic evidence requires review",
                    sanitized_code="INCOMPLETE_LOCAL_STAGING_ARTIFACT",
                )
            reconciled = False
            try:
                payload = self._volume.read_exact(
                    ExternalVolumeOperation.INGESTION_STAGING,
                    saga.local_relative_path,
                    expected_sha256=saga.content_sha256,
                    expected_length=saga.content_length,
                )
                status = self._volume.verify(require_write=False)
                evidence = _effect_evidence(
                    saga,
                    {
                        "atomic_no_replace": True,
                        "device_reference": status.device_reference,
                        "exact_read_back": True,
                        "operation": "INGESTION_STAGING",
                        "relative_path": (
                            f"ingestion/downloads/{saga.local_relative_path}"
                        ),
                        "reconciled_existing": True,
                        "storage_authority_status": status.authority_status,
                        "system_drive_fallback_allowed": False,
                    },
                )
                reconciled = True
            except ExternalVolumeUnavailableError as error:
                if error.sanitized_code != "EXTERNAL_READ_FAILED":
                    raise
                assert_no_open_persistence_transaction()
                payload = self._acquisition.acquire(saga)
                if not isinstance(payload, bytes):
                    raise IngestionReceiptError(
                        "acquisition boundary did not return immutable bytes",
                        sanitized_code="INVALID_ACQUISITION_PAYLOAD",
                    )
                write = self._volume.atomic_write_exact(
                    ExternalVolumeOperation.INGESTION_STAGING,
                    saga.local_relative_path,
                    payload,
                    expected_sha256=saga.content_sha256,
                    expected_length=saga.content_length,
                )
                evidence = _effect_evidence(
                    saga,
                    {
                        **write.to_dict(),
                        "reconciled_existing": False,
                    },
                )
            if payload != snapshot.canonical_payload:
                raise IngestionReceiptError(
                    "acquired local bytes differ from snapshot envelope",
                    sanitized_code="LOCAL_SNAPSHOT_BYTES_MISMATCH",
                )
            record = self._receipt(context, record, evidence)
            if reconciled and record.receipt is None:
                raise AssertionError("reconciled receipt was not recorded")
        assert record.receipt is not None
        return self._transition(
            context,
            saga,
            SagaMilestone.ACQUIRED_LOCAL,
            record.receipt.receipt_digest,
        )

    def _verify_hash(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        intent = self._intent(
            saga,
            ExternalEffectKind.HASH_VERIFICATION,
            f"external-volume:INGESTION_STAGING:{saga.local_relative_path}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            assert_no_open_persistence_transaction()
            payload = self._volume.read_exact(
                ExternalVolumeOperation.INGESTION_STAGING,
                saga.local_relative_path,
                expected_sha256=saga.content_sha256,
                expected_length=saga.content_length,
            )
            if payload != snapshot.canonical_payload:
                raise IngestionReceiptError(
                    "hash read-back differs from exact snapshot bytes",
                    sanitized_code="LOCAL_HASH_VERIFICATION_MISMATCH",
                )
            evidence = _effect_evidence(
                saga,
                {
                    "exact_read_back": True,
                    "manifest_sha256": snapshot.manifest_sha256,
                    "operation": "HASH_VERIFICATION",
                    "relative_path": (
                        f"ingestion/downloads/{saga.local_relative_path}"
                    ),
                    "system_drive_fallback_allowed": False,
                },
            )
            record = self._receipt(context, record, evidence)
        assert record.receipt is not None
        return self._transition(
            context,
            saga,
            SagaMilestone.HASH_VERIFIED,
            record.receipt.receipt_digest,
        )

    def _record_upload_pending(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        assert_no_open_persistence_transaction()
        plan = self._storage.plan_snapshot(snapshot)
        intent = self._intent(
            saga,
            ExternalEffectKind.S3_UPLOAD,
            f"s3:{plan.bucket_reference}:{plan.object_key}",
        )
        record = self._record_intent(context, intent)
        return self._transition(
            context,
            saga,
            SagaMilestone.SNAPSHOT_UPLOAD_PENDING,
            record.intent.intent_digest,
        )

    def _upload_snapshot(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        plan = self._storage.plan_snapshot(snapshot)
        intent = self._intent(
            saga,
            ExternalEffectKind.S3_UPLOAD,
            f"s3:{plan.bucket_reference}:{plan.object_key}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            evidence: SnapshotStorageEvidence | None = None
            for attempt in range(1, self._s3_attempt_limit + 1):
                assert_no_open_persistence_transaction()
                evidence = self._storage.reconcile_snapshot(snapshot)
                if evidence is not None:
                    break
                try:
                    evidence = self._storage.persist_snapshot(snapshot)
                    break
                except SnapshotServiceUnavailableError:
                    assert_no_open_persistence_transaction()
                    evidence = self._storage.reconcile_snapshot(snapshot)
                    if evidence is not None:
                        break
                    if attempt >= self._s3_attempt_limit:
                        raise
            if evidence is None:
                raise IngestionReconciliationError(
                    "bounded S3 upload did not produce exact evidence",
                    sanitized_code="S3_UPLOAD_RESULT_AMBIGUOUS",
                )
            record = self._receipt(
                context,
                record,
                _storage_evidence(saga, evidence),
            )
        assert record.receipt is not None
        return self._transition(
            context,
            saga,
            SagaMilestone.SNAPSHOT_UPLOADED,
            record.receipt.receipt_digest,
        )

    def _verify_lock(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        upload_plan = self._storage.plan_snapshot(snapshot)
        upload_record = self._record_intent(
            context,
            self._intent(
                saga,
                ExternalEffectKind.S3_UPLOAD,
                f"s3:{upload_plan.bucket_reference}:{upload_plan.object_key}",
            ),
        )
        if upload_record.receipt is None:
            raise IngestionReceiptError(
                "S3 lock verification requires an upload receipt",
                sanitized_code="S3_UPLOAD_RECEIPT_MISSING",
            )
        version_id = upload_record.receipt.evidence.get("version_id")
        if not isinstance(version_id, str) or not version_id:
            raise IngestionReceiptError(
                "S3 upload receipt has no exact version ID",
                sanitized_code="MISSING_S3_VERSION_ID",
            )
        intent = self._intent(
            saga,
            ExternalEffectKind.S3_LOCK_VERIFICATION,
            f"s3-version:{upload_plan.bucket_reference}:{version_id}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            assert_no_open_persistence_transaction()
            evidence = self._storage.verify_snapshot(
                snapshot,
                version_id=version_id,
            )
            if not (
                evidence.metadata_verified
                and evidence.content_verified
                and evidence.version_id == version_id
                and evidence.retain_until == saga.retain_until
            ):
                raise IngestionReceiptError(
                    "S3 exact version lacks complete lock and byte evidence",
                    sanitized_code="S3_LOCK_VERIFICATION_FAILURE",
                )
            record = self._receipt(
                context,
                record,
                _storage_evidence(saga, evidence),
            )
        assert record.receipt is not None
        return self._transition(
            context,
            saga,
            SagaMilestone.SNAPSHOT_LOCK_VERIFIED,
            record.receipt.receipt_digest,
        )

    def _locked_s3_evidence(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> tuple[str, str]:
        plan = self._storage.plan_snapshot(snapshot)
        upload = self._record_intent(
            context,
            self._intent(
                saga,
                ExternalEffectKind.S3_UPLOAD,
                f"s3:{plan.bucket_reference}:{plan.object_key}",
            ),
        )
        if upload.receipt is None:
            raise IngestionReceiptError(
                "locked snapshot requires an upload receipt",
                sanitized_code="S3_UPLOAD_RECEIPT_MISSING",
            )
        version_id = upload.receipt.evidence.get("version_id")
        if not isinstance(version_id, str) or not version_id:
            raise IngestionReceiptError(
                "locked snapshot requires an exact version ID",
                sanitized_code="MISSING_S3_VERSION_ID",
            )
        lock = self._record_intent(
            context,
            self._intent(
                saga,
                ExternalEffectKind.S3_LOCK_VERIFICATION,
                f"s3-version:{plan.bucket_reference}:{version_id}",
            ),
        )
        if lock.receipt is None:
            raise IngestionReceiptError(
                "locked snapshot receipt is missing",
                sanitized_code="S3_LOCK_RECEIPT_MISSING",
            )
        return version_id, lock.receipt.evidence_digest

    def _parse(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        version_id, lock_digest = self._locked_s3_evidence(
            context,
            saga,
            snapshot,
        )
        intent = self._intent(
            saga,
            ExternalEffectKind.PARSE,
            f"parser-input:{version_id}:{lock_digest}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            assert_no_open_persistence_transaction()
            parsed = self._parser.reconcile(
                saga,
                s3_version_id=version_id,
                locked_storage_evidence_digest=lock_digest,
            )
            if parsed is None:
                parsed = self._parser.parse(
                    saga,
                    s3_version_id=version_id,
                    locked_storage_evidence_digest=lock_digest,
                )
            self._validate_parse_receipt(saga, parsed, version_id)
            evidence = _effect_evidence(
                saga,
                {
                    "output_artifact_digest": parsed.output_artifact_digest,
                    "parser_contract_version": parsed.parser_contract_version,
                    "parser_name": parsed.parser_name,
                    "parser_version": parsed.parser_version,
                    "receipt_digest": parsed.receipt_digest,
                    "s3_version_id": parsed.s3_version_id,
                    "synthetic_validation_boundary": (
                        parsed.synthetic_validation_boundary
                    ),
                },
            )
            record = self._receipt(
                context,
                record,
                evidence,
                completed_at=parsed.completed_at,
            )
        assert record.receipt is not None
        return self._transition(
            context,
            saga,
            SagaMilestone.PARSED,
            record.receipt.receipt_digest,
        )

    @staticmethod
    def _validate_parse_receipt(
        saga: IngestionSaga,
        receipt: ParseReceipt,
        version_id: str,
    ) -> None:
        if (
            not isinstance(receipt, ParseReceipt)
            or receipt.tenant_id != saga.tenant_id
            or receipt.saga_id != saga.saga_id
            or receipt.source_id != saga.source_id
            or receipt.snapshot_id != saga.snapshot_id
            or receipt.s3_version_id != version_id
            or receipt.input_sha256 != saga.content_sha256
        ):
            raise IngestionReceiptError(
                "parse receipt is bound to a different locked snapshot",
                sanitized_code="PARSE_RECEIPT_BINDING_MISMATCH",
            )

    def _parse_receipt_from_effect(
        self,
        saga: IngestionSaga,
        effect: ExternalEffectRecord,
    ) -> ParseReceipt:
        if effect.receipt is None:
            raise IngestionReceiptError(
                "parse effect receipt is missing",
                sanitized_code="PARSE_RECEIPT_MISSING",
            )
        evidence = effect.receipt.evidence
        try:
            return ParseReceipt(
                tenant_id=saga.tenant_id,
                saga_id=saga.saga_id,
                source_id=saga.source_id,
                snapshot_id=saga.snapshot_id,
                s3_version_id=str(evidence["s3_version_id"]),
                input_sha256=saga.content_sha256,
                parser_name=str(evidence["parser_name"]),
                parser_version=str(evidence["parser_version"]),
                parser_contract_version=str(
                    evidence["parser_contract_version"]
                ),
                output_artifact_digest=str(
                    evidence["output_artifact_digest"]
                ),
                synthetic_validation_boundary=bool(
                    evidence["synthetic_validation_boundary"]
                ),
                completed_at=effect.receipt.completed_at,
                receipt_digest=str(evidence["receipt_digest"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IngestionReceiptError(
                "stored parse receipt evidence is malformed",
                sanitized_code="PARSE_RECEIPT_MALFORMED",
            ) from exc

    def _validate(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        version_id, lock_digest = self._locked_s3_evidence(
            context,
            saga,
            snapshot,
        )
        parse_effect = self._record_intent(
            context,
            self._intent(
                saga,
                ExternalEffectKind.PARSE,
                f"parser-input:{version_id}:{lock_digest}",
            ),
        )
        parsed = self._parse_receipt_from_effect(saga, parse_effect)
        intent = self._intent(
            saga,
            ExternalEffectKind.VALIDATION,
            f"validator-input:{parsed.output_artifact_digest}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            assert_no_open_persistence_transaction()
            validated = self._validator.reconcile(saga, parsed)
            if validated is None:
                validated = self._validator.validate(saga, parsed)
            self._validate_validation_receipt(saga, parsed, validated)
            evidence = _effect_evidence(
                saga,
                {
                    "accepted": validated.accepted,
                    "output_artifact_digest": (
                        validated.output_artifact_digest
                    ),
                    "parse_output_digest": validated.parse_output_digest,
                    "reason_codes": validated.reason_codes,
                    "receipt_digest": validated.receipt_digest,
                    "synthetic_validation_boundary": (
                        validated.synthetic_validation_boundary
                    ),
                    "validator_contract_version": (
                        validated.validator_contract_version
                    ),
                    "validator_name": validated.validator_name,
                    "validator_version": validated.validator_version,
                },
            )
            record = self._receipt(
                context,
                record,
                evidence,
                completed_at=validated.completed_at,
            )
        assert record.receipt is not None
        if record.receipt.evidence.get("accepted") is not True:
            raise IngestionReceiptError(
                "rejected validation cannot advance the saga",
                sanitized_code="VALIDATION_REJECTED",
            )
        return self._transition(
            context,
            saga,
            SagaMilestone.VALIDATED,
            record.receipt.receipt_digest,
        )

    @staticmethod
    def _validate_validation_receipt(
        saga: IngestionSaga,
        parsed: ParseReceipt,
        receipt: ValidationReceipt,
    ) -> None:
        if (
            not isinstance(receipt, ValidationReceipt)
            or not receipt.accepted
            or receipt.tenant_id != saga.tenant_id
            or receipt.saga_id != saga.saga_id
            or receipt.source_id != saga.source_id
            or receipt.snapshot_id != saga.snapshot_id
            or receipt.parse_output_digest != parsed.output_artifact_digest
        ):
            raise IngestionReceiptError(
                "validation receipt is not bound to the parse output",
                sanitized_code="VALIDATION_RECEIPT_BINDING_MISMATCH",
            )

    def _validation_receipt_from_effect(
        self,
        saga: IngestionSaga,
        effect: ExternalEffectRecord,
    ) -> ValidationReceipt:
        if effect.receipt is None:
            raise IngestionReceiptError(
                "validation effect receipt is missing",
                sanitized_code="VALIDATION_RECEIPT_MISSING",
            )
        evidence = effect.receipt.evidence
        try:
            return ValidationReceipt(
                tenant_id=saga.tenant_id,
                saga_id=saga.saga_id,
                source_id=saga.source_id,
                snapshot_id=saga.snapshot_id,
                parse_output_digest=str(evidence["parse_output_digest"]),
                validator_name=str(evidence["validator_name"]),
                validator_version=str(evidence["validator_version"]),
                validator_contract_version=str(
                    evidence["validator_contract_version"]
                ),
                accepted=bool(evidence["accepted"]),
                reason_codes=tuple(evidence["reason_codes"]),
                output_artifact_digest=str(
                    evidence["output_artifact_digest"]
                ),
                synthetic_validation_boundary=bool(
                    evidence["synthetic_validation_boundary"]
                ),
                completed_at=effect.receipt.completed_at,
                receipt_digest=str(evidence["receipt_digest"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IngestionReceiptError(
                "stored validation receipt evidence is malformed",
                sanitized_code="VALIDATION_RECEIPT_MALFORMED",
            ) from exc

    def _publish(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        snapshot: SnapshotEnvelope,
    ) -> IngestionSaga:
        version_id, lock_digest = self._locked_s3_evidence(
            context,
            saga,
            snapshot,
        )
        parsed = self._parse_receipt_from_effect(
            saga,
            self._record_intent(
                context,
                self._intent(
                    saga,
                    ExternalEffectKind.PARSE,
                    f"parser-input:{version_id}:{lock_digest}",
                ),
            ),
        )
        validation_effect = self._record_intent(
            context,
            self._intent(
                saga,
                ExternalEffectKind.VALIDATION,
                f"validator-input:{parsed.output_artifact_digest}",
            ),
        )
        validated = self._validation_receipt_from_effect(
            saga,
            validation_effect,
        )
        intent = self._intent(
            saga,
            ExternalEffectKind.PUBLICATION,
            f"source-registry:{saga.source_id}:{saga.source_registry_digest}",
        )
        record = self._record_intent(context, intent)
        if record.receipt is None:
            assert_no_open_persistence_transaction()
            publication = self._publication.reconcile(
                context,
                saga,
                validated,
            )
            if publication is None:
                publication = self._publication.publish(
                    context,
                    saga,
                    validated,
                )
            self._validate_publication_receipt(saga, publication)
            evidence = _effect_evidence(
                saga,
                {
                    "publication_event_digest": (
                        publication.publication_event_digest
                    ),
                    "publication_event_id": publication.publication_event_id,
                    "publication_sequence": publication.publication_sequence,
                    "receipt_digest": publication.receipt_digest,
                    "source_registry_digest": (
                        publication.source_registry_digest
                    ),
                },
            )
            record = self._receipt(
                context,
                record,
                evidence,
                completed_at=publication.completed_at,
            )
        assert record.receipt is not None
        return self._transition(
            context,
            saga,
            SagaMilestone.PUBLISHED,
            record.receipt.receipt_digest,
        )

    @staticmethod
    def _validate_publication_receipt(
        saga: IngestionSaga,
        receipt: PublicationReceipt,
    ) -> None:
        if (
            not isinstance(receipt, PublicationReceipt)
            or receipt.tenant_id != saga.tenant_id
            or receipt.saga_id != saga.saga_id
            or receipt.source_id != saga.source_id
            or receipt.snapshot_id != saga.snapshot_id
            or receipt.source_registry_digest != saga.source_registry_digest
        ):
            raise IngestionReceiptError(
                "publication receipt is not bound to the Step 9 source event",
                sanitized_code="PUBLICATION_RECEIPT_BINDING_MISMATCH",
            )
