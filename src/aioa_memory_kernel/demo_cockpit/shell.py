"""Pure projection layer for the current and legacy cockpit modes."""

from __future__ import annotations

from dataclasses import dataclass

from .legacy_archive import (
    LegacyArchiveManifest,
    LegacyCompatibilityMode,
)

from .models import (
    CockpitExecutionKind,
    CockpitMode,
    CockpitModeOption,
    CockpitRunState,
    CockpitRuntimeStatus,
    CockpitStageSummary,
    CockpitView,
    LegacyModeStatus,
)


_CURRENT_STAGES = (
    ("question", "Question", "Owner-authenticated input; no run has started."),
    ("draft-v1", "Draft V1", "Evidence-blind provider boundary."),
    ("german-law-route", "German Law route / HAT", "Typed route and source authority."),
    ("evidence-bundle", "Evidence Bundle", "Independent canonical evidence lineage."),
    ("temporal-status", "Temporal status", "Scope, freshness and conflict resolution."),
    ("correction-packet", "Correction Packet", "Deterministic evidence-bound correction."),
    ("draft-v2", "Draft V2", "Corrected draft generated from the bounded packet."),
    ("verification", "Verification", "Layered claim and evidence-binding gates."),
    ("verified-answer", "Verified Answer", "Only eligible output may cross the boundary."),
    ("personal-memory", "Personal Memory", "Private, owner-approved and non-canonical."),
    ("audit", "Audit", "Receipt and integrity projection without authority."),
)

_CURRENT_OBSERVERS = (
    (
        "logic-claims",
        "Logic & Claims",
        "Current claim extraction and verifier contracts provide this view.",
    ),
    (
        "safety-authority",
        "Safety & Authority",
        "Evidence, approval and provider authorities remain separated.",
    ),
    (
        "evidence-consistency",
        "Evidence & Consistency",
        "Canonical evidence outranks model output and Personal Memory.",
    ),
)

_LEGACY_OBSERVERS = (
    (
        "legacy-logic-claims",
        "Logic & Claims",
        "Historical observer concept; advisory display only.",
    ),
    (
        "legacy-safety-authority",
        "Safety & Authority",
        "Historical observer concept; no current approval authority.",
    ),
    (
        "legacy-evidence-consistency",
        "Evidence & Consistency",
        "Historical observer concept; not canonical evidence.",
    ),
)


def _stage(
    stage_id: str,
    label: str,
    detail: str,
    *,
    status: str,
    authority: str,
) -> CockpitStageSummary:
    return CockpitStageSummary(stage_id, label, status, detail, authority)


@dataclass(frozen=True, slots=True)
class CockpitShell:
    """Select a bounded view; it owns no provider, DB or mutation capability."""

    runtime_status: CockpitRuntimeStatus
    legacy_mode: LegacyCompatibilityMode = LegacyCompatibilityMode.DISABLED
    legacy_archive: LegacyArchiveManifest | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.runtime_status, CockpitRuntimeStatus)
            or not isinstance(self.legacy_mode, LegacyCompatibilityMode)
            or (
                self.legacy_archive is not None
                and not isinstance(self.legacy_archive, LegacyArchiveManifest)
            )
            or (
                self.legacy_mode is LegacyCompatibilityMode.DISABLED
                and self.legacy_archive is not None
            )
        ):
            raise ValueError("cockpit shell configuration is invalid")

    @property
    def legacy_enabled(self) -> bool:
        return (
            self.legacy_mode is LegacyCompatibilityMode.ARCHIVAL_VIEW
            and self.legacy_archive is not None
        )

    def project(self, requested_mode: str | None = None) -> CockpitView:
        selected = CockpitMode.MEMORY_PATCH_CURRENT
        notice: str | None = None
        if requested_mode == CockpitMode.CRITICAL_PROMPT_LEGACY.value:
            if self.legacy_enabled:
                selected = CockpitMode.CRITICAL_PROMPT_LEGACY
            else:
                notice = (
                    "Legacy / Origin execution is disabled. Its archival metadata "
                    "view is unavailable unless enabled and integrity-valid; Memory "
                    "Patch remains available."
                )
        elif requested_mode not in (None, "", CockpitMode.MEMORY_PATCH_CURRENT.value):
            notice = "Unknown presentation mode was ignored safely."

        mode_options = (
            CockpitModeOption(
                CockpitMode.MEMORY_PATCH_CURRENT,
                "Memory Patch — Current",
                True,
                selected is CockpitMode.MEMORY_PATCH_CURRENT,
                "Current / Evidence-bound",
            ),
            CockpitModeOption(
                CockpitMode.CRITICAL_PROMPT_LEGACY,
                "Critical Prompt Loop — Legacy",
                self.legacy_enabled,
                selected is CockpitMode.CRITICAL_PROMPT_LEGACY,
                "Legacy / Origin · Archival metadata",
            ),
        )
        legacy = LegacyModeStatus(
            enabled=self.legacy_enabled,
            configured_mode=self.legacy_mode,
            classification="DISABLED_WITH_ARCHIVAL_VIEW",
            availability=(
                "ARCHIVAL_VIEW_AVAILABLE"
                if self.legacy_enabled
                else (
                    "ARCHIVE_INTEGRITY_OR_AVAILABILITY_FAILURE"
                    if self.legacy_mode is LegacyCompatibilityMode.ARCHIVAL_VIEW
                    else "DISABLED_BY_CONFIG"
                )
            ),
            execution_kind=CockpitExecutionKind.HISTORICAL_VIEW,
            explanation=(
                "D0 and D2 found exact source provenance but not all exact prompt and "
                "output bytes required for a truthful replay. Live compatibility was "
                "not approved. Only immutable source metadata may be displayed."
            ),
        )
        if selected is CockpitMode.CRITICAL_PROMPT_LEGACY:
            return CockpitView(
                selected_mode=selected,
                execution_kind=CockpitExecutionKind.HISTORICAL_VIEW,
                run_state=CockpitRunState.IDLE,
                heading="Critical Prompt Loop",
                mode_badge="LEGACY / ORIGIN — ARCHIVAL VIEW",
                introduction=(
                    "Verified source provenance explains the first bounded correction "
                    "concept. This is not live, not a replay and not a current Memory "
                    "Patch authority path; unavailable bytes are never reconstructed."
                ),
                notice=notice,
                mode_options=mode_options,
                runtime=self.runtime_status,
                stages=(
                    _stage(
                        "legacy-prompt",
                        "Prompt",
                        "The exact versioned execution prompt was not found and is not shown.",
                        status="BYTES UNAVAILABLE",
                        authority="Historical display only",
                    ),
                    _stage(
                        "legacy-main",
                        "MAIN draft",
                        "The source contract is versioned; exact historical draft "
                        "bytes are unavailable.",
                        status="METADATA ONLY",
                        authority="Model output, never canonical evidence",
                    ),
                    _stage(
                        "legacy-review",
                        "Three sequential observers",
                        "Closed roles are provenance-verified; their historical "
                        "outputs are not fabricated.",
                        status="ADVISORY ONLY",
                        authority="Cannot approve, commit or activate",
                    ),
                    _stage(
                        "legacy-revision",
                        "Final model revision",
                        "The five-call orchestration is documented, but no legacy "
                        "controller or provider runs.",
                        status="NOT EXECUTED",
                        authority="No current verifier or evidence authority",
                    ),
                ),
                observer_cards=tuple(
                    _stage(
                        stage_id,
                        label,
                        detail,
                        status="LEGACY CONCEPT",
                        authority="Advisory / demo only",
                    )
                    for stage_id, label, detail in _LEGACY_OBSERVERS
                ),
                legacy=legacy,
                legacy_archive=self.legacy_archive,
            )

        return CockpitView(
            selected_mode=selected,
            execution_kind=CockpitExecutionKind.LIVE,
            run_state=CockpitRunState.IDLE,
            heading="Memory Patch",
            mode_badge="CURRENT / EVIDENCE-BOUND",
            introduction=(
                "The current production-authority path independently retrieves "
                "evidence, constructs corrections and emits only verified or "
                "fail-closed results. D3 will bind this shell to the live jury trace."
            ),
            notice=notice,
            mode_options=mode_options,
            runtime=self.runtime_status,
            stages=tuple(
                _stage(
                    stage_id,
                    label,
                    detail,
                    status="READY FOR D3",
                    authority="Current typed Memory Patch contract",
                )
                for stage_id, label, detail in _CURRENT_STAGES
            ),
            observer_cards=tuple(
                _stage(
                    stage_id,
                    label,
                    detail,
                    status="CURRENT CONTRACT",
                    authority="Presentation only; verifier decides eligibility",
                )
                for stage_id, label, detail in _CURRENT_OBSERVERS
            ),
            legacy=legacy,
            legacy_archive=self.legacy_archive,
        )


def build_default_cockpit_shell() -> CockpitShell:
    """Safe factory for direct Step35 app construction and focused tests."""

    return CockpitShell(
        CockpitRuntimeStatus(
            profile_id="runtime-injected",
            asgi_application="aioa_memory_kernel.demo_runtime.asgi:app",
            authentication="OIDC + PKCE / server-side owner session",
            session_backend="Runtime-injected OwnerSessionStore",
            database="CockroachDB through current service contracts",
            provider="Approved provider through server-side adapter",
            provider_guard="One guarded provider boundary",
            readiness_contract="/health/live + /health/ready",
        ),
        legacy_mode=LegacyCompatibilityMode.DISABLED,
    )


__all__ = ["CockpitShell", "build_default_cockpit_shell"]
