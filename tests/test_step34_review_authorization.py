"""Step 34 reviewer authorization, RLS, privacy, and boundary tests."""

from __future__ import annotations

import json
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from tests._support import REPOSITORY_ROOT
from tests.test_step34_human_review_workspace import (
    NOW,
    OWNER,
    REVIEWER,
    TENANT,
    FrozenClock,
    MemoryReviewRepository,
    MemoryRunner,
    case_fixture,
    principal,
)

from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.review_workspace import (
    STEP34_SCHEMA_VERSION,
    HumanReviewWorkspaceError,
    HumanReviewWorkspaceService,
    ReviewCaseIntakeService,
    ReviewCaseType,
    ReviewDecisionHandoffService,
    ReviewDecisionType,
    ReviewQueueRequest,
    ReviewSourceContext,
    ReviewSourceContract,
    ReviewerPrincipal,
    ReviewerRole,
    SubmitReviewDecision,
    build_claim_review_case_request,
    build_reviewer_authorization,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose


ROOT = REPOSITORY_ROOT
MIGRATION = ROOT / "sql/cockroachdb/migrations/0017_step34_human_review_workspace.sql"
SECURITY_MANIFEST = (
    ROOT / "config/cockroachdb/review-workspace-security-1a.json"
)
EVIDENCE = ROOT / "docs/evidence/review/step34-human-review-workspace-validation.json"
ROADMAP = ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md"
AGENTS = ROOT / "AGENTS.md"


class Step34AuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = MemoryReviewRepository()
        self.reviewer_runner = MemoryRunner()
        self.service_runner = MemoryRunner(CredentialPurpose.REVIEW_SERVICE_DATABASE)
        self.clock = FrozenClock()
        self.workspace = HumanReviewWorkspaceService(
            self.reviewer_runner, repository=self.repo, trusted_clock=self.clock
        )
        self.intake = ReviewCaseIntakeService(
            self.service_runner, repository=self.repo, trusted_clock=self.clock
        )
        self.case, self.context = case_fixture()
        self.reviewer = principal()
        self.authorization = build_reviewer_authorization(
            self.reviewer,
            case_type=self.case.case_type,
            owner_user_id=OWNER,
            granted_at=NOW,
        )
        self.repo.authorizations[
            (
                TENANT,
                REVIEWER,
                self.reviewer.reviewer_role,
                self.case.case_type,
                OWNER,
            )
        ] = self.authorization
        self.intake.create_case(
            self.case,
            self.context,
            authenticated_tenant_id=TENANT,
            authenticated_owner_user_id=OWNER,
        )

    def test_ordinary_user_without_reviewer_authorization_is_denied(self):
        ordinary = principal(reviewer="ordinary-user")
        request = ReviewQueueRequest(
            schema_version=STEP34_SCHEMA_VERSION,
            tenant_id=TENANT,
            reviewer_principal_hash=ordinary.principal_hash,
            reviewer_id=ordinary.reviewer_id,
            reviewer_role=ordinary.reviewer_role,
            case_types=(self.case.case_type,),
            page_size=8,
            continuation=None,
            requested_at=NOW,
        )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.workspace.list_queue(request, ordinary)

    def test_cross_tenant_principal_is_denied(self):
        foreign = principal(tenant="foreign-tenant")
        request = build_claim_review_case_request(
            self.case,
            self.reviewer,
            requested_at=NOW,
            idempotency_key="cross-tenant-claim",
        )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.workspace.claim_case(request, foreign)

    def test_owner_scoped_grant_hides_unrelated_private_case(self):
        # A detached mutation cannot even become a valid hash-bound case.
        with self.assertRaises(ContractValidationError):
            replace(self.case, owner_user_id="unrelated-owner")

    def test_answer_reviewer_cannot_request_shared_promotion_cases(self):
        answer_reviewer = principal(role=ReviewerRole.ANSWER_REVIEWER)
        with self.assertRaises(ContractValidationError):
            ReviewQueueRequest(
                schema_version=STEP34_SCHEMA_VERSION,
                tenant_id=TENANT,
                reviewer_principal_hash=answer_reviewer.principal_hash,
                reviewer_id=answer_reviewer.reviewer_id,
                reviewer_role=answer_reviewer.reviewer_role,
                case_types=(ReviewCaseType.SHARED_MEMORY_PROMOTION,),
                page_size=8,
                continuation=None,
                requested_at=NOW,
            )

    def test_model_and_critic_cannot_authenticate_as_reviewer(self):
        values = {
            "schema_version": STEP34_SCHEMA_VERSION,
            "tenant_id": TENANT,
            "reviewer_id": REVIEWER,
            "reviewer_role": ReviewerRole.SENIOR_REVIEWER,
            "authentication_context_hash": canonical_sha256("auth"),
            "authenticated_at": NOW,
        }
        with self.assertRaises(ContractValidationError):
            ReviewerPrincipal(**values, model_actor=True)
        with self.assertRaises(ContractValidationError):
            ReviewerPrincipal(**values, critic_actor=True)

    def test_context_rejects_secret_shaped_or_machine_path_values(self):
        for payload in (
            {"password": "do-not-store"},
            {"safe": "/home/reviewer/private.txt"},
            {"authorization": "Bearer token"},
        ):
            with self.assertRaises(ContractValidationError):
                ReviewSourceContext(
                    schema_version=STEP34_SCHEMA_VERSION,
                    source_contract=ReviewSourceContract.STEP26_HUMAN_REVIEW_REQUIRED,
                    subject_hash=self.case.subject_hash,
                    context_payload=payload,
                    contains_raw_private_memory=False,
                    canonical_evidence=False,
                    model_authority=False,
                )

    def test_reviewer_note_is_bounded_non_evidence_and_secret_safe(self):
        claimed, _, _ = self.workspace.claim_case(
            build_claim_review_case_request(
                self.case,
                self.reviewer,
                requested_at=NOW,
                idempotency_key="note-claim",
            ),
            self.reviewer,
        )
        detail = self.workspace.get_detail(
            tenant_id=TENANT,
            review_case_id=claimed.review_case_id,
            principal=self.reviewer,
        )
        base = {
            "schema_version": STEP34_SCHEMA_VERSION,
            "tenant_id": TENANT,
            "review_case_id": claimed.review_case_id,
            "review_case_hash": claimed.case_hash,
            "subject_hash": claimed.subject_hash,
            "reviewer_principal_hash": self.reviewer.principal_hash,
            "reviewer_id": REVIEWER,
            "reviewer_role": self.reviewer.reviewer_role,
            "decision_type": ReviewDecisionType.REJECT_ANSWER,
            "decision_reason_codes": (),
            "context_digest": detail.detail_hash,
            "audit_verification_result_hash": claimed.audit_verification_result_hash,
            "expected_state": claimed.review_state,
            "expected_state_version": claimed.review_state_version,
            "decided_at": NOW,
            "idempotency_key": "secret-note",
        }
        for note in (
            "Authorization: Bearer private-token",
            "AKIAABCDEFGHIJKLMNOP",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
        ):
            with self.subTest(note=note):
                with self.assertRaises(ContractValidationError):
                    SubmitReviewDecision(**base, reviewer_note=note)

    def test_concurrent_claim_has_exactly_one_winner(self):
        second = principal(reviewer="second-reviewer")
        self.repo.authorizations[
            (TENANT, second.reviewer_id, second.reviewer_role, self.case.case_type, OWNER)
        ] = build_reviewer_authorization(
            second,
            case_type=self.case.case_type,
            owner_user_id=OWNER,
            granted_at=NOW,
        )
        requests = (
            (
                self.reviewer,
                build_claim_review_case_request(
                    self.case,
                    self.reviewer,
                    requested_at=NOW,
                    idempotency_key="race-one",
                ),
            ),
            (
                second,
                build_claim_review_case_request(
                    self.case,
                    second,
                    requested_at=NOW,
                    idempotency_key="race-two",
                ),
            ),
        )
        outcomes = []

        def claim(value):
            reviewer, request = value
            try:
                outcomes.append(("PASS", self.workspace.claim_case(request, reviewer)))
            except HumanReviewWorkspaceError:
                outcomes.append(("DENIED", None))

        threads = [threading.Thread(target=claim, args=(value,)) for value in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(item[0] for item in outcomes), ["DENIED", "PASS"])

    def test_handoff_service_requires_exact_dedicated_service_identity(self):
        service = ReviewDecisionHandoffService(
            self.service_runner, repository=self.repo, trusted_clock=self.clock
        )
        source = inspect_source(service)
        self.assertIn("STEP34_REVIEW_SERVICE_ACTOR_ID", source)
        self.assertNotIn("authenticated_service_id: bool", source)


def inspect_source(value) -> str:
    import inspect

    return inspect.getsource(type(value))


class Step34SqlSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.manifest = json.loads(SECURITY_MANIFEST.read_text(encoding="utf-8"))

    def test_dedicated_roles_are_nologin_non_bypass_and_separate(self):
        for role in ("mp_human_reviewer", "mp_review_service"):
            self.assertIn(f"CREATE ROLE IF NOT EXISTS {role}", self.sql)
        self.assertGreaterEqual(
            self.sql.count("NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"), 2
        )
        self.assertIn("REVOKE mp_review_service FROM mp_human_reviewer", self.sql)
        self.assertIn("REVOKE mp_human_reviewer FROM mp_review_service", self.sql)

    def test_five_review_tables_have_rls_and_force_rls(self):
        tables = [item["table"] for item in self.manifest["tables"]]
        self.assertEqual(len(tables), 5)
        for table in tables:
            self.assertRegex(
                self.sql,
                rf"ALTER TABLE memory_patch\.{table}\s+ENABLE ROW LEVEL SECURITY",
            )
            self.assertRegex(
                self.sql,
                rf"ALTER TABLE memory_patch\.{table}\s+FORCE ROW LEVEL SECURITY",
            )

    def test_ordinary_runtime_and_commit_helper_have_no_review_grants(self):
        revoke = self.sql[
            self.sql.index("REVOKE ALL ON TABLE") : self.sql.index(
                "GRANT SELECT ON TABLE memory_patch.reviewer_authorizations"
            )
        ]
        self.assertIn("mp_app_runtime", revoke)
        self.assertIn("mp_personal_memory_commit_helper", revoke)
        self.assertNotRegex(
            self.sql,
            r"GRANT .*human_review_.* TO mp_app_runtime",
        )

    def test_reviewer_has_no_handoff_insert_or_arbitrary_business_grant(self):
        self.assertNotIn(
            "GRANT INSERT ON TABLE memory_patch.human_review_handoffs\n  TO mp_human_reviewer",
            self.sql,
        )
        for forbidden in (
            "GRANT UPDATE ON TABLE memory_patch.memory_items TO mp_human_reviewer",
            "GRANT INSERT ON TABLE memory_patch.source_publications TO mp_human_reviewer",
            "GRANT mp_personal_memory_commit_helper TO mp_human_reviewer",
        ):
            self.assertNotIn(forbidden, self.sql)

    def test_append_only_records_have_database_guards(self):
        for table in (
            "reviewer_authorizations",
            "human_review_claims",
            "human_review_decisions",
            "human_review_handoffs",
        ):
            self.assertIn(f"{table}_s34_append_only", self.sql)
        self.assertIn("BEFORE UPDATE OR DELETE", self.sql)

    def test_case_transition_guard_allows_only_exact_step34_sequence(self):
        for fragment in (
            "TG_OP = 'INSERT'",
            "review case must start OPEN at version 1",
            "BEFORE INSERT OR UPDATE ON memory_patch.human_review_cases",
            "'OPEN' AND (NEW).review_state = 'CLAIMED'",
            "(NEW).review_state = 'IN_REVIEW'",
            "(NEW).review_state IN ('RESOLVED', 'ESCALATED')",
            "review state transition is forbidden",
        ):
            self.assertIn(fragment, self.sql)

    def test_case_shape_binds_exact_typed_upstream_source_family(self):
        for fragment in (
            "STEP26_HUMAN_REVIEW_REQUIRED",
            "STEP26_BOUNDED_ANSWER_FAILURE",
            "STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL",
            "human_review_result_hash",
            "bounded_failure_hash",
            "promotion_proposal_hash",
        ):
            self.assertIn(fragment, self.sql)

    def test_audit_extension_does_not_change_step33_hash_domains(self):
        self.assertNotIn("MEMORY_PATCH_AUDIT_EVENT_V2", self.sql)
        self.assertNotIn("MEMORY_PATCH_AUDIT_CHAIN_V2", self.sql)
        self.assertIn("audit-event-envelope-1.0.0", self.sql)
        self.assertIn("Hash domains", self.sql)

    def test_step35_and_external_authority_are_absent(self):
        lowered = self.sql.casefold()
        for forbidden in (
            "personal_memory_dashboard",
            "slot_management_ui",
            "patch_management_ui",
            "execute_action",
            "external_action",
            "publish_source(",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_validation_evidence_is_canonical_and_fail_closed(self):
        raw = EVIDENCE.read_text(encoding="utf-8")
        evidence = json.loads(raw)
        self.assertEqual(raw, canonical_json(evidence) + "\n")
        digest = evidence.pop("validation_digest")
        self.assertEqual(digest, canonical_sha256(evidence))
        self.assertEqual(evidence["step"], 34)
        self.assertEqual(
            evidence["start_sha"],
            "6f8f14b8acde20a8044d929ba7f6582f2c36785b",
        )
        self.assertEqual(evidence["status"], "PASS")
        self.assertTrue(evidence["audit"]["verified"])
        self.assertEqual(evidence["database"]["migration_count"], 17)
        self.assertEqual(evidence["database"]["replay_skipped_count"], 17)
        self.assertEqual(evidence["database_counts"]["cases"], 2)
        self.assertEqual(
            evidence["case_matrix"]["non_open_initial_state"], "DENIED"
        )
        self.assertEqual(
            evidence["claiming"]["post_resolution_exact_replay"], "PASS"
        )
        self.assertEqual(
            evidence["claiming"]["concurrency"],
            "DETERMINISTIC_FIXED_ORDER_ONE_WINNER_ONE_DENIED",
        )
        self.assertFalse(
            evidence["database"]["security_catalog"][
                "ordinary_user_workspace_access"
            ]
        )
        self.assertEqual(evidence["isolation"]["cross_tenant_review"], "DENIED")
        self.assertFalse(evidence["authority"]["model_reviewer_authority"])
        self.assertFalse(evidence["step35_boundary"]["step35_started"])
        self.assertTrue(evidence["cleanup"]["database_removed"])
        self.assertFalse(evidence["cleanup"]["force_kill_used"])

    def test_step34_document_package_and_live_checkpoint_are_exact(self):
        for relative in (
            "docs/architecture/HUMAN_REVIEW_WORKSPACE_1A.md",
            "docs/adr/ADR-041-human-review-workspace.md",
            "docs/operations/STEP_34_HUMAN_REVIEW_VALIDATION_1A.md",
            "docs/audits/STEP_34_HUMAN_REVIEW_WORKSPACE_CLOSURE_1A.md",
            "docs/evidence/review/step34-human-review-workspace-validation.json",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        roadmap = ROADMAP.read_text(encoding="utf-8")
        agents = AGENTS.read_text(encoding="utf-8")
        self.assertIn("- [x] **Step 34", roadmap)
        self.assertIn(
            "Step 34: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn("- [x] **Step 35", roadmap)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [x] **Step 38", roadmap)
        self.assertIn("- [x] **Step 39", roadmap)
        self.assertIn("- [ ] **Step 40", roadmap)
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: NOT STARTED", roadmap)
        self.assertIn("Step 39 completion does not authorize Step 40.", roadmap)
        self.assertIn(
            "Step 34: COMPLETE AND PUSHED at actual closure commit",
            agents,
        )
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: NOT STARTED", agents)
        self.assertIn("Step 39 completion does not authorize Step 40.", agents)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
