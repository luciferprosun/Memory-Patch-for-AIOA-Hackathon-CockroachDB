from __future__ import annotations

import math
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from tests._support import (
    NOW,
    RUN_A,
    TENANT_A,
    USER_A,
    make_evidence,
    make_personal_proposal,
)
from aioa_memory_kernel.contracts import (
    ActorType,
    AuditEvent,
    CONTRACT_SCHEMA_VERSION,
    ContractValidationError,
    EvidenceBundle,
    EvidenceStatus,
    IntegrityError,
    KnowledgeRoute,
    approval_proof_hash,
    build_audit_event,
    canonical_json,
    canonical_sha256,
    deduplicate_audit_events,
    normalize_utc_timestamp,
    verify_audit_chain,
    verify_evidence_bundle_hash,
)


class CanonicalSerializationTests(unittest.TestCase):
    def test_canonical_json_is_deterministic_across_mapping_order(self) -> None:
        left = {"z": 2, "a": {"y": 1, "x": 0}}
        right = {"a": {"x": 0, "y": 1}, "z": 2}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))

    def test_stable_enum_serialization(self) -> None:
        self.assertEqual(
            canonical_json({"route": KnowledgeRoute.HAT_ENFORCE}),
            '{"route":"HAT_ENFORCE"}',
        )

    def test_utc_timestamp_normalization(self) -> None:
        plus_two = datetime(
            2030, 1, 2, 14, 0, tzinfo=timezone(timedelta(hours=2))
        )
        self.assertEqual(
            normalize_utc_timestamp(plus_two),
            "2030-01-02T12:00:00.000000Z",
        )
        self.assertEqual(canonical_json({"at": plus_two}), canonical_json({"at": NOW}))

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            canonical_json({"at": datetime(2030, 1, 1)})

    def test_nan_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            canonical_json({"value": math.nan})

    def test_infinity_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            canonical_json({"value": math.inf})

    def test_non_string_mapping_keys_are_rejected_deterministically(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "mapping keys must be strings"
        ):
            canonical_json({"valid": 1, 2: "invalid"})

    def test_sets_are_sorted_deterministically(self) -> None:
        self.assertEqual(
            canonical_json({"values": {"z", "a", "m"}}),
            '{"values":["a","m","z"]}',
        )

    def test_own_hash_field_can_be_excluded(self) -> None:
        one = {"record_id": "r1", "content_hash": "one"}
        two = {"record_id": "r1", "content_hash": "two"}
        self.assertEqual(
            canonical_sha256(one, exclude_fields=("content_hash",)),
            canonical_sha256(two, exclude_fields=("content_hash",)),
        )

    def test_machine_specific_absolute_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "machine-specific absolute path"
        ):
            canonical_json({"local_path": "/home/example/private-record"})

    def test_evidence_bundle_hash_is_stable(self) -> None:
        evidence = make_evidence()
        first = EvidenceBundle(
            evidence_bundle_id="bundle-1",
            kernel_run_id=RUN_A,
            hat_id="synthetic-software-version",
            evidence_status=EvidenceStatus.SUFFICIENT,
            ordered_items=(evidence,),
            retrieval_policy_version="synthetic-1",
            created_at=NOW,
        )
        second = EvidenceBundle(
            evidence_bundle_id="bundle-1",
            kernel_run_id=RUN_A,
            hat_id="synthetic-software-version",
            evidence_status=EvidenceStatus.SUFFICIENT,
            ordered_items=(evidence,),
            retrieval_policy_version="synthetic-1",
            created_at=NOW,
        )
        self.assertEqual(first.bundle_hash, second.bundle_hash)
        verify_evidence_bundle_hash(first)

    def test_proposal_hash_changes_after_content_mutation(self) -> None:
        first = make_personal_proposal(content={"statement": "first"})
        second = make_personal_proposal(content={"statement": "second"})
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_proposal_hash_ignores_lifecycle_cursor(self) -> None:
        proposal = make_personal_proposal()
        proposed = replace(proposal, lifecycle_state=proposal.lifecycle_state)
        self.assertEqual(proposal.content_hash, proposed.content_hash)

    def test_approval_proof_is_deterministic(self) -> None:
        kwargs = {
            "approval_id": "approval-1",
            "proposal_id": "proposal-1",
            "proposal_hash": "a" * 64,
            "tenant_id": TENANT_A,
            "owner_user_id": USER_A,
            "personal_memory_space_id": "space-alpha-1",
            "decision": "APPROVE",
            "approver_type": "USER",
            "approver_id": USER_A,
            "reason_code": "SYNTHETIC_REVIEW_COMPLETE",
            "decided_at": NOW,
        }
        self.assertEqual(approval_proof_hash(**kwargs), approval_proof_hash(**kwargs))


class AuditHashChainTests(unittest.TestCase):
    def _event(
        self,
        event_id: str,
        sequence: int,
        previous: AuditEvent | None,
        created_at: datetime,
    ) -> AuditEvent:
        return build_audit_event(
            audit_event_id=event_id,
            tenant_id=TENANT_A,
            user_id=USER_A,
            kernel_run_id=RUN_A,
            event_type="SYNTHETIC_TRANSITION",
            sequence_number=sequence,
            previous_event=previous,
            resource_type="SyntheticRecord",
            resource_id="record-1",
            state_before=None if sequence == 0 else "BEFORE",
            state_after="AFTER",
            actor_type=ActorType.SYSTEM,
            actor_id="contract-validator",
            content_hashes={"record": "a" * 64},
            created_at=created_at,
        )

    def test_audit_hash_chain_is_deterministic(self) -> None:
        first_a = self._event("audit-1", 0, None, NOW)
        second_a = self._event("audit-2", 1, first_a, NOW + timedelta(seconds=1))
        first_b = self._event("audit-1", 0, None, NOW)
        second_b = self._event("audit-2", 1, first_b, NOW + timedelta(seconds=1))
        self.assertEqual(first_a.event_hash, first_b.event_hash)
        self.assertEqual(second_a.event_hash, second_b.event_hash)
        verify_audit_chain((first_a, second_a))

    def test_broken_audit_link_is_rejected(self) -> None:
        first = self._event("audit-1", 0, None, NOW)
        broken = AuditEvent(
            schema_version=CONTRACT_SCHEMA_VERSION,
            audit_event_id="audit-2",
            tenant_id=TENANT_A,
            user_id=USER_A,
            kernel_run_id=RUN_A,
            event_type="SYNTHETIC_TRANSITION",
            sequence_number=1,
            previous_event_hash="b" * 64,
            resource_type="SyntheticRecord",
            resource_id="record-1",
            state_before="BEFORE",
            state_after="AFTER",
            actor_type=ActorType.SYSTEM,
            actor_id="contract-validator",
            content_hashes={"record": "a" * 64},
            created_at=NOW + timedelta(seconds=1),
        )
        with self.assertRaises(IntegrityError):
            verify_audit_chain((first, broken))

    def test_at_least_once_audit_export_deduplicates_by_id(self) -> None:
        first = self._event("audit-1", 0, None, NOW)
        self.assertEqual(deduplicate_audit_events((first, first)), (first,))

    def test_duplicate_id_with_different_content_fails(self) -> None:
        first = self._event("audit-1", 0, None, NOW)
        changed = self._event(
            "audit-1", 0, None, NOW + timedelta(seconds=1)
        )
        with self.assertRaises(IntegrityError):
            deduplicate_audit_events((first, changed))

    def test_raw_payload_cannot_be_embedded_as_data_uri(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "referenced, not embedded"
        ):
            AuditEvent(
                schema_version=CONTRACT_SCHEMA_VERSION,
                audit_event_id="audit-private",
                tenant_id=TENANT_A,
                user_id=USER_A,
                kernel_run_id=RUN_A,
                event_type="PRIVATE_PAYLOAD",
                sequence_number=0,
                previous_event_hash=None,
                resource_type="ProtectedPayload",
                resource_id="payload-1",
                state_before=None,
                state_after="REFERENCED",
                actor_type=ActorType.SYSTEM,
                actor_id="contract-validator",
                content_hashes={"payload": "a" * 64},
                created_at=NOW.replace(tzinfo=UTC),
                protected_payload_reference="data:text/plain,private",
            )


if __name__ == "__main__":
    unittest.main()
