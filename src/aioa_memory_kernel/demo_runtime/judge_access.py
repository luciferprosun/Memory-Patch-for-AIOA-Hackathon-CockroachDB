"""Deny-by-default hosted-demo access from verified OIDC subjects."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from aioa_memory_kernel.personal_memory_ui.models import OwnerPrincipal


@dataclass(frozen=True, slots=True, repr=False)
class JudgeAccessPolicy:
    """Exact server-side OIDC subject allowlist; browser flags are irrelevant."""

    allowed_subjects: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.allowed_subjects) <= 32
            or tuple(sorted(set(self.allowed_subjects))) != self.allowed_subjects
            or any(
                not isinstance(subject, str)
                or not subject
                or subject != subject.strip()
                or len(subject.encode("utf-8")) > 255
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in subject
                )
                for subject in self.allowed_subjects
            )
        ):
            raise ValueError("judge access policy is invalid")

    def __call__(self, principal: OwnerPrincipal) -> bool:
        if not isinstance(principal, OwnerPrincipal):
            return False
        allowed = False
        for subject in self.allowed_subjects:
            allowed = secrets.compare_digest(
                principal.oidc_subject.encode("utf-8"), subject.encode("utf-8")
            ) or allowed
        return allowed

    def __repr__(self) -> str:
        return f"JudgeAccessPolicy(allowed_subject_count={len(self.allowed_subjects)})"


__all__ = ["JudgeAccessPolicy"]
