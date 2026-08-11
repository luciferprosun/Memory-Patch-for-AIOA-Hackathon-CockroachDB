"""Typed append, verification, and owner-export boundary for Step 33."""

from __future__ import annotations

from typing import Protocol

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose

from .models import (
    AuditActorType,
    AuditEventDraft,
    AuditExportBundle,
    AuditExportEvent,
    AuditExportRangeProof,
    AuditExportRequest,
    AuditLedgerEntry,
    AuditLedgerError,
    AuditReasonCode,
    AuditRedactionPolicy,
    AuditRedactionProfile,
    build_audit_event_envelope,
    build_audit_ledger_entry,
    compute_audit_chain_id,
    verify_audit_event_draft,
)
from .repository import AuditLedgerCockroachRepository
from .verifier import verify_audit_chain


class AuditLedgerPort(Protocol):
    def append_event(
        self,
        draft: AuditEventDraft,
        *,
        authenticated_tenant_id: str,
        authenticated_actor_type: AuditActorType,
        authenticated_actor_id: str,
    ) -> tuple[AuditLedgerEntry, bool]: ...

    def verify_chain(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        authenticated_tenant_id: str,
        authenticated_owner_user_id: str,
    ): ...

    def export_chain(
        self,
        request: AuditExportRequest,
        *,
        authenticated_tenant_id: str,
        authenticated_owner_user_id: str,
    ) -> AuditExportBundle: ...


def _context(tenant_id: str, owner_user_id: str | None) -> RequestContext:
    if owner_user_id is None:
        return RequestContext(
            tenant_id=tenant_id,
            user_id=None,
            access_mode=AccessMode.TENANT_SHARED,
        )
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


class AuditLedgerService:
    """Short serializable operations with no business-state authority."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        reader_transaction_runner: SerializableTransactionRunner | None = None,
        repository: AuditLedgerCockroachRepository | None = None,
        redaction_policy: AuditRedactionPolicy | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        transaction_runner.require_credential_purpose(
            CredentialPurpose.AUDIT_APPENDER_DATABASE
        )
        if reader_transaction_runner is not None:
            if not isinstance(
                reader_transaction_runner, SerializableTransactionRunner
            ):
                raise TypeError(
                    "reader_transaction_runner must be SerializableTransactionRunner"
                )
            reader_transaction_runner.require_credential_purpose(
                CredentialPurpose.AUDIT_READER_DATABASE
            )
        self._append_runner = transaction_runner
        self._reader_runner = reader_transaction_runner
        self._repository = repository or AuditLedgerCockroachRepository()
        self._redaction_policy = redaction_policy or AuditRedactionPolicy()

    def append_event(
        self,
        draft: AuditEventDraft,
        *,
        authenticated_tenant_id: str,
        authenticated_actor_type: AuditActorType,
        authenticated_actor_id: str,
    ) -> tuple[AuditLedgerEntry, bool]:
        if not isinstance(draft, AuditEventDraft):
            raise TypeError("draft must be AuditEventDraft")
        verify_audit_event_draft(draft)
        if authenticated_tenant_id != draft.tenant_id:
            raise AuditLedgerError(AuditReasonCode.AUDIT_TENANT_MISMATCH)
        if (
            authenticated_actor_type is not draft.actor_type
            or authenticated_actor_id != draft.actor_id
        ):
            raise AuditLedgerError(AuditReasonCode.AUDIT_EVENT_INVALID)
        if (
            draft.actor_type is AuditActorType.HUMAN_USER
            and draft.owner_user_id != draft.actor_id
        ):
            raise AuditLedgerError(AuditReasonCode.AUDIT_OWNER_MISMATCH)
        chain_id = compute_audit_chain_id(draft.tenant_id, draft.owner_user_id)

        def work(transaction):
            replay = self._repository.get_replay(
                transaction,
                tenant_id=draft.tenant_id,
                chain_id=chain_id,
                idempotency_key=draft.idempotency_key,
            )
            if replay is not None:
                if replay.envelope.draft_hash != draft.draft_hash:
                    raise AuditLedgerError(
                        AuditReasonCode.AUDIT_EVENT_REPLAY_CONFLICT
                    )
                return replay, True
            head = self._repository.lock_chain_head(
                transaction,
                tenant_id=draft.tenant_id,
                owner_user_id=draft.owner_user_id,
                updated_at=draft.recorded_at,
            )
            # A competing transaction may have committed this idempotency
            # identity while this transaction waited for the chain-head lock.
            # Recheck under the serialized head before allocating a sequence.
            replay = self._repository.get_replay(
                transaction,
                tenant_id=draft.tenant_id,
                chain_id=chain_id,
                idempotency_key=draft.idempotency_key,
            )
            if replay is not None:
                if replay.envelope.draft_hash != draft.draft_hash:
                    raise AuditLedgerError(
                        AuditReasonCode.AUDIT_EVENT_REPLAY_CONFLICT
                    )
                return replay, True
            event = build_audit_event_envelope(
                draft,
                sequence_number=head.last_sequence + 1,
                previous_event_hash=head.last_event_hash,
            )
            entry = build_audit_ledger_entry(event, draft.event_payload)
            self._repository.insert_entry(transaction, entry)
            self._repository.advance_chain_head(
                transaction, current=head, entry=entry
            )
            return entry, False

        return self._append_runner.run(
            _context(draft.tenant_id, draft.owner_user_id),
            work,
            operation_kind="STEP33_AUDIT_APPEND",
        )

    def verify_chain(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        authenticated_tenant_id: str,
        authenticated_owner_user_id: str,
    ):
        if self._reader_runner is None:
            raise AuditLedgerError(AuditReasonCode.AUDIT_REVIEWER_UNAUTHORIZED)
        if tenant_id != authenticated_tenant_id:
            raise AuditLedgerError(AuditReasonCode.AUDIT_TENANT_MISMATCH)
        if owner_user_id != authenticated_owner_user_id:
            raise AuditLedgerError(AuditReasonCode.AUDIT_OWNER_MISMATCH)
        chain_id = compute_audit_chain_id(tenant_id, owner_user_id)

        def work(transaction):
            head = self._repository.get_chain_head(
                transaction,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                chain_id=chain_id,
            )
            if head is None:
                raise AuditLedgerError(AuditReasonCode.AUDIT_EVENT_INVALID)
            if head.last_sequence > 10_000:
                raise AuditLedgerError(AuditReasonCode.AUDIT_EXPORT_TRUNCATED)
            entries = self._repository.load_range(
                transaction,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                chain_id=chain_id,
                start_sequence=1,
                end_sequence=head.last_sequence,
                maximum_events=max(1, head.last_sequence),
            )
            return verify_audit_chain(chain_id, entries, expected_head=head)

        return self._reader_runner.run(
            _context(tenant_id, owner_user_id),
            work,
            operation_kind="STEP33_AUDIT_VERIFY",
        )

    def export_chain(
        self,
        request: AuditExportRequest,
        *,
        authenticated_tenant_id: str,
        authenticated_owner_user_id: str,
    ) -> AuditExportBundle:
        if self._reader_runner is None:
            raise AuditLedgerError(AuditReasonCode.AUDIT_REVIEWER_UNAUTHORIZED)
        if not isinstance(request, AuditExportRequest):
            raise TypeError("request must be AuditExportRequest")
        if request.tenant_id != authenticated_tenant_id:
            raise AuditLedgerError(AuditReasonCode.AUDIT_TENANT_MISMATCH)
        if (
            request.owner_user_id != authenticated_owner_user_id
            or request.requester_id != authenticated_owner_user_id
        ):
            raise AuditLedgerError(AuditReasonCode.AUDIT_EXPORT_SCOPE_DENIED)
        if request.event_types:
            # V1 exports are contiguous proof ranges.  A sparse event-type
            # filter would need additional skip proofs and is fail-closed.
            raise AuditLedgerError(AuditReasonCode.AUDIT_EVENT_INVALID)

        def work(transaction):
            chain_id = request.chain_ids[0]
            predecessor = self._repository.predecessor_hash(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                chain_id=chain_id,
                start_sequence=request.start_sequence,
            )
            if predecessor is None:
                raise AuditLedgerError(
                    AuditReasonCode.AUDIT_PREVIOUS_HASH_MISMATCH
                )
            loaded = self._repository.load_range(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                chain_id=chain_id,
                start_sequence=request.start_sequence,
                end_sequence=request.end_sequence,
                maximum_events=request.maximum_events,
            )
            truncated = len(loaded) > request.maximum_events
            entries = loaded[: request.maximum_events]
            if not entries:
                raise AuditLedgerError(AuditReasonCode.AUDIT_EVENT_INVALID)
            verification = verify_audit_chain(
                chain_id, entries, predecessor_hash=predecessor
            )
            if not verification.verified:
                raise AuditLedgerError(AuditReasonCode.AUDIT_CHAIN_BROKEN)
            export_events: list[AuditExportEvent] = []
            for entry in entries:
                hash_only = request.redaction_profile is AuditRedactionProfile.HASH_ONLY
                export_events.append(
                    AuditExportEvent(
                        envelope=entry.envelope,
                        payload_representation={}
                        if hash_only
                        else entry.event_payload,
                        original_payload_digest=entry.envelope.event_payload_digest,
                        redaction_profile=request.redaction_profile,
                        redacted=hash_only,
                        redaction_marker=(
                            "HASH_ONLY_ORIGINAL_DIGEST_PRESERVED"
                            if hash_only
                            else "SAFE_METADATA_POLICY_APPLIED"
                        ),
                    )
                )
            last = entries[-1].envelope
            continuation = (
                "audit-continuation-"
                + canonical_sha256(
                    {
                        "request_hash": request.request_hash,
                        "chain_id": chain_id,
                        "last_sequence": last.sequence_number,
                    }
                )
                if truncated
                else None
            )
            proof = AuditExportRangeProof(
                chain_id=chain_id,
                start_sequence=entries[0].envelope.sequence_number,
                end_sequence=last.sequence_number,
                predecessor_hash=predecessor,
                first_event_hash=entries[0].envelope.event_hash,
                last_event_hash=last.event_hash,
            )
            return AuditExportBundle(
                schema_version="audit-export-bundle-1.0.0",
                request_hash=request.request_hash,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                ordered_events=tuple(export_events),
                range_proofs=(proof,),
                verification_results=(verification,),
                redaction_policy_id=self._redaction_policy.policy_id,
                redaction_policy_version=self._redaction_policy.policy_version,
                redaction_policy_digest=self._redaction_policy.policy_digest,
                exported_event_count=len(entries),
                truncated=truncated,
                continuation_token=continuation,
                exported_at=request.requested_at,
            )

        return self._reader_runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind="STEP33_AUDIT_EXPORT",
        )


__all__ = ["AuditLedgerPort", "AuditLedgerService"]
