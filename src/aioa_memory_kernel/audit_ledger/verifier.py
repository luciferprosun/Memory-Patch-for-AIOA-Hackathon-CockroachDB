"""Deterministic, read-only Step 33 hash-chain verification."""

from __future__ import annotations

from collections.abc import Sequence

from aioa_memory_kernel.contracts.exceptions import IntegrityError
from .models import (
    STEP33_GENESIS_SENTINEL,
    STEP33_VERIFICATION_POLICY_DIGEST,
    STEP33_VERIFICATION_POLICY_ID,
    STEP33_VERIFICATION_POLICY_VERSION,
    AuditChainHead,
    AuditChainVerificationResult,
    AuditLedgerEntry,
    AuditReasonCode,
    verify_audit_event_envelope,
)


def _result(
    chain_id: str,
    entries: Sequence[AuditLedgerEntry],
    reasons: set[AuditReasonCode],
    failure_sequence: int | None,
) -> AuditChainVerificationResult:
    return AuditChainVerificationResult(
        chain_id=chain_id,
        event_count=len(entries),
        first_sequence=entries[0].envelope.sequence_number if entries else None,
        last_sequence=entries[-1].envelope.sequence_number if entries else None,
        first_hash=entries[0].envelope.event_hash if entries else None,
        last_hash=entries[-1].envelope.event_hash if entries else None,
        verified=not reasons,
        failure_sequence=failure_sequence,
        failure_reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        verification_policy_id=STEP33_VERIFICATION_POLICY_ID,
        verification_policy_version=STEP33_VERIFICATION_POLICY_VERSION,
        verification_policy_digest=STEP33_VERIFICATION_POLICY_DIGEST,
    )


def verify_audit_chain(
    chain_id: str,
    entries: Sequence[AuditLedgerEntry],
    *,
    predecessor_hash: str | None = None,
    expected_head: AuditChainHead | None = None,
) -> AuditChainVerificationResult:
    """Verify a complete chain or an anchored contiguous range.

    Verification is observational only.  It never updates a chain head or
    repairs a damaged ledger row.
    """

    frozen = tuple(entries)
    reasons: set[AuditReasonCode] = set()
    failure_sequence: int | None = None
    seen_sequences: set[int] = set()
    seen_event_ids: set[str] = set()
    expected_sequence = frozen[0].envelope.sequence_number if frozen else 1
    expected_previous = (
        STEP33_GENESIS_SENTINEL
        if expected_sequence == 1
        else predecessor_hash
    )
    if expected_sequence > 1 and expected_previous is None:
        reasons.add(AuditReasonCode.AUDIT_PREVIOUS_HASH_MISMATCH)
    if not frozen:
        reasons.add(AuditReasonCode.AUDIT_GENESIS_INVALID)
        failure_sequence = 1

    for entry in frozen:
        envelope = entry.envelope
        current_sequence = envelope.sequence_number
        if envelope.chain_id != chain_id:
            reasons.add(AuditReasonCode.AUDIT_EVENT_INVALID)
        if current_sequence in seen_sequences:
            reasons.add(AuditReasonCode.AUDIT_SEQUENCE_DUPLICATE)
        seen_sequences.add(current_sequence)
        if envelope.event_id in seen_event_ids:
            reasons.add(AuditReasonCode.AUDIT_EVENT_INVALID)
        seen_event_ids.add(envelope.event_id)
        if current_sequence != expected_sequence:
            reasons.add(AuditReasonCode.AUDIT_SEQUENCE_GAP)
        if expected_previous is None or envelope.previous_event_hash != expected_previous:
            reasons.add(
                AuditReasonCode.AUDIT_GENESIS_INVALID
                if current_sequence == 1
                else AuditReasonCode.AUDIT_PREVIOUS_HASH_MISMATCH
            )
        try:
            verify_audit_event_envelope(envelope)
            # Reconstructing the entry verifies payload and append receipt.
            AuditLedgerEntry(
                envelope=envelope,
                event_payload=entry.event_payload,
                append_receipt=entry.append_receipt,
            )
        except IntegrityError as exc:
            message = str(exc).lower()
            if "payload" in message:
                reasons.add(AuditReasonCode.AUDIT_PAYLOAD_DIGEST_MISMATCH)
            else:
                reasons.add(AuditReasonCode.AUDIT_EVENT_HASH_MISMATCH)
        except Exception:
            reasons.add(AuditReasonCode.AUDIT_EVENT_INVALID)
        if reasons and failure_sequence is None:
            failure_sequence = current_sequence
        expected_sequence = current_sequence + 1
        expected_previous = envelope.event_hash

    if expected_head is not None:
        if (
            expected_head.chain_id != chain_id
            or expected_head.head_version != expected_head.last_sequence
            or (
                frozen
                and (
                    expected_head.last_sequence != frozen[-1].envelope.sequence_number
                    or expected_head.last_event_hash != frozen[-1].envelope.event_hash
                )
            )
            or (
                not frozen
                and (
                    expected_head.last_sequence != 0
                    or expected_head.last_event_hash != STEP33_GENESIS_SENTINEL
                )
            )
        ):
            reasons.add(AuditReasonCode.AUDIT_CHAIN_HEAD_MISMATCH)
            if failure_sequence is None:
                failure_sequence = (
                    frozen[-1].envelope.sequence_number if frozen else 1
                )

    return _result(chain_id, frozen, reasons, failure_sequence)


__all__ = ["STEP33_VERIFICATION_POLICY_DIGEST", "verify_audit_chain"]
