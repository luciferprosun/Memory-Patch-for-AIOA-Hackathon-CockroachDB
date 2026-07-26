"""Versioned Knowledge HAT manifest and non-executable SDK protocol."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from .correction import CorrectionRequirement
from .enums import HatAuthorityDeclaration
from .evidence import ClaimCandidate
from .exceptions import AuthorityViolation, ContractValidationError
from .patches import MemoryPatchProposal
from .personal_memory import MemoryConflict
from .scope import HatScopeDimensionDefinition, ScopeDimension
from .serialization import (
    freeze_json,
    freeze_string_tuple,
    freeze_typed_tuple,
    require_enum_member,
    require_non_empty,
)


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_FORBIDDEN_CAPABILITY_TOKENS = (
    "EXTERNAL_ACTION",
    "SHELL",
    "FILE_WRITE",
    "MESSAGE_SEND",
    "PAYMENT",
    "CANONICAL_WRITE",
    "PATCH_APPROVAL",
    "PATCH_COMMIT",
    "MEMORY_ACTIVATION",
    "CAPABILITY_GRANT",
)
_RESERVED_SECURITY_KEYS = frozenset(
    {
        "external_action_authority",
        "canonical_write_authority",
        "patch_approval_authority",
        "patch_commit_authority",
        "executable_user_code",
        "private_memory_access",
    }
)
HAT_MANIFEST_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class HatSecurityPolicy:
    """Knowledge HATs declare zero authority at all consequential boundaries."""

    external_action_authority: HatAuthorityDeclaration = (
        HatAuthorityDeclaration.NONE
    )
    canonical_write_authority: HatAuthorityDeclaration = (
        HatAuthorityDeclaration.NONE
    )
    patch_approval_authority: HatAuthorityDeclaration = (
        HatAuthorityDeclaration.NONE
    )
    patch_commit_authority: HatAuthorityDeclaration = HatAuthorityDeclaration.NONE
    executable_user_code: bool = False
    private_memory_access: bool = False

    def __post_init__(self) -> None:
        for name, declaration in (
            ("external_action_authority", self.external_action_authority),
            ("canonical_write_authority", self.canonical_write_authority),
            ("patch_approval_authority", self.patch_approval_authority),
            ("patch_commit_authority", self.patch_commit_authority),
        ):
            try:
                require_enum_member(
                    declaration, HatAuthorityDeclaration, name
                )
            except ContractValidationError as exc:
                raise AuthorityViolation(
                    f"{name} must be declared as NONE"
                ) from exc
        declarations = (
            self.external_action_authority,
            self.canonical_write_authority,
            self.patch_approval_authority,
            self.patch_commit_authority,
        )
        if any(value is not HatAuthorityDeclaration.NONE for value in declarations):
            raise AuthorityViolation("a Knowledge HAT must declare all authority NONE")
        if self.executable_user_code is not False:
            raise AuthorityViolation("a Knowledge HAT cannot execute user code")
        if self.private_memory_access is not False:
            raise AuthorityViolation(
                "shared Knowledge HAT retrieval cannot access private memory"
            )


@dataclass(frozen=True, slots=True)
class HatManifest:
    """Domain-neutral, versioned declaration consumed by Kernel Core."""

    schema_version: str
    hat_id: str
    hat_version: str
    display_name: str
    domain_ids: tuple[str, ...]
    kernel_api_compatibility: str
    supported_languages: tuple[str, ...]
    scope_dimension_definitions: tuple[HatScopeDimensionDefinition, ...]
    capabilities: tuple[str, ...]
    source_authority_policy: Mapping[str, Any]
    retrieval_contract: Mapping[str, Any]
    claim_contract: Mapping[str, Any]
    conflict_contract: Mapping[str, Any]
    memory_policy: Mapping[str, Any]
    security_policy: HatSecurityPolicy
    extension_points: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "schema_version",
            "hat_id",
            "hat_version",
            "display_name",
            "kernel_api_compatibility",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        if self.schema_version != HAT_MANIFEST_SCHEMA_VERSION:
            raise ContractValidationError(
                f"unsupported HAT manifest schema_version {self.schema_version!r}"
            )
        if not _IDENTIFIER.fullmatch(self.hat_id):
            raise ContractValidationError(
                "hat_id must use lowercase letters, digits, dots, underscores, or hyphens"
            )
        object.__setattr__(
            self,
            "domain_ids",
            freeze_string_tuple(self.domain_ids, "domain_ids", unique=True),
        )
        object.__setattr__(
            self,
            "supported_languages",
            freeze_string_tuple(
                self.supported_languages,
                "supported_languages",
                unique=True,
            ),
        )
        object.__setattr__(
            self,
            "scope_dimension_definitions",
            freeze_typed_tuple(
                self.scope_dimension_definitions,
                HatScopeDimensionDefinition,
                "scope_dimension_definitions",
            ),
        )
        object.__setattr__(
            self,
            "capabilities",
            freeze_string_tuple(
                self.capabilities,
                "capabilities",
                unique=True,
            ),
        )
        if not self.domain_ids:
            raise ContractValidationError("domain_ids must be non-empty and unique")
        if any(not _IDENTIFIER.fullmatch(item) for item in self.domain_ids):
            raise ContractValidationError("domain_ids contain an invalid identifier")
        if not self.supported_languages:
            raise ContractValidationError(
                "supported_languages must be non-empty and unique"
            )
        if any(
            not _LANGUAGE.fullmatch(language)
            for language in self.supported_languages
        ):
            raise ContractValidationError("supported_languages contain an invalid tag")
        names = [
            definition.name for definition in self.scope_dimension_definitions
        ]
        if len(names) != len(set(names)):
            raise ContractValidationError(
                "scope_dimension_definitions must have unique names"
            )
        if not self.capabilities:
            raise ContractValidationError(
                "capabilities must contain non-empty declarations"
            )
        for capability in self.capabilities:
            normalized = capability.upper()
            if any(
                token in normalized for token in _FORBIDDEN_CAPABILITY_TOKENS
            ):
                raise AuthorityViolation(
                    f"Knowledge HAT capability {capability!r} declares forbidden authority"
                )
        if not isinstance(self.security_policy, HatSecurityPolicy):
            raise ContractValidationError(
                "security_policy must be a HatSecurityPolicy"
            )
        for field_name in (
            "source_authority_policy",
            "retrieval_contract",
            "claim_contract",
            "conflict_contract",
            "memory_policy",
            "extension_points",
        ):
            object.__setattr__(self, field_name, freeze_json(getattr(self, field_name)))
            _assert_no_hidden_security_declaration(
                getattr(self, field_name), field_name=field_name
            )
        validate_hat_manifest(self)


def _assert_no_hidden_security_declaration(
    value: Any, *, field_name: str
) -> None:
    """Reserve all authority declarations for the closed security policy."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key.casefold() in _RESERVED_SECURITY_KEYS:
                raise AuthorityViolation(
                    f"{field_name} cannot redeclare HAT security key {key!r}"
                )
            _assert_no_hidden_security_declaration(
                nested, field_name=field_name
            )
    elif isinstance(value, (tuple, frozenset)):
        for nested in value:
            _assert_no_hidden_security_declaration(
                nested, field_name=field_name
            )


def validate_hat_manifest(manifest: HatManifest) -> None:
    """Validate a manifest without loading or executing HAT implementation code."""

    if not isinstance(manifest, HatManifest):
        raise ContractValidationError("manifest must be a HatManifest")
    policy = manifest.security_policy
    if policy.external_action_authority is not HatAuthorityDeclaration.NONE:
        raise AuthorityViolation("HAT external-action authority must be NONE")
    if policy.canonical_write_authority is not HatAuthorityDeclaration.NONE:
        raise AuthorityViolation("HAT canonical-write authority must be NONE")
    if policy.patch_approval_authority is not HatAuthorityDeclaration.NONE:
        raise AuthorityViolation("HAT patch-approval authority must be NONE")
    if policy.patch_commit_authority is not HatAuthorityDeclaration.NONE:
        raise AuthorityViolation("HAT patch-commit authority must be NONE")


@runtime_checkable
class HatSdk(Protocol):
    """Static interface for trusted, system-installed future Knowledge HATs.

    The contract is intentionally not a loader. Kernel Core never imports
    arbitrary user-provided modules through this protocol.
    """

    @property
    def manifest(self) -> HatManifest:
        """Return the immutable installed manifest."""

    def validate_manifest(self) -> None:
        """Validate the installed manifest and declared compatibility."""

    def normalize_request(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Normalize request data without executing external actions."""

    def derive_scope_requirements(
        self, request: Mapping[str, Any]
    ) -> tuple[ScopeDimension, ...]:
        """Derive typed scope constraints for the request."""

    def build_retrieval_constraints(
        self, dimensions: tuple[ScopeDimension, ...]
    ) -> Mapping[str, Any]:
        """Build a declarative retrieval constraint record."""

    def rank_source_authority(
        self, source_metadata: tuple[Mapping[str, Any], ...]
    ) -> tuple[str, ...]:
        """Return source identifiers in declared authority order."""

    def extract_candidate_claims(
        self, draft_reference: str
    ) -> tuple[ClaimCandidate, ...]:
        """Describe candidate claims; it does not call a model."""

    def detect_conflicts(
        self, evidence_references: tuple[str, ...]
    ) -> tuple[MemoryConflict, ...]:
        """Return explicit candidate conflicts."""

    def create_correction_requirements(
        self, claim_references: tuple[str, ...]
    ) -> tuple[CorrectionRequirement, ...]:
        """Create declarative correction requirements."""

    def create_memory_patch_proposal(
        self, correction_reference: str
    ) -> MemoryPatchProposal:
        """Propose, but never approve, commit, or activate, a Memory Patch."""


def assert_system_installed_hat(instance: object) -> None:
    """Validate SDK shape only; dynamic loading is deliberately unsupported."""

    if not isinstance(instance, HatSdk):
        raise ContractValidationError("installed HAT does not implement HatSdk")
    validate_hat_manifest(instance.manifest)
