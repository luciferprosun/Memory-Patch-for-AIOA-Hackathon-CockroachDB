"""Immutable, secret-free Step 42 release and recovery manifests."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TypeVar

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.security.redaction import assert_secret_free


RC_MANIFEST_SCHEMA_VERSION = "step42-rc-manifest-1a"
RECOVERY_ASSET_MANIFEST_SCHEMA_VERSION = "step42-recovery-assets-1a"
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_T = TypeVar("_T")


class RecoveryStateClass(str, Enum):
    AUTHORITATIVE_BACKUP_REQUIRED = "AUTHORITATIVE_BACKUP_REQUIRED"
    AUTHORITATIVE_EXTERNALLY_PROTECTED = "AUTHORITATIVE_EXTERNALLY_PROTECTED"
    REBUILDABLE_FROM_CANONICAL_INPUTS = "REBUILDABLE_FROM_CANONICAL_INPUTS"
    EPHEMERAL_DO_NOT_BACKUP = "EPHEMERAL_DO_NOT_BACKUP"
    SECRET_DO_NOT_ARCHIVE = "SECRET_DO_NOT_ARCHIVE"


def _canonical_text(value: object, field_name: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or len(value.encode("utf-8")) > maximum
    ):
        raise ContractValidationError(f"{field_name} must be bounded canonical text")
    return value


def _safe_id(value: object, field_name: str) -> str:
    text = _canonical_text(value, field_name, maximum=128)
    if _SAFE_ID.fullmatch(text) is None:
        raise ContractValidationError(f"{field_name} must be a safe identifier")
    return text


def _git_sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
        raise ContractValidationError(f"{field_name} must be a full Git SHA")
    return value


def _canonical_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"{field_name} must be a tuple")
    items = tuple(_canonical_text(item, field_name, maximum=256) for item in value)
    if items != tuple(sorted(set(items))):
        raise ContractValidationError(f"{field_name} must be sorted and unique")
    return items


def _digest_pairs(value: object, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"{field_name} must be a tuple")
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ContractValidationError(f"{field_name} entries must be pairs")
        name = _safe_id(item[0], f"{field_name}.name")
        digest = require_sha256_hex(item[1], f"{field_name}.{name}")
        pairs.append((name, digest))
    result = tuple(pairs)
    if result != tuple(sorted(set(result))):
        raise ContractValidationError(f"{field_name} must be sorted and unique")
    if len({name for name, _digest in result}) != len(result):
        raise ContractValidationError(f"{field_name} names must be unique")
    return result


def _reconstruct(value: _T, expected_type: type[_T]) -> _T:
    if not isinstance(value, expected_type):
        raise IntegrityError(f"expected {expected_type.__name__}")
    try:
        rebuilt = expected_type(
            **{
                field.name: getattr(value, field.name)
                for field in dataclasses.fields(expected_type)
            }
        )
    except (ContractValidationError, TypeError, ValueError) as error:
        raise IntegrityError(
            f"{expected_type.__name__} failed canonical reconstruction"
        ) from error
    if rebuilt != value:
        raise IntegrityError(f"{expected_type.__name__} is not canonical")
    return rebuilt


@dataclass(frozen=True, slots=True)
class RecoveryAsset:
    asset_id: str
    state_class: RecoveryStateClass
    recovery_source: str
    backup_mechanism: str
    integrity_mechanism: str
    protection_mechanism: str
    restore_order: int
    prerequisites: tuple[str, ...]
    portable: bool
    contains_private_data: bool
    owner_tenant_isolation_required: bool
    immutable_or_retained: bool
    rebuildable: bool
    omission_reason: str | None
    asset_hash: str

    def __post_init__(self) -> None:
        _safe_id(self.asset_id, "asset_id")
        if not isinstance(self.state_class, RecoveryStateClass):
            raise ContractValidationError("state_class must be closed")
        for name in (
            "recovery_source",
            "backup_mechanism",
            "integrity_mechanism",
            "protection_mechanism",
        ):
            _canonical_text(getattr(self, name), name)
        if (
            not isinstance(self.restore_order, int)
            or isinstance(self.restore_order, bool)
            or not 0 <= self.restore_order <= 100
        ):
            raise ContractValidationError("restore_order must be bounded")
        _canonical_tuple(self.prerequisites, "prerequisites")
        for name in (
            "portable",
            "contains_private_data",
            "owner_tenant_isolation_required",
            "immutable_or_retained",
            "rebuildable",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError(f"{name} must be boolean")
        omitted = self.state_class in {
            RecoveryStateClass.AUTHORITATIVE_EXTERNALLY_PROTECTED,
            RecoveryStateClass.REBUILDABLE_FROM_CANONICAL_INPUTS,
            RecoveryStateClass.EPHEMERAL_DO_NOT_BACKUP,
            RecoveryStateClass.SECRET_DO_NOT_ARCHIVE,
        }
        if omitted != (self.omission_reason is not None):
            raise ContractValidationError("omitted assets require an exact reason")
        if self.omission_reason is not None:
            _canonical_text(self.omission_reason, "omission_reason")
        if self.state_class is RecoveryStateClass.SECRET_DO_NOT_ARCHIVE and (
            self.backup_mechanism != "EXCLUDED" or not self.omission_reason
        ):
            raise ContractValidationError("secrets must be explicitly excluded")
        if self.state_class is RecoveryStateClass.AUTHORITATIVE_BACKUP_REQUIRED and (
            self.rebuildable or self.omission_reason is not None
        ):
            raise ContractValidationError("authoritative backup assets cannot be omitted")
        assert_secret_free(self, surface="Step 42 recovery asset")
        expected = canonical_sha256(self, exclude_fields=("asset_hash",))
        if self.asset_hash != expected:
            raise ContractValidationError("asset_hash differs")

    @classmethod
    def create(cls, **values: Any) -> "RecoveryAsset":
        return cls(**values, asset_hash=canonical_sha256(values))


@dataclass(frozen=True, slots=True)
class RecoveryAssetManifest:
    schema_version: str
    step41_base_sha: str
    assets: tuple[RecoveryAsset, ...]
    classification_counts: tuple[tuple[str, int], ...]
    manifest_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != RECOVERY_ASSET_MANIFEST_SCHEMA_VERSION:
            raise ContractValidationError("recovery manifest schema differs")
        _git_sha(self.step41_base_sha, "step41_base_sha")
        if not isinstance(self.assets, tuple) or not self.assets:
            raise ContractValidationError("recovery assets must be non-empty")
        if any(not isinstance(item, RecoveryAsset) for item in self.assets):
            raise ContractValidationError("recovery asset type differs")
        if tuple(item.asset_id for item in self.assets) != tuple(
            sorted(item.asset_id for item in self.assets)
        ):
            raise ContractValidationError("recovery assets must be sorted")
        if len({item.asset_id for item in self.assets}) != len(self.assets):
            raise ContractValidationError("recovery asset IDs must be unique")
        expected_counts = tuple(
            sorted(
                (
                    state.value,
                    sum(item.state_class is state for item in self.assets),
                )
                for state in RecoveryStateClass
            )
        )
        if self.classification_counts != expected_counts or any(
            count < 1 for _name, count in expected_counts
        ):
            raise ContractValidationError("every recovery class must be inventoried")
        for item in self.assets:
            _reconstruct(item, RecoveryAsset)
        assert_secret_free(self, surface="Step 42 recovery manifest")
        expected = canonical_sha256(self, exclude_fields=("manifest_digest",))
        if self.manifest_digest != expected:
            raise ContractValidationError("recovery manifest digest differs")


@dataclass(frozen=True, slots=True)
class ReleaseCandidateManifest:
    rc_schema_version: str
    rc_id: str
    created_from_step41_sha: str
    release_commit_expected_parent: str
    repository_identity: str
    branch: str
    migration_manifest_digest: str
    runtime_profile_digest: str
    provider_configuration_identity: str
    embedding_model_identity: str
    prompt_model_contract_identities: tuple[tuple[str, str], ...]
    source_registry_digest: str
    corpus_publication_manifest_digest: str
    audit_schema_hash_chain_identity: str
    personal_memory_schema_identity: str
    dependency_manifest_digests: tuple[tuple[str, str], ...]
    build_container_identities: tuple[tuple[str, str], ...]
    recovery_asset_manifest_digest: str
    validation_environment_class: str
    runtime_content_digest: str
    manifest_digest: str

    def __post_init__(self) -> None:
        if self.rc_schema_version != RC_MANIFEST_SCHEMA_VERSION:
            raise ContractValidationError("RC schema version differs")
        _safe_id(self.rc_id, "rc_id")
        if self.created_from_step41_sha != self.release_commit_expected_parent:
            raise ContractValidationError("Step 41 parent identity differs")
        _git_sha(self.created_from_step41_sha, "created_from_step41_sha")
        _git_sha(
            self.release_commit_expected_parent,
            "release_commit_expected_parent",
        )
        for name in (
            "migration_manifest_digest",
            "runtime_profile_digest",
            "provider_configuration_identity",
            "embedding_model_identity",
            "source_registry_digest",
            "corpus_publication_manifest_digest",
            "audit_schema_hash_chain_identity",
            "personal_memory_schema_identity",
            "recovery_asset_manifest_digest",
            "runtime_content_digest",
        ):
            require_sha256_hex(getattr(self, name), name)
        _canonical_text(self.repository_identity, "repository_identity")
        if self.branch != "main":
            raise ContractValidationError("RC branch must be main")
        _digest_pairs(
            self.prompt_model_contract_identities,
            "prompt_model_contract_identities",
        )
        _digest_pairs(self.dependency_manifest_digests, "dependency_manifest_digests")
        _digest_pairs(self.build_container_identities, "build_container_identities")
        _canonical_text(
            self.validation_environment_class,
            "validation_environment_class",
        )
        assert_secret_free(self, surface="Step 42 RC manifest")
        expected = canonical_sha256(self, exclude_fields=("manifest_digest",))
        if self.manifest_digest != expected:
            raise ContractValidationError("RC manifest digest differs")


def verify_recovery_asset_manifest(
    value: RecoveryAssetManifest,
) -> RecoveryAssetManifest:
    return _reconstruct(value, RecoveryAssetManifest)


def verify_release_candidate_manifest(
    value: ReleaseCandidateManifest,
) -> ReleaseCandidateManifest:
    return _reconstruct(value, ReleaseCandidateManifest)


__all__ = [
    "RC_MANIFEST_SCHEMA_VERSION",
    "RECOVERY_ASSET_MANIFEST_SCHEMA_VERSION",
    "RecoveryAsset",
    "RecoveryAssetManifest",
    "RecoveryStateClass",
    "ReleaseCandidateManifest",
    "verify_recovery_asset_manifest",
    "verify_release_candidate_manifest",
]
