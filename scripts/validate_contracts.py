#!/usr/bin/env python3
"""Validate Step 1A contracts without network access or third-party packages."""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import aioa_memory_kernel as kernel  # noqa: E402


class ValidationFailure(ValueError):
    """A repository contract artifact failed deterministic validation."""


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationFailure(f"{path.relative_to(REPOSITORY_ROOT)}: {exc}") from exc


def resolve_local_reference(reference: str, root_schema: dict[str, Any]) -> Any:
    if not reference.startswith("#/"):
        raise ValidationFailure(f"only local JSON Schema references are allowed: {reference}")
    current: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValidationFailure(f"unresolved local JSON Schema reference: {reference}")
        current = current[part]
    return current


def type_matches(value: Any, expected: str) -> bool:
    checks = {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    if expected not in checks:
        raise ValidationFailure(f"unsupported schema type in local validator: {expected}")
    return checks[expected]


def validate_instance(
    instance: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any],
    location: str,
) -> None:
    """Validate the deliberately used JSON Schema subset."""

    if "$ref" in schema:
        target = resolve_local_reference(schema["$ref"], root_schema)
        validate_instance(
            instance,
            target,
            root_schema=root_schema,
            location=location,
        )
        return
    if "const" in schema and instance != schema["const"]:
        raise ValidationFailure(f"{location}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise ValidationFailure(f"{location}: value {instance!r} is outside enum")
    expected_type = schema.get("type")
    if expected_type is not None:
        type_names = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(type_matches(instance, name) for name in type_names):
            raise ValidationFailure(
                f"{location}: expected type {type_names}, got {type(instance).__name__}"
            )
    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            raise ValidationFailure(f"{location}: missing required keys {missing}")
        if len(instance) < schema.get("minProperties", 0):
            raise ValidationFailure(f"{location}: too few object properties")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                validate_instance(
                    value,
                    properties[name],
                    root_schema=root_schema,
                    location=f"{location}.{name}",
                )
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise ValidationFailure(f"{location}: unexpected key {name!r}")
            if isinstance(additional, dict):
                validate_instance(
                    value,
                    additional,
                    root_schema=root_schema,
                    location=f"{location}.{name}",
                )
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise ValidationFailure(f"{location}: too few array items")
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
                for item in instance
            ]
            if len(encoded) != len(set(encoded)):
                raise ValidationFailure(f"{location}: array items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                validate_instance(
                    item,
                    item_schema,
                    root_schema=root_schema,
                    location=f"{location}[{index}]",
                )
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise ValidationFailure(f"{location}: string is too short")
        pattern = schema.get("pattern")
        if pattern and re.fullmatch(pattern, instance) is None:
            raise ValidationFailure(f"{location}: string does not match {pattern!r}")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationFailure(f"{location}: invalid date-time") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValidationFailure(f"{location}: date-time must include an offset")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ValidationFailure(f"{location}: value is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ValidationFailure(f"{location}: value is above maximum")


def validate_schema_shape(path: Path, schema: dict[str, Any]) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValidationFailure(f"{path.name}: unexpected JSON Schema dialect")
    for field in ("$id", "title", "type", "properties"):
        if field not in schema:
            raise ValidationFailure(f"{path.name}: missing schema field {field}")
    if schema["type"] != "object":
        raise ValidationFailure(f"{path.name}: top-level schema must be an object")
    unknown_required = set(schema.get("required", ())) - set(schema["properties"])
    if unknown_required:
        raise ValidationFailure(
            f"{path.name}: required properties not declared: {sorted(unknown_required)}"
        )


def instantiate_manifest(raw: dict[str, Any]) -> kernel.HatManifest:
    definitions = tuple(
        kernel.HatScopeDimensionDefinition(
            name=item["name"],
            value_type=kernel.ScopeValueType(item["value_type"]),
            comparison_mode=kernel.ScopeComparisonMode(item["comparison_mode"]),
            required=item["required"],
            default_behavior=kernel.MissingDimensionBehavior(
                item["default_behavior"]
            ),
            missing_creates_ambiguous=item["missing_creates_ambiguous"],
            default_value=item.get("default_value"),
            description=item.get("description", ""),
        )
        for item in raw["scope_dimension_definitions"]
    )
    policy = raw["security_policy"]
    return kernel.HatManifest(
        schema_version=raw["schema_version"],
        hat_id=raw["hat_id"],
        hat_version=raw["hat_version"],
        display_name=raw["display_name"],
        domain_ids=tuple(raw["domain_ids"]),
        kernel_api_compatibility=raw["kernel_api_compatibility"],
        supported_languages=tuple(raw["supported_languages"]),
        scope_dimension_definitions=definitions,
        capabilities=tuple(raw["capabilities"]),
        source_authority_policy=raw["source_authority_policy"],
        retrieval_contract=raw["retrieval_contract"],
        claim_contract=raw["claim_contract"],
        conflict_contract=raw["conflict_contract"],
        memory_policy=raw["memory_policy"],
        security_policy=kernel.HatSecurityPolicy(
            external_action_authority=kernel.HatAuthorityDeclaration(
                policy["external_action_authority"]
            ),
            canonical_write_authority=kernel.HatAuthorityDeclaration(
                policy["canonical_write_authority"]
            ),
            patch_approval_authority=kernel.HatAuthorityDeclaration(
                policy["patch_approval_authority"]
            ),
            patch_commit_authority=kernel.HatAuthorityDeclaration(
                policy["patch_commit_authority"]
            ),
            executable_user_code=policy["executable_user_code"],
            private_memory_access=policy["private_memory_access"],
        ),
        extension_points=raw["extension_points"],
    )


def validate_enum_values() -> None:
    expected = {
        kernel.KnowledgeRoute: {
            "PASS_THROUGH",
            "HAT_ASSIST",
            "HAT_ENFORCE",
            "AMBIGUOUS",
        },
        kernel.ActionPolicy: {
            "ALLOW",
            "DENY_ACTION",
            "REQUIRE_CONFIRMATION",
        },
        kernel.EvidenceStatus: {
            "NOT_REQUIRED",
            "SUFFICIENT",
            "INSUFFICIENT",
            "CONFLICTING",
            "UNAVAILABLE",
            "STALE",
            "INVALID",
        },
        kernel.PatchState: {
            "DETECTED",
            "PROPOSED",
            "EVIDENCE_BOUND",
            "VALIDATED",
            "AWAITING_APPROVAL",
            "APPROVED",
            "COMMITTED",
            "ACTIVE",
            "SUPERSEDED",
            "REJECTED",
            "REVOKED",
        },
        kernel.PersonalMemorySpaceState: {
            "EMPTY",
            "CONFIGURED",
            "ACTIVE",
            "SUSPENDED",
            "ARCHIVED",
            "DELETED_PENDING",
            "DELETED",
        },
        kernel.SharedPromotionState: {
            "SHARED_PROMOTION_PROPOSED",
            "EVIDENCE_REVALIDATED",
            "DOMAIN_REVIEW_REQUIRED",
            "APPROVED_FOR_SHARED",
            "SHARED_PATCH_COMMITTED",
            "REJECTED",
        },
    }
    for enum_type, required_values in expected.items():
        actual = {member.value for member in enum_type}
        if actual != required_values:
            raise ValidationFailure(
                f"{enum_type.__name__} values differ: {sorted(actual)}"
            )


def validate_public_surface() -> None:
    required_symbols = {
        "KnowledgeRoute",
        "ActionPolicy",
        "EvidenceStatus",
        "AnswerStatus",
        "MemoryTrustClass",
        "MemoryConflictType",
        "MemoryTargetScope",
        "ProposalOrigin",
        "PatchState",
        "PersonalMemorySpaceState",
        "SharedPromotionState",
        "ScopeComparisonMode",
        "StorageClass",
        "HatAuthorityDeclaration",
        "HatManifest",
        "HatScopeDimensionDefinition",
        "PersonalMemorySpace",
        "PersonalHatQuotaPolicy",
        "MemoryOwnership",
        "MemoryItem",
        "MemoryConflict",
        "KernelRunIdentity",
        "RoutingDecision",
        "ActionPolicyDecision",
        "EvidenceItem",
        "EvidenceBundle",
        "CorrectionCandidate",
        "CorrectionRequirement",
        "CorrectionPacket",
        "ClaimCandidate",
        "ClaimVerdict",
        "MemoryPatchProposal",
        "MemoryPatchApproval",
        "MemoryPatchCommit",
        "SharedPromotionProposal",
        "ModelExperienceEvent",
        "AuditEvent",
        "HatSdk",
        "canonical_json",
        "canonical_sha256",
        "transition_memory_patch",
        "transition_personal_memory_space",
        "transition_shared_promotion",
    }
    missing = sorted(name for name in required_symbols if not hasattr(kernel, name))
    if missing:
        raise ValidationFailure(f"public contract surface is missing: {missing}")


def validate_runtime_schema_surfaces(
    schemas: dict[str, dict[str, Any]],
) -> None:
    """Keep schema-backed runtime records structurally identical at the root."""

    mappings = {
        "hat-manifest.schema.json": kernel.HatManifest,
        "personal-memory-space.schema.json": kernel.PersonalMemorySpace,
        "memory-patch-proposal.schema.json": kernel.MemoryPatchProposal,
        "correction-packet.schema.json": kernel.CorrectionPacket,
        "audit-event.schema.json": kernel.AuditEvent,
    }
    for schema_name, contract_type in mappings.items():
        schema = schemas[schema_name]
        schema_fields = set(schema["properties"])
        runtime_fields = {
            field.name for field in dataclasses.fields(contract_type)
        }
        if schema_fields != runtime_fields:
            raise ValidationFailure(
                f"{schema_name}: schema/runtime field mismatch; "
                f"schema-only={sorted(schema_fields - runtime_fields)}, "
                f"runtime-only={sorted(runtime_fields - schema_fields)}"
            )
        if schema.get("additionalProperties") is not False:
            raise ValidationFailure(
                f"{schema_name}: top-level additional properties must fail closed"
            )
        version = schema["properties"].get("schema_version")
        if version != {"const": kernel.CONTRACT_SCHEMA_VERSION}:
            raise ValidationFailure(
                f"{schema_name}: schema_version is not explicitly pinned"
            )
        schema_version_field = next(
            field
            for field in dataclasses.fields(contract_type)
            if field.name == "schema_version"
        )
        if schema_version_field.default is not dataclasses.MISSING:
            raise ValidationFailure(
                f"{contract_type.__name__}: schema_version must be explicit"
            )

    for contract_type in (
        kernel.MemoryPatchApproval,
        kernel.MemoryPatchCommit,
        kernel.MemoryItem,
        kernel.SharedPromotionProposal,
    ):
        schema_version_fields = [
            field
            for field in dataclasses.fields(contract_type)
            if field.name == "schema_version"
        ]
        if (
            len(schema_version_fields) != 1
            or schema_version_fields[0].default is not dataclasses.MISSING
        ):
            raise ValidationFailure(
                f"{contract_type.__name__}: authority records require an "
                "explicit schema_version"
            )

    closed_nested_definitions = (
        ("correction-packet.schema.json", "scopeDimension"),
        ("correction-packet.schema.json", "claim"),
        ("correction-packet.schema.json", "evidence"),
        ("correction-packet.schema.json", "memoryConflict"),
        ("correction-packet.schema.json", "correctionRequirement"),
        ("memory-patch-proposal.schema.json", "scopeDimension"),
    )
    for schema_name, definition_name in closed_nested_definitions:
        definition = schemas[schema_name]["$defs"][definition_name]
        if definition.get("additionalProperties") is not False:
            raise ValidationFailure(
                f"{schema_name}#/$defs/{definition_name}: "
                "additional properties must fail closed"
            )


def validate_state_graphs() -> None:
    forbidden_patch_edges = (
        (kernel.PatchState.PROPOSED, kernel.PatchState.ACTIVE),
        (kernel.PatchState.DETECTED, kernel.PatchState.APPROVED),
        (kernel.PatchState.VALIDATED, kernel.PatchState.COMMITTED),
        (kernel.PatchState.REJECTED, kernel.PatchState.ACTIVE),
        (kernel.PatchState.REVOKED, kernel.PatchState.ACTIVE),
    )
    for current, target in forbidden_patch_edges:
        if kernel.memory_patch_transition_allowed(current, target):
            raise ValidationFailure(
                f"forbidden patch edge exists: {current.value}->{target.value}"
            )
    successful_path = (
        kernel.PatchState.DETECTED,
        kernel.PatchState.PROPOSED,
        kernel.PatchState.EVIDENCE_BOUND,
        kernel.PatchState.VALIDATED,
        kernel.PatchState.AWAITING_APPROVAL,
        kernel.PatchState.APPROVED,
        kernel.PatchState.COMMITTED,
        kernel.PatchState.ACTIVE,
    )
    for current, target in zip(successful_path, successful_path[1:]):
        if not kernel.memory_patch_transition_allowed(current, target):
            raise ValidationFailure(
                f"required patch edge is absent: {current.value}->{target.value}"
            )


def validate_domain_neutrality() -> None:
    dataclass_types = (
        kernel.HatManifest,
        kernel.HatScopeDimensionDefinition,
        kernel.ScopeDimension,
        kernel.CorrectionPacket,
        kernel.MemoryPatchProposal,
    )
    field_names = {
        field.name
        for contract in dataclass_types
        for field in dataclasses.fields(contract)
    }
    forbidden_fields = {"jurisdiction", "legal_code", "court", "statute"}
    present = sorted(field_names & forbidden_fields)
    if present:
        raise ValidationFailure(f"domain-specific Kernel fields found: {present}")
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        machine_path = re.compile(
            r"/(?:home|media)/[A-Za-z0-9._-]+/"
        )
        uuid_literal = re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{12}\b"
        )
        if machine_path.search(text) or uuid_literal.search(text):
            raise ValidationFailure(
                f"machine-specific absolute path in {path.relative_to(REPOSITORY_ROOT)}"
            )
    hats_source = (
        SOURCE_ROOT / "aioa_memory_kernel" / "contracts" / "hats.py"
    ).read_text(encoding="utf-8")
    for forbidden_loader in ("importlib.", "__import__(", "exec(", "eval("):
        if forbidden_loader in hats_source:
            raise ValidationFailure(
                f"arbitrary HAT loader primitive found: {forbidden_loader}"
            )


def validate_documentation_inventory() -> None:
    required_docs = (
        "docs/architecture/KNOWLEDGE_KERNEL_CONTRACT_BASELINE_1A.md",
        "docs/architecture/PERSONAL_MEMORY_HATS_1A.md",
        "docs/architecture/MEMORY_TRUST_AND_PRECEDENCE_1A.md",
        "docs/architecture/HAT_SDK_CONTRACT_1A.md",
        "docs/architecture/MULTI_TENANT_ISOLATION_CONTRACT_1A.md",
        "docs/architecture/DATA_OWNERSHIP_AND_STORAGE_CLASSES_1A.md",
        "docs/adr/ADR-001-domain-neutral-kernel.md",
        "docs/adr/ADR-002-knowledge-hat-vs-personal-memory-hat.md",
        "docs/adr/ADR-003-personal-memory-ownership.md",
        "docs/adr/ADR-004-memory-trust-precedence.md",
        "docs/adr/ADR-005-tenant-isolation-and-future-rls.md",
        "docs/adr/ADR-006-personal-vs-global-s3-snapshots.md",
        "docs/adr/ADR-007-memory-patch-approval-and-commit.md",
        "docs/adr/ADR-008-critic-loop-proposal-only-boundary.md",
        "docs/adr/ADR-009-model-experience-is-not-evidence.md",
    )
    missing = [path for path in required_docs if not (REPOSITORY_ROOT / path).is_file()]
    if missing:
        raise ValidationFailure(f"required documentation is missing: {missing}")


def main() -> int:
    schema_paths = sorted((REPOSITORY_ROOT / "schemas").glob("*.schema.json"))
    fixture_paths = sorted((REPOSITORY_ROOT / "tests" / "fixtures").glob("*.json"))
    if len(schema_paths) != 5:
        raise ValidationFailure(f"expected five schemas, found {len(schema_paths)}")
    if not fixture_paths:
        raise ValidationFailure("no JSON fixtures were found")
    schemas: dict[str, dict[str, Any]] = {}
    for path in schema_paths:
        loaded = load_json(path)
        if not isinstance(loaded, dict):
            raise ValidationFailure(f"{path.name}: schema must be an object")
        validate_schema_shape(path, loaded)
        schemas[path.name] = loaded
    fixtures = load_json(
        REPOSITORY_ROOT / "tests" / "fixtures" / "synthetic_contract_fixtures.json"
    )
    if fixtures.get("fixture_kind") != "SYNTHETIC_CONTRACT_FIXTURES":
        raise ValidationFailure("fixture pack is not explicitly synthetic")
    mappings = (
        (
            fixtures["personal_memory_space"],
            schemas["personal-memory-space.schema.json"],
            "fixtures.personal_memory_space",
        ),
        (
            fixtures["personal_patch"],
            schemas["memory-patch-proposal.schema.json"],
            "fixtures.personal_patch",
        ),
        (
            fixtures["correction_packet"],
            schemas["correction-packet.schema.json"],
            "fixtures.correction_packet",
        ),
        (
            fixtures["audit_event"],
            schemas["audit-event.schema.json"],
            "fixtures.audit_event",
        ),
    )
    for instance, schema, location in mappings:
        validate_instance(instance, schema, root_schema=schema, location=location)
    hat_schema = schemas["hat-manifest.schema.json"]
    manifests = fixtures["hat_manifests"]
    if len(manifests) < 2:
        raise ValidationFailure("fixture pack requires two unrelated HAT manifests")
    for index, raw_manifest in enumerate(manifests):
        validate_instance(
            raw_manifest,
            hat_schema,
            root_schema=hat_schema,
            location=f"fixtures.hat_manifests[{index}]",
        )
        kernel.validate_hat_manifest(instantiate_manifest(raw_manifest))
    if manifests[0]["domain_ids"] == manifests[1]["domain_ids"]:
        raise ValidationFailure("HAT fixtures must represent unrelated domains")
    if len(fixtures["tenants"]) < 2 or len(fixtures["users"]) < 2:
        raise ValidationFailure("fixture pack requires two tenants and two users")
    scenario_keys = {
        "generic_source_versions",
        "temporal_update",
        "conflicting_source",
        "missing_evidence_case",
    }
    if set(fixtures["source_scenarios"]) != scenario_keys:
        raise ValidationFailure("synthetic source scenario coverage is incomplete")
    validate_enum_values()
    validate_public_surface()
    validate_runtime_schema_surfaces(schemas)
    validate_state_graphs()
    validate_domain_neutrality()
    validate_documentation_inventory()
    deterministic_left = kernel.canonical_sha256({"b": 2, "a": 1})
    deterministic_right = kernel.canonical_sha256({"a": 1, "b": 2})
    if deterministic_left != deterministic_right:
        raise ValidationFailure("canonical hashing is not deterministic")
    print(
        "Contract validation passed: "
        f"{len(schema_paths)} schemas, {len(fixture_paths)} fixture files, "
        f"{len(manifests)} unrelated HAT manifests, public surface and "
        "state/authority invariants."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationFailure as exc:
        print(f"Contract validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
