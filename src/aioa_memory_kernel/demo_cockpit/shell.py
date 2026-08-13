"""Pure projection layer for the current and legacy cockpit modes."""

from __future__ import annotations

from dataclasses import dataclass

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
    legacy_enabled: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.runtime_status, CockpitRuntimeStatus)
            or not isinstance(self.legacy_enabled, bool)
        ):
            raise ValueError("cockpit shell configuration is invalid")

    def project(self, requested_mode: str | None = None) -> CockpitView:
        selected = CockpitMode.MEMORY_PATCH_CURRENT
        notice: str | None = None
        if requested_mode == CockpitMode.CRITICAL_PROMPT_LEGACY.value:
            if self.legacy_enabled:
                selected = CockpitMode.CRITICAL_PROMPT_LEGACY
            else:
                notice = (
                    "Legacy / Origin view is disabled by server configuration. "
                    "Memory Patch remains available."
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
                "Legacy / Origin",
            ),
        )
        legacy = LegacyModeStatus(
            enabled=self.legacy_enabled,
            classification="LEGACY_VIEW_ONLY",
            availability="AVAILABLE" if self.legacy_enabled else "DISABLED_BY_CONFIG",
            execution_kind=CockpitExecutionKind.HISTORICAL_VIEW,
            explanation=(
                "D0 found reusable visual and narrative patterns, but no verified "
                "historical execution trace suitable for replay and no approved live "
                "compatibility controller. D2 owns any provenance-bound extension."
            ),
        )
        if selected is CockpitMode.CRITICAL_PROMPT_LEGACY:
            return CockpitView(
                selected_mode=selected,
                execution_kind=CockpitExecutionKind.HISTORICAL_VIEW,
                run_state=CockpitRunState.IDLE,
                heading="Critical Prompt Loop",
                mode_badge="LEGACY / ORIGIN — VIEW ONLY",
                introduction=(
                    "The first bounded correction concept is shown as historical "
                    "context. It is not live, not a verified replay and not a current "
                    "Memory Patch authority path."
                ),
                notice=notice,
                mode_options=mode_options,
                runtime=self.runtime_status,
                stages=(
                    _stage(
                        "legacy-prompt",
                        "Prompt",
                        "No provenance-bound execution fixture is attached in D1.",
                        status="NOT RUN",
                        authority="Historical display only",
                    ),
                    _stage(
                        "legacy-main",
                        "MAIN response",
                        "No historical output is presented as live provider output.",
                        status="NOT RUN",
                        authority="Model output, never canonical evidence",
                    ),
                    _stage(
                        "legacy-review",
                        "CRITIC / observer review",
                        "D1 provides labels only; no old controller is executed.",
                        status="ADVISORY ONLY",
                        authority="Cannot approve, commit or activate",
                    ),
                    _stage(
                        "legacy-revision",
                        "Bounded revision",
                        "Reserved for a D2 decision backed by exact provenance.",
                        status="UNAVAILABLE",
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
        legacy_enabled=False,
    )


__all__ = ["CockpitShell", "build_default_cockpit_shell"]
