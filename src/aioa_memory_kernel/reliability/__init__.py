"""Controlled recovery contracts; scripted injection lives only in tests."""

from .failure_injection import (
    FAILURE_INJECTOR_VERSION,
    FAILURE_POINT_DOMAINS,
    FAILURE_POINT_REGISTRY_VERSION,
    MAXIMUM_FAILURE_ATTEMPTS,
    RECOVERY_POLICY_VERSION,
    STEP37_SCHEMA_VERSION,
    FailureDirective,
    FailureDomain,
    FailureInjector,
    FailurePoint,
    FailureRecoveryCaseResult,
    InjectedFailure,
    NoOpFailureInjector,
    RecoveryStatus,
    verify_failure_recovery_case_result,
)

__all__ = [
    "FAILURE_INJECTOR_VERSION",
    "FAILURE_POINT_DOMAINS",
    "FAILURE_POINT_REGISTRY_VERSION",
    "MAXIMUM_FAILURE_ATTEMPTS",
    "RECOVERY_POLICY_VERSION",
    "STEP37_SCHEMA_VERSION",
    "FailureDirective",
    "FailureDomain",
    "FailureInjector",
    "FailurePoint",
    "FailureRecoveryCaseResult",
    "InjectedFailure",
    "NoOpFailureInjector",
    "RecoveryStatus",
    "verify_failure_recovery_case_result",
]
