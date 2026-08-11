"""Step 37 recovery authority, scope, and credential failure tests."""

from __future__ import annotations

import inspect
import unittest
from datetime import UTC, datetime, timedelta

from aioa_memory_kernel.audit_ledger import (
    AuditActorType,
    AuditLedgerError,
    AuditLedgerService,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    PersistenceConfigurationError,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (
    PersonalMemoryApprovalService,
    PersonalMemoryCommitHelper,
    PersonalMemoryPatchLifecycleError,
    build_personal_memory_approval_request,
)
from aioa_memory_kernel.reliability import (
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
)
from aioa_memory_kernel.security import (
    CREDENTIAL_SPECS,
    CredentialBoundaryError,
    CredentialPurpose,
    load_required_credential,
)
from tests.test_step28_correction_candidate_bridge import FakeIdempotency
from tests.test_step29_personal_memory_patch_proposal import (
    InMemoryCandidateRepository,
    InMemoryProposalRepository,
    InMemorySlotRepository,
    fixture,
)
from tests.test_step30_user_approval_commit_activation import (
    InMemoryLifecycleRepository,
    MutableTrustedClock,
)
from tests.test_step33_audit_ledger import MemoryRepository, MemoryRunner, draft


def _runner(purpose: CredentialPurpose) -> SerializableTransactionRunner:
    return SerializableTransactionRunner(
        lambda: None,
        credential_purpose=purpose,
    )


class _Clock:
    def now(self) -> datetime:
        return datetime(2046, 1, 2, 3, 4, 5, tzinfo=UTC)


def run_authority_recovery_campaigns() -> tuple[FailureRecoveryCaseResult, ...]:
    broad_credentials = {
        "DATABASE_URL": "postgresql://root:synthetic@example.invalid/defaultdb",
        "DATABASE_URL_ADMIN": "postgresql://admin:synthetic@example.invalid/db",
        "DATABASE_URL_MIGRATOR": "postgresql://migrator:synthetic@example.invalid/db",
    }
    try:
        load_required_credential(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
            broad_credentials,
        )
    except CredentialBoundaryError:
        pass
    else:
        raise AssertionError("missing Commit Helper credential used a broader fallback")

    for missing_purpose in (
        CredentialPurpose.HUMAN_REVIEWER_DATABASE,
        CredentialPurpose.AUDIT_APPENDER_DATABASE,
        CredentialPurpose.MODEL_PROVIDER,
    ):
        try:
            load_required_credential(missing_purpose, broad_credentials)
        except CredentialBoundaryError:
            pass
        else:
            raise AssertionError(
                f"missing {missing_purpose.value} credential used a broader fallback"
            )

    for forbidden_purpose in (
        CredentialPurpose.APPLICATION_DATABASE,
        CredentialPurpose.HUMAN_REVIEWER_DATABASE,
        CredentialPurpose.MODEL_PROVIDER,
        CredentialPurpose.MIGRATION_DATABASE,
    ):
        try:
            PersonalMemoryCommitHelper(
                _runner(forbidden_purpose), trusted_clock=_Clock()
            )
        except PersistenceConfigurationError:
            pass
        else:
            raise AssertionError("recovery principal acquired Commit Helper authority")

    value = fixture()
    lifecycle = InMemoryLifecycleRepository(value.awaiting)
    slots = InMemorySlotRepository(value)
    candidates = InMemoryCandidateRepository(value)
    proposals = InMemoryProposalRepository()
    proposals.values[value.awaiting.proposal.proposal_id] = value.awaiting
    clock = MutableTrustedClock(value.awaiting.updated_at + timedelta(seconds=1))
    app_runner = MemoryRunner(CredentialPurpose.APPLICATION_DATABASE)
    approval = PersonalMemoryApprovalService(
        app_runner,
        lifecycle_repository=lifecycle,
        slot_repository=slots,
        candidate_repository=candidates,
        proposal_repository=proposals,
        idempotency=FakeIdempotency(),
        trusted_clock=clock,
    )
    request = build_personal_memory_approval_request(
        value.awaiting,
        approval_nonce="step37-scope-recovery",
        requested_at=clock.current,
    )
    try:
        approval.approve(
            request,
            authenticated_actor_user_id="different-owner",
        )
    except PersonalMemoryPatchLifecycleError:
        pass
    else:
        raise AssertionError("owner scope widened during recovery")
    if lifecycle.state != value.awaiting or lifecycle.approvals:
        raise AssertionError("denied owner attempt mutated Personal Memory")

    audit_repository = MemoryRepository()
    audit_service = AuditLedgerService(
        MemoryRunner(CredentialPurpose.AUDIT_APPENDER_DATABASE),
        repository=audit_repository,
    )
    event = draft(38)
    try:
        audit_service.append_event(
            event,
            authenticated_tenant_id="different-tenant",
            authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
            authenticated_actor_id=event.actor_id,
        )
    except AuditLedgerError:
        pass
    else:
        raise AssertionError("tenant scope widened during audit recovery")
    if audit_repository.entries:
        raise AssertionError("denied audit append produced a durable event")

    provider_capabilities = CREDENTIAL_SPECS[
        CredentialPurpose.MODEL_PROVIDER
    ].capabilities
    if provider_capabilities != ("CALL_APPROVED_MODEL_PROVIDER",):
        raise AssertionError("provider capability gained recovery authority")

    subject_hash = canonical_sha256({"case": "authority-recovery"})
    return (
        FailureRecoveryCaseResult.build(
            case_id="credential.commit-helper-unavailable",
            failure_domain=FailureDomain.CREDENTIAL_UNAVAILABLE,
            failure_point=FailurePoint.CREDENTIAL_COMMIT_HELPER_UNAVAILABLE,
            subject_hash=subject_hash,
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="NO_ADMIN_FALLBACK",
            reason_codes=("MISSING_CREDENTIAL_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="authority.owner-scope-denied",
            failure_domain=FailureDomain.PERSONAL_MEMORY_APPROVAL,
            failure_point=FailurePoint.PM_BEFORE_APPROVAL,
            subject_hash=value.awaiting.state_hash,
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="OWNER_SCOPE_UNCHANGED",
            reason_codes=("OWNER_SCOPE_WIDENING_DENIED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="authority.audit-tenant-denied",
            failure_domain=FailureDomain.AUDIT_LEDGER,
            failure_point=FailurePoint.AUDIT_BEFORE_APPEND,
            subject_hash=event.subject_hash,
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="TENANT_SCOPE_UNCHANGED",
            reason_codes=("TENANT_SCOPE_WIDENING_DENIED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="credential.provider-boundary",
            failure_domain=FailureDomain.CREDENTIAL_UNAVAILABLE,
            failure_point=FailurePoint.CREDENTIAL_PROVIDER_UNAVAILABLE,
            subject_hash=canonical_sha256(provider_capabilities),
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="NO_DATABASE_OR_APPROVAL_AUTHORITY",
            reason_codes=("PROVIDER_AUTHORITY_NOT_UPGRADED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="credential.reviewer-unavailable",
            failure_domain=FailureDomain.CREDENTIAL_UNAVAILABLE,
            failure_point=FailurePoint.CREDENTIAL_REVIEWER_UNAVAILABLE,
            subject_hash=subject_hash,
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="NO_REVIEWER_AUTHORITY_FALLBACK",
            reason_codes=("MISSING_REVIEWER_CREDENTIAL_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="credential.audit-appender-unavailable",
            failure_domain=FailureDomain.CREDENTIAL_UNAVAILABLE,
            failure_point=FailurePoint.CREDENTIAL_AUDIT_APPENDER_UNAVAILABLE,
            subject_hash=subject_hash,
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="NO_AUDIT_APPENDER_FALLBACK",
            reason_codes=("MISSING_AUDIT_CREDENTIAL_FAILED_CLOSED",),
        ),
    )


class Step37AuthorityRecoveryTests(unittest.TestCase):
    def test_missing_dedicated_credential_never_falls_back_to_admin(self) -> None:
        values = run_authority_recovery_campaigns()
        self.assertTrue(all(value.authority_violation_count == 0 for value in values))
        self.assertIn(
            "NO_ADMIN_FALLBACK",
            {value.final_semantic_state for value in values},
        )

    def test_reviewer_provider_app_and_migrator_cannot_recover_as_commit_helper(
        self,
    ) -> None:
        for purpose in (
            CredentialPurpose.APPLICATION_DATABASE,
            CredentialPurpose.HUMAN_REVIEWER_DATABASE,
            CredentialPurpose.REVIEW_SERVICE_DATABASE,
            CredentialPurpose.MODEL_PROVIDER,
            CredentialPurpose.MIGRATION_DATABASE,
            CredentialPurpose.AUDIT_READER_DATABASE,
        ):
            with self.subTest(purpose=purpose.value):
                with self.assertRaises(PersistenceConfigurationError):
                    PersonalMemoryCommitHelper(
                        _runner(purpose), trusted_clock=_Clock()
                    )

    def test_audit_reader_cannot_be_reinterpreted_as_appender(self) -> None:
        with self.assertRaises(PersistenceConfigurationError):
            AuditLedgerService(
                _runner(CredentialPurpose.AUDIT_READER_DATABASE),
                repository=MemoryRepository(),
            )

    def test_scripted_injector_is_not_imported_by_runtime_services(self) -> None:
        import aioa_memory_kernel.audit_ledger.service as audit_service
        import aioa_memory_kernel.personal_memory.lifecycle_service as lifecycle_service
        import aioa_memory_kernel.review_workspace.service as review_service

        source = "\n".join(
            inspect.getsource(module)
            for module in (audit_service, lifecycle_service, review_service)
        )
        self.assertNotIn("ScriptedFailureInjector", source)
        self.assertNotIn("tests.failure_injection", source)
        self.assertNotIn("fallback_to_admin", source.casefold())
        self.assertNotIn("retry_as_admin", source.casefold())

    def test_recovery_results_report_zero_scope_and_authority_violations(self) -> None:
        for value in run_authority_recovery_campaigns():
            with self.subTest(case=value.case_id):
                self.assertEqual(value.authority_violation_count, 0)
                self.assertEqual(value.integrity_violation_count, 0)
                self.assertEqual(value.duplicate_side_effect_count, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
