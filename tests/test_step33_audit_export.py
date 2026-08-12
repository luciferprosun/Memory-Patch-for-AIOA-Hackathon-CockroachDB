"""Step 33 bounded owner audit-export, range-proof, and redaction tests."""

from __future__ import annotations

import dataclasses
import json
import unittest

from tests._support import REPOSITORY_ROOT
from tests.test_step33_audit_ledger import MemoryRepository, MemoryRunner, draft

from aioa_memory_kernel.audit_ledger import (
    MAX_AUDIT_EXPORT_EVENTS,
    AuditActorType,
    AuditEventType,
    AuditExportBundle,
    AuditExportEvent,
    AuditExportRequest,
    AuditLedgerError,
    AuditLedgerService,
    AuditReasonCode,
    AuditRedactionProfile,
    audit_to_jsonb,
    compute_audit_chain_id,
    verify_audit_chain,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.security.credentials import CredentialPurpose


ROOT = REPOSITORY_ROOT


def populated_service(count: int = 6):
    repository = MemoryRepository()
    service = AuditLedgerService(
        MemoryRunner(CredentialPurpose.AUDIT_APPENDER_DATABASE),
        reader_transaction_runner=MemoryRunner(
            CredentialPurpose.AUDIT_READER_DATABASE
        ),
        repository=repository,
    )
    for index in range(1, count + 1):
        service.append_event(
            draft(index),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
            authenticated_actor_id="audit-system-policy-1a",
        )
    return service, repository


def request(*, maximum: int = 1000, start: int = 1, profile=AuditRedactionProfile.HASH_ONLY):
    return AuditExportRequest(
        tenant_id="audit-tenant-a",
        requester_actor_type=AuditActorType.HUMAN_USER,
        requester_id="audit-owner-a",
        owner_user_id="audit-owner-a",
        chain_ids=(compute_audit_chain_id("audit-tenant-a", "audit-owner-a"),),
        start_sequence=start,
        end_sequence=None,
        maximum_events=maximum,
        redaction_profile=profile,
        requested_at=draft(100).recorded_at,
    )


class Step33ExportContractTests(unittest.TestCase):
    def test_owner_export_is_deterministic_ordered_and_proof_carrying(self):
        service, _ = populated_service()
        first = service.export_chain(
            request(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        second = service.export_chain(
            request(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        self.assertEqual(first, second)
        self.assertEqual(first.exported_event_count, 6)
        self.assertEqual(
            [item.envelope.sequence_number for item in first.ordered_events],
            list(range(1, 7)),
        )
        self.assertEqual(first.range_proofs[0].predecessor_hash, first.ordered_events[0].envelope.previous_event_hash)
        self.assertTrue(first.verification_results[0].verified)
        self.assertFalse(first.business_authority)
        self.assertTrue(first.owner_private)

    def test_hash_only_redaction_preserves_original_digest_and_chain_hash(self):
        service, repository = populated_service(2)
        bundle = service.export_chain(
            request(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        for exported, stored in zip(bundle.ordered_events, repository.entries):
            self.assertTrue(exported.redacted)
            self.assertEqual(dict(exported.payload_representation), {})
            self.assertEqual(
                exported.original_payload_digest,
                stored.envelope.event_payload_digest,
            )
            self.assertEqual(exported.envelope.event_hash, stored.envelope.event_hash)
            self.assertIn("ORIGINAL_DIGEST_PRESERVED", exported.redaction_marker)
        rendered = json.dumps(audit_to_jsonb(bundle), sort_keys=True).lower()
        for forbidden in (
            "api_key",
            "authorization:",
            "bearer ",
            "password",
            "aws_secret",
            "github_token",
            "/home/",
            "/media/",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_safe_metadata_profile_never_changes_original_proof(self):
        service, repository = populated_service(2)
        bundle = service.export_chain(
            request(profile=AuditRedactionProfile.SAFE_METADATA),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        self.assertFalse(bundle.ordered_events[0].redacted)
        self.assertEqual(
            dict(bundle.ordered_events[0].payload_representation),
            dict(repository.entries[0].event_payload),
        )
        reconstructed = verify_audit_chain(
            bundle.ordered_events[0].envelope.chain_id,
            tuple(repository.entries),
        )
        self.assertTrue(reconstructed.verified)
        with self.assertRaises(ContractValidationError):
            AuditExportEvent(
                envelope=bundle.ordered_events[0].envelope,
                payload_representation={"decision": "CHANGED"},
                original_payload_digest=(
                    bundle.ordered_events[0].original_payload_digest
                ),
                redaction_profile=AuditRedactionProfile.SAFE_METADATA,
                redacted=False,
                redaction_marker="SAFE_METADATA_POLICY_APPLIED",
            )

    def test_range_export_has_predecessor_anchor(self):
        service, repository = populated_service(5)
        bundle = service.export_chain(
            request(start=3),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        proof = bundle.range_proofs[0]
        self.assertEqual(proof.start_sequence, 3)
        self.assertEqual(proof.predecessor_hash, repository.entries[1].envelope.event_hash)
        self.assertTrue(bundle.verification_results[0].verified)

    def test_truncation_and_continuation_are_stable(self):
        service, _ = populated_service(6)
        bundle = service.export_chain(
            request(maximum=2),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        replay = service.export_chain(
            request(maximum=2),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        self.assertTrue(bundle.truncated)
        self.assertEqual(bundle.exported_event_count, 2)
        self.assertEqual(bundle.continuation_token, replay.continuation_token)
        self.assertEqual(bundle.bundle_hash, replay.bundle_hash)

    def test_export_bounds_and_sparse_filter_fail_closed(self):
        with self.assertRaises(ContractValidationError):
            request(maximum=0)
        with self.assertRaises(ContractValidationError):
            request(maximum=MAX_AUDIT_EXPORT_EVENTS + 1)
        with self.assertRaises(ContractValidationError):
            request(maximum=True)
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(request(), start_sequence=True)
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(request(), end_sequence=True)
        service, _ = populated_service()
        filtered = dataclasses.replace(
            request(), event_types=(AuditEventType.POLICY_BLOCKED,)
        )
        with self.assertRaises(AuditLedgerError):
            service.export_chain(
                filtered,
                authenticated_tenant_id="audit-tenant-a",
                authenticated_owner_user_id="audit-owner-a",
            )


class Step33ExportIsolationTests(unittest.TestCase):
    def test_cross_user_and_requester_mismatch_are_denied(self):
        service, _ = populated_service()
        with self.assertRaises(AuditLedgerError):
            service.export_chain(
                request(),
                authenticated_tenant_id="audit-tenant-a",
                authenticated_owner_user_id="audit-owner-b",
            )
        spoof = dataclasses.replace(request(), requester_id="audit-owner-b")
        with self.assertRaises(AuditLedgerError):
            service.export_chain(
                spoof,
                authenticated_tenant_id="audit-tenant-a",
                authenticated_owner_user_id="audit-owner-a",
            )
        with self.assertRaises(AuditLedgerError) as caught:
            service.export_chain(
                request(),
                authenticated_tenant_id="audit-tenant-b",
                authenticated_owner_user_id="audit-owner-a",
            )
        self.assertIs(
            caught.exception.reason_code,
            AuditReasonCode.AUDIT_TENANT_MISMATCH,
        )

    def test_cross_tenant_or_foreign_chain_cannot_form_request(self):
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(request(), tenant_id="audit-tenant-b")
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(
                request(),
                chain_ids=(
                    compute_audit_chain_id("audit-tenant-a", "audit-owner-b"),
                ),
            )

    def test_future_reviewer_actor_is_step34_and_not_authorized(self):
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(
                request(),
                requester_actor_type=AuditActorType.REVIEW_SERVICE,
                requester_id="future-reviewer",
            )

    def test_sql_injection_shaped_id_is_rejected_as_noncanonical_data(self):
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(request(), owner_user_id="owner' OR 1=1 --")

    def test_db_policy_and_query_are_owner_scoped_and_bounded(self):
        migration = (
            ROOT
            / "sql/cockroachdb/migrations/0016_step33_audit_ledger_hash_chain.sql"
        ).read_text(encoding="utf-8")
        repository = (
            ROOT / "src/aioa_memory_kernel/audit_ledger/repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn("user_context_matches(tenant_id, owner_user_id)", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("user_id IS NOT DISTINCT FROM %s", repository)
        self.assertIn("ORDER BY chain_id, sequence_number LIMIT %s", repository)
        self.assertNotIn("SELECT *", repository)
        self.assertNotRegex(repository, r"UPDATE memory_patch\.audit_events")
        self.assertNotRegex(repository, r"DELETE FROM memory_patch\.audit_events")


class Step33ExportAuthorityTests(unittest.TestCase):
    def test_export_is_observational_and_has_no_step34_or_business_actions(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src/aioa_memory_kernel/audit_ledger").glob("*.py")
        )
        for forbidden in (
            "review_queue",
            "reviewer_assignment",
            "review_dashboard",
            "moderation_ui",
            "personal_memory_ui",
            "execute_action",
            "publish_shared_memory",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("httpx", source)

    def test_bundle_tamper_is_hash_detectable(self):
        service, _ = populated_service()
        bundle = service.export_chain(
            request(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_owner_user_id="audit-owner-a",
        )
        object.__setattr__(bundle, "exported_event_count", 0)
        with self.assertRaises((ContractValidationError, AssertionError)):
            AuditExportBundle(
                schema_version=bundle.schema_version,
                request_hash=bundle.request_hash,
                tenant_id=bundle.tenant_id,
                owner_user_id=bundle.owner_user_id,
                ordered_events=bundle.ordered_events,
                range_proofs=bundle.range_proofs,
                verification_results=bundle.verification_results,
                redaction_policy_id=bundle.redaction_policy_id,
                redaction_policy_version=bundle.redaction_policy_version,
                redaction_policy_digest=bundle.redaction_policy_digest,
                exported_event_count=bundle.exported_event_count,
                truncated=bundle.truncated,
                continuation_token=bundle.continuation_token,
                exported_at=bundle.exported_at,
            )
        detached_proof = dataclasses.replace(
            bundle.range_proofs[0], last_event_hash="0" * 64
        )
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(bundle, range_proofs=(detached_proof,))
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(
                bundle.verification_results[0],
                verification_policy_digest="0" * 64,
            )


class Step33ClosureTests(unittest.TestCase):
    def test_validation_evidence_is_canonical_bound_and_fail_closed(self):
        path = ROOT / "docs/evidence/audit/step33-audit-ledger-validation.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        canonical = (
            json.dumps(
                evidence,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(path.read_bytes(), canonical)
        digest = evidence.pop("validation_digest")
        self.assertEqual(digest, canonical_sha256(evidence))
        self.assertEqual(evidence["step"], 33)
        self.assertEqual(
            evidence["start_sha"],
            "355a790b50a6412adcf64dd0a463219574a3f849",
        )
        self.assertEqual(evidence["status"], "PASS")
        self.assertTrue(evidence["chain"]["verified"])
        self.assertTrue(evidence["append"]["concurrency_chain_verified"])
        self.assertTrue(
            all(value == "DETECTED" for value in evidence["tamper_matrix"].values())
        )
        self.assertEqual(evidence["export"]["secret_leakage_count"], 0)
        self.assertTrue(evidence["rls"]["rls_enabled"])
        self.assertTrue(evidence["rls"]["force_rls"])
        self.assertFalse(evidence["authority"]["audit_business_authority"])
        self.assertFalse(evidence["step34_boundary"]["step34_started"])

    def test_step33_document_package_and_live_checkpoint_are_exact(self):
        for relative in (
            "docs/architecture/AUDIT_LEDGER_HASH_CHAIN_AUDIT_EXPORT_1A.md",
            "docs/adr/ADR-040-append-only-audit-ledger-hash-chain.md",
            "docs/operations/STEP_33_AUDIT_LEDGER_VALIDATION_1A.md",
            "docs/audits/STEP_33_AUDIT_LEDGER_HASH_CHAIN_CLOSURE_1A.md",
            "docs/evidence/audit/step33-audit-ledger-validation.json",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn(
            "Step 33: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn("- [x] **Step 34", roadmap)
        self.assertIn("- [x] **Step 35", roadmap)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [x] **Step 38", roadmap)
        self.assertIn("- [x] **Step 39", roadmap)
        self.assertIn("- [x] **Step 40", roadmap)
        self.assertIn("- [x] **Step 41", roadmap)
        self.assertIn("- [ ] **Step 42", roadmap)
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 41: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 42: NOT STARTED", roadmap)
        self.assertIn("Step 41 completion does not authorize Step 42.", roadmap)
        self.assertIn(
            "Step 33: COMPLETE AND PUSHED at actual closure commit",
            agents,
        )
        self.assertIn("Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 41: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 42: NOT STARTED", agents)
        self.assertIn("Step 41 completion does not authorize Step 42.", agents)
        validator = (
            ROOT / "scripts/run_step33_audit_ledger_validation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("STEP33_RUNTIME_STOP_TIMEOUT_SECONDS = 120", validator)
        self.assertIn(
            "runtime.process.wait(timeout=STEP33_RUNTIME_STOP_TIMEOUT_SECONDS)",
            validator,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
