"""Deterministic release-content and recovery-asset freeze assembly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.security.redaction import assert_secret_free

from .models import (
    RC_MANIFEST_SCHEMA_VERSION,
    RECOVERY_ASSET_MANIFEST_SCHEMA_VERSION,
    RecoveryAsset,
    RecoveryAssetManifest,
    RecoveryStateClass,
    ReleaseCandidateManifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RECOVERY_ASSET_SPEC_PATH = (
    REPOSITORY_ROOT / "config/release/step42-recovery-assets-1a.json"
)
_RUNTIME_ROOTS = ("src", "scripts", "config", "schemas", "sql")
_RUNTIME_FILES = ("package.json", "requirements-ui.txt")


def _file_sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError("release input must be a real file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError("release JSON input is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractValidationError("release JSON input is malformed") from error
    if not isinstance(value, Mapping):
        raise ContractValidationError("release JSON input must be an object")
    return value


def _digest_files(relative_paths: tuple[str, ...]) -> str:
    entries = tuple(
        (path, _file_sha256(REPOSITORY_ROOT / path))
        for path in sorted(relative_paths)
    )
    return canonical_sha256(entries)


def runtime_content_manifest() -> tuple[tuple[tuple[str, int, str], ...], str]:
    """Freeze runtime/config/schema content without self-referential docs."""

    entries: list[tuple[str, int, str]] = []
    for root_name in _RUNTIME_ROOTS:
        root = REPOSITORY_ROOT / root_name
        if not root.is_dir() or root.is_symlink():
            raise ContractValidationError("runtime content root is unavailable")
        for path in sorted(root.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            if (
                path.is_symlink()
                or "__pycache__" in path.parts
                or path.suffix in {".pyc", ".pyo"}
            ):
                continue
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            entries.append((relative, path.stat().st_size, _file_sha256(path)))
    for relative in _RUNTIME_FILES:
        path = REPOSITORY_ROOT / relative
        entries.append((relative, path.stat().st_size, _file_sha256(path)))
    result = tuple(sorted(entries))
    if len({name for name, _size, _digest in result}) != len(result):
        raise ContractValidationError("runtime content manifest has duplicates")
    return result, canonical_sha256(result)


def build_recovery_asset_manifest(
    *,
    step41_base_sha: str,
    spec_path: Path = DEFAULT_RECOVERY_ASSET_SPEC_PATH,
) -> RecoveryAssetManifest:
    spec = _load_json(spec_path)
    if set(spec) != {"schema_version", "assets"} or spec.get(
        "schema_version"
    ) != RECOVERY_ASSET_MANIFEST_SCHEMA_VERSION:
        raise ContractValidationError("recovery asset spec keys differ")
    raw_assets = spec.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ContractValidationError("recovery asset spec must contain assets")
    assets: list[RecoveryAsset] = []
    expected_keys = {
        "asset_id",
        "state_class",
        "recovery_source",
        "backup_mechanism",
        "integrity_mechanism",
        "protection_mechanism",
        "restore_order",
        "prerequisites",
        "portable",
        "contains_private_data",
        "owner_tenant_isolation_required",
        "immutable_or_retained",
        "rebuildable",
        "omission_reason",
    }
    for raw in raw_assets:
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            raise ContractValidationError("recovery asset spec entry differs")
        prerequisites = raw["prerequisites"]
        if not isinstance(prerequisites, list):
            raise ContractValidationError("asset prerequisites must be a list")
        values = dict(raw)
        try:
            values["state_class"] = RecoveryStateClass(values["state_class"])
        except (TypeError, ValueError) as error:
            raise ContractValidationError("asset state class is unknown") from error
        values["prerequisites"] = tuple(sorted(prerequisites))
        assets.append(RecoveryAsset.create(**values))
    ordered = tuple(sorted(assets, key=lambda item: item.asset_id))
    counts = tuple(
        sorted(
            (
                state.value,
                sum(item.state_class is state for item in ordered),
            )
            for state in RecoveryStateClass
        )
    )
    payload = {
        "schema_version": RECOVERY_ASSET_MANIFEST_SCHEMA_VERSION,
        "step41_base_sha": step41_base_sha,
        "assets": ordered,
        "classification_counts": counts,
    }
    manifest = RecoveryAssetManifest(
        **payload,
        manifest_digest=canonical_sha256(payload),
    )
    assert_secret_free(manifest, surface="Step 42 recovery asset manifest")
    return manifest


def build_rc_manifest(
    *,
    step41_base_sha: str,
    recovery_asset_manifest_digest: str,
    repository_identity: str,
    branch: str = "main",
) -> ReleaseCandidateManifest:
    migrations = tuple(
        ["sql/cockroachdb/migrations/manifest.json"]
        + [
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in sorted(
                (REPOSITORY_ROOT / "sql/cockroachdb/migrations").glob("*.sql")
            )
        ]
    )
    runtime_profile_path = REPOSITORY_ROOT / "config/runtime/4gb-demo-1a.json"
    provider_path = (
        REPOSITORY_ROOT
        / "config/modeling/openrouter-moonshotai-kimi-k2-step38-1a.json"
    )
    embedding_path = (
        REPOSITORY_ROOT
        / "config/embeddings/multilingual-e5-small-step19-1a.json"
    )
    runtime_profile = _load_json(runtime_profile_path)
    provider = _load_json(provider_path)
    embedding = _load_json(embedding_path)
    _entries, runtime_digest = runtime_content_manifest()
    dependency_paths = tuple(
        path
        for path in ("requirements-ui.txt", "package.json")
        if (REPOSITORY_ROOT / path).is_file()
    )
    dependency_pairs = tuple(
        sorted(
            (
                Path(path).stem.replace("_", "-").replace(".", "-").lower(),
                _file_sha256(REPOSITORY_ROOT / path),
            )
            for path in dependency_paths
        )
    )
    prompt_model_pairs = tuple(
        sorted(
            (
                name,
                _digest_files(paths),
            )
            for name, paths in {
                "critic-contract": (
                    "src/aioa_memory_kernel/critic/models.py",
                    "src/aioa_memory_kernel/critic/parser.py",
                ),
                "draft-v1-contract": (
                    "src/aioa_memory_kernel/modeling/models.py",
                    "config/modeling/openrouter-moonshotai-kimi-k2-step38-1a.json",
                ),
                "draft-v2-verifier-contract": (
                    "src/aioa_memory_kernel/verification/models.py",
                    "src/aioa_memory_kernel/verification/service.py",
                ),
            }.items()
        )
    )
    build_pairs = (
        (
            "cockroachdb-version-pin",
            _file_sha256(REPOSITORY_ROOT / "config/cockroachdb/version-pin.json"),
        ),
        (
            "owner-ui-static-assets",
            _digest_files(
                tuple(
                    path.relative_to(REPOSITORY_ROOT).as_posix()
                    for path in sorted(
                        (REPOSITORY_ROOT / "src/aioa_memory_kernel/personal_memory_ui/static").rglob("*")
                    )
                    if path.is_file() and not path.is_symlink()
                )
            ),
        ),
    )
    migration_digest = _digest_files(migrations)
    profile_digest = runtime_profile.get("profile_digest")
    provider_digest = provider.get("config_digest")
    embedding_digest = embedding.get("config_digest")
    if not all(isinstance(item, str) and len(item) == 64 for item in (
        profile_digest,
        provider_digest,
        embedding_digest,
    )):
        raise ContractValidationError("approved configuration digests are unavailable")
    payload = {
        "rc_schema_version": RC_MANIFEST_SCHEMA_VERSION,
        "rc_id": f"memory-patch-aioa-rc-1a-{runtime_digest[:16]}",
        "created_from_step41_sha": step41_base_sha,
        "release_commit_expected_parent": step41_base_sha,
        "repository_identity": repository_identity,
        "branch": branch,
        "migration_manifest_digest": migration_digest,
        "runtime_profile_digest": profile_digest,
        "provider_configuration_identity": provider_digest,
        "embedding_model_identity": embedding_digest,
        "prompt_model_contract_identities": prompt_model_pairs,
        "source_registry_digest": _digest_files(
            (
                "config/source-registry/source-registry-policy-1a.json",
                "schemas/hat-manifest.schema.json",
            )
        ),
        "corpus_publication_manifest_digest": _digest_files(
            (
                "config/hats/german-law-1.0.0.json",
                "docs/evidence/corpus/step14-german-law-corpus-inventory-summary.json",
                "docs/evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json",
                "docs/evidence/corpus/step16-german-law-hat-publication-summary.json",
            )
        ),
        "audit_schema_hash_chain_identity": _digest_files(
            (
                "schemas/audit-event.schema.json",
                "sql/cockroachdb/migrations/0016_step33_audit_ledger_hash_chain.sql",
            )
        ),
        "personal_memory_schema_identity": _digest_files(
            (
                "schemas/personal-memory-space.schema.json",
                "schemas/memory-patch-proposal.schema.json",
                "config/personal-memory/personal-memory-policy-1a.json",
                "sql/cockroachdb/migrations/0011_step27_personal_memory_persistence.sql",
                "sql/cockroachdb/migrations/0012_step28_correction_candidate_bridge.sql",
                "sql/cockroachdb/migrations/0013_step29_personal_memory_patch_validation.sql",
                "sql/cockroachdb/migrations/0014_step30_user_approval_commit_activation.sql",
                "sql/cockroachdb/migrations/0015_step32_personal_memory_lifecycle.sql",
            )
        ),
        "dependency_manifest_digests": dependency_pairs,
        "build_container_identities": build_pairs,
        "recovery_asset_manifest_digest": recovery_asset_manifest_digest,
        "validation_environment_class": (
            "CONTROLLED_DISPOSABLE_SINGLE_NODE_NOT_PRODUCTION_HA_DR"
        ),
        "runtime_content_digest": runtime_digest,
    }
    manifest = ReleaseCandidateManifest(
        **payload,
        manifest_digest=canonical_sha256(payload),
    )
    assert_secret_free(manifest, surface="Step 42 RC manifest")
    return manifest


__all__ = [
    "DEFAULT_RECOVERY_ASSET_SPEC_PATH",
    "REPOSITORY_ROOT",
    "build_rc_manifest",
    "build_recovery_asset_manifest",
    "runtime_content_manifest",
]
