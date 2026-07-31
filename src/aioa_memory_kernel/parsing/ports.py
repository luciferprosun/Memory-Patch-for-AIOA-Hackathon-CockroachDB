"""Concrete production Step 10 parser and validator ports for Step 11."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.ingestion import IngestionSaga, ParseReceipt, ValidationReceipt
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    assert_no_open_persistence_transaction,
)
from aioa_memory_kernel.storage import SnapshotEnvelope, SnapshotStorageProtocol

from .errors import ParsingQuarantineError, ParsingValidationError
from .models import LanguageTag, VALIDATOR_CONTRACT_VERSION
from .pipeline import GenericParsingPipeline, ParsingRequest
from .protocols import ParsingPersistenceProtocol, SnapshotEnvelopeResolverProtocol
from .validation import ParseArtifactValidator


PARSER_RECEIPT_NAME = "generic-deterministic-parser"
PARSER_RECEIPT_VERSION = "1.0.0"
VALIDATOR_NAME = "generic-parse-artifact-structural-validator"
VALIDATOR_VERSION = "1.0.0"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _context(saga: IngestionSaga) -> RequestContext:
    return RequestContext(
        tenant_id=saga.tenant_id,
        user_id=saga.owner_user_id,
        access_mode=(
            AccessMode.TENANT_SHARED
            if saga.owner_user_id is None
            else AccessMode.USER_PRIVATE
        ),
    )


def _assert_saga_snapshot(saga: IngestionSaga, snapshot: SnapshotEnvelope) -> None:
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
        raise ParsingValidationError(
            "snapshot manifest differs from immutable saga facts",
            sanitized_code="PARSE_RECEIPT_BINDING_MISMATCH",
        )


class GenericParsingPipelinePort:
    """Retrieve one exact locked version, parse outside SQL, then persist."""

    def __init__(
        self,
        *,
        snapshot_storage: SnapshotStorageProtocol,
        snapshot_resolver: SnapshotEnvelopeResolverProtocol,
        persistence: ParsingPersistenceProtocol,
        pipeline: GenericParsingPipeline | None = None,
        clock: Callable[[], datetime] = _utc_now,
        version_ordinal: Callable[[IngestionSaga], int] = lambda saga: 1,
        language_tag: Callable[[IngestionSaga], LanguageTag | None] = lambda saga: None,
    ) -> None:
        if any(
            not callable(value)
            for value in (snapshot_resolver, clock, version_ordinal, language_tag)
        ) or not callable(getattr(snapshot_storage, "retrieve_snapshot", None)):
            raise ParsingValidationError(
                "parser port dependencies do not implement the narrow contract",
                sanitized_code="INVALID_PARSING_PORT",
            )
        if not callable(getattr(persistence, "persist", None)) or not callable(
            getattr(persistence, "get_by_saga", None)
        ):
            raise ParsingValidationError(
                "parser persistence dependency is invalid",
                sanitized_code="INVALID_PARSING_PORT",
            )
        self._storage = snapshot_storage
        self._snapshot_resolver = snapshot_resolver
        self._persistence = persistence
        self._pipeline = pipeline or GenericParsingPipeline()
        self._clock = clock
        self._version_ordinal = version_ordinal
        self._language_tag = language_tag

    @staticmethod
    def _receipt(artifact: object) -> ParseReceipt:
        document = artifact.document  # type: ignore[attr-defined]
        return ParseReceipt(
            tenant_id=document.tenant_id,
            saga_id=document.saga_id,
            source_id=document.source_id,
            snapshot_id=document.snapshot_id,
            s3_version_id=document.s3_version_id,
            input_sha256=document.input_sha256,
            parser_name=document.parser_name,
            parser_version=document.parser_version,
            parser_contract_version=document.parser_contract_version,
            output_artifact_digest=document.parse_artifact_digest,
            completed_at=document.completed_at,
            synthetic_validation_boundary=False,
        )

    @staticmethod
    def _assert_binding(
        saga: IngestionSaga,
        artifact: object,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> None:
        document = artifact.document  # type: ignore[attr-defined]
        if (
            document.tenant_id != saga.tenant_id
            or document.owner_user_id != saga.owner_user_id
            or document.saga_id != saga.saga_id
            or document.source_id != saga.source_id
            or document.snapshot_id != saga.snapshot_id
            or document.knowledge_version_id != saga.knowledge_version_id
            or document.hat_scope_id != saga.hat_scope_id
            or document.s3_version_id != s3_version_id
            or document.locked_storage_evidence_digest
            != locked_storage_evidence_digest
            or document.input_sha256 != saga.content_sha256
            or document.input_byte_length != saga.content_length
            or document.media_type != saga.media_type
        ):
            raise ParsingValidationError(
                "parse artifact differs from exact saga and lock binding",
                sanitized_code="PARSE_RECEIPT_BINDING_MISMATCH",
            )

    def parse(
        self,
        saga: IngestionSaga,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt:
        assert_no_open_persistence_transaction()
        snapshot = self._snapshot_resolver(saga)
        if not isinstance(snapshot, SnapshotEnvelope):
            raise ParsingValidationError(
                "snapshot resolver returned an invalid envelope",
                sanitized_code="PARSE_RECEIPT_BINDING_MISMATCH",
            )
        _assert_saga_snapshot(saga, snapshot)
        retrieved = self._storage.retrieve_snapshot(
            snapshot,
            version_id=s3_version_id,
        )
        evidence = retrieved.evidence
        if (
            evidence.snapshot_id != saga.snapshot_id
            or evidence.version_id != s3_version_id
            or evidence.canonical_sha256 != saga.content_sha256
            or evidence.content_length != saga.content_length
            or evidence.retention_mode is not snapshot.retention_mode
            or evidence.retain_until != snapshot.retain_until
            or not evidence.metadata_verified
            or not evidence.content_verified
            or retrieved.payload != snapshot.canonical_payload
        ):
            raise ParsingValidationError(
                "retrieved S3 version lacks exact locked snapshot binding",
                sanitized_code="PARSE_RECEIPT_BINDING_MISMATCH",
            )
        completed_at = self._clock()
        artifact = self._pipeline.parse(
            ParsingRequest(
                tenant_id=saga.tenant_id,
                owner_user_id=saga.owner_user_id,
                saga_id=saga.saga_id,
                source_id=saga.source_id,
                snapshot_id=saga.snapshot_id,
                knowledge_version_id=saga.knowledge_version_id,
                knowledge_version_ordinal=self._version_ordinal(saga),
                hat_scope_id=saga.hat_scope_id,
                s3_version_id=s3_version_id,
                locked_storage_evidence_digest=locked_storage_evidence_digest,
                input_sha256=saga.content_sha256,
                input_byte_length=saga.content_length,
                media_type=saga.media_type,
                completed_at=completed_at,
                language_tag=self._language_tag(saga),
            ),
            retrieved.payload,
        )
        if artifact.quarantine.required:
            raise ParsingQuarantineError(
                "blocking deterministic finding requires quarantine",
                sanitized_code=artifact.quarantine.reason_codes[0],
            )
        persisted = self._persistence.persist(_context(saga), artifact)
        self._assert_binding(
            saga,
            persisted,
            s3_version_id=s3_version_id,
            locked_storage_evidence_digest=locked_storage_evidence_digest,
        )
        return self._receipt(persisted)

    def reconcile(
        self,
        saga: IngestionSaga,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt | None:
        assert_no_open_persistence_transaction()
        artifact = self._persistence.get_by_saga(
            _context(saga),
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
        )
        if artifact is None:
            return None
        self._assert_binding(
            saga,
            artifact,
            s3_version_id=s3_version_id,
            locked_storage_evidence_digest=locked_storage_evidence_digest,
        )
        validation = ParseArtifactValidator().validate(artifact)
        if not validation.accepted:
            raise ParsingValidationError(
                "persisted artifact does not reconcile structurally",
                sanitized_code=validation.reason_codes[0],
            )
        return self._receipt(artifact)


class GenericParseArtifactValidatorPort:
    """Deterministically validate persisted parse output; never infer semantics."""

    def __init__(
        self,
        *,
        persistence: ParsingPersistenceProtocol,
        validator: ParseArtifactValidator | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(getattr(persistence, "get_by_saga", None)) or not callable(clock):
            raise ParsingValidationError(
                "validator port dependencies are invalid",
                sanitized_code="INVALID_PARSING_PORT",
            )
        self._persistence = persistence
        self._validator = validator or ParseArtifactValidator()
        self._clock = clock

    def _validate_binding(
        self,
        saga: IngestionSaga,
        receipt: ParseReceipt,
    ) -> object:
        if (
            not isinstance(receipt, ParseReceipt)
            or receipt.tenant_id != saga.tenant_id
            or receipt.saga_id != saga.saga_id
            or receipt.source_id != saga.source_id
            or receipt.snapshot_id != saga.snapshot_id
            or receipt.input_sha256 != saga.content_sha256
            or receipt.synthetic_validation_boundary
        ):
            raise ParsingValidationError(
                "parse receipt differs from the saga binding",
                sanitized_code="VALIDATION_RECEIPT_BINDING_MISMATCH",
            )
        artifact = self._persistence.get_by_saga(
            _context(saga),
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
        )
        if (
            artifact is None
            or artifact.document.parse_artifact_digest
            != receipt.output_artifact_digest
            or artifact.document.s3_version_id != receipt.s3_version_id
        ):
            raise ParsingValidationError(
                "parse receipt lacks complete matching persistence",
                sanitized_code="VALIDATION_RECEIPT_BINDING_MISMATCH",
            )
        return artifact

    def validate(
        self,
        saga: IngestionSaga,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt:
        assert_no_open_persistence_transaction()
        artifact = self._validate_binding(saga, parse_receipt)
        result = self._validator.validate(artifact)  # type: ignore[arg-type]
        validation_digest = canonical_sha256(
            {
                "contract": VALIDATOR_CONTRACT_VERSION,
                "parse_artifact_digest": result.parse_artifact_digest,
                "accepted": result.accepted,
                "reason_codes": result.reason_codes,
                "tenant_id": saga.tenant_id,
                "saga_id": saga.saga_id,
                "source_id": saga.source_id,
                "snapshot_id": saga.snapshot_id,
            }
        )
        return ValidationReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=saga.snapshot_id,
            parse_output_digest=parse_receipt.output_artifact_digest,
            validator_name=VALIDATOR_NAME,
            validator_version=VALIDATOR_VERSION,
            validator_contract_version=VALIDATOR_CONTRACT_VERSION,
            accepted=result.accepted,
            reason_codes=result.reason_codes,
            output_artifact_digest=validation_digest,
            completed_at=(
                artifact.document.completed_at + timedelta(microseconds=1)  # type: ignore[attr-defined]
            ),
            synthetic_validation_boundary=False,
        )

    def reconcile(
        self,
        saga: IngestionSaga,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt | None:
        assert_no_open_persistence_transaction()
        return self.validate(saga, parse_receipt)


__all__ = [
    "GenericParseArtifactValidatorPort",
    "GenericParsingPipelinePort",
    "PARSER_RECEIPT_NAME",
    "PARSER_RECEIPT_VERSION",
    "VALIDATOR_NAME",
    "VALIDATOR_VERSION",
]
