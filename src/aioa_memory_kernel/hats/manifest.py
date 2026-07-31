"""Strict local-only HAT manifest decoding and compatibility policy."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from aioa_memory_kernel.contracts import HatManifest, HatScopeDimensionDefinition, HatSecurityPolicy, MissingDimensionBehavior, ScopeComparisonMode, ScopeValueType
from aioa_memory_kernel.contracts.serialization import canonical_json_bytes, canonical_sha256
from .errors import HatRegistryError
from .models import CAPABILITY_METHODS, KERNEL_API_VERSION, CompatibilityDecision, ManifestIdentity

MAX_MANIFEST_BYTES = 262_144
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")
_COMPARATOR = re.compile(r"(==|=|>=|<=|>|<)([^,]+)")
_FORBIDDEN_NESTED = re.compile(r"(?:^|_)(?:path|module|entry_point|package_url|shell|command|credential|secret|password|token)(?:$|_)", re.I)

def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HatRegistryError("MANIFEST_DECODE_FAILED", "duplicate JSON member")
        result[key] = value
    return result

def _bad_constant(value: str) -> None:
    raise HatRegistryError("MANIFEST_DECODE_FAILED", f"non-finite JSON number: {value}")

def parse_semver(value: str) -> tuple[int, int, int, tuple[str, ...] | None]:
    match = _SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise HatRegistryError("INVALID_HAT_VERSION", "invalid semantic version")
    return int(match[1]), int(match[2]), int(match[3]), tuple(match[4].split(".")) if match[4] else None

def compatibility(expression: str, current: str = KERNEL_API_VERSION) -> bool:
    current_version = parse_semver(current)[:3]
    terms = expression.split(",")
    if not terms or any(not term for term in terms):
        raise HatRegistryError("INVALID_COMPATIBILITY_EXPRESSION", "invalid compatibility expression")
    for term in terms:
        match = _COMPARATOR.fullmatch(term)
        if not match:
            raise HatRegistryError("INVALID_COMPATIBILITY_EXPRESSION", "unsupported compatibility expression")
        target = parse_semver(match[2])[:3]
        op = match[1]
        accepted = {"=": current_version == target, "==": current_version == target, ">": current_version > target, ">=": current_version >= target, "<": current_version < target, "<=": current_version <= target}[op]
        if not accepted:
            return False
    return True

def _bounded(value: Any, depth: int = 0) -> None:
    if depth > 24:
        raise HatRegistryError("MANIFEST_DECODE_FAILED", "manifest depth limit exceeded")
    if isinstance(value, dict):
        if len(value) > 256:
            raise HatRegistryError("MANIFEST_DECODE_FAILED", "manifest member limit exceeded")
        for key, nested in value.items():
            if len(key) > 256 or _FORBIDDEN_NESTED.search(key):
                raise HatRegistryError("HIDDEN_AUTHORITY_DECLARATION", "forbidden nested declaration")
            _bounded(nested, depth + 1)
    elif isinstance(value, list):
        if len(value) > 1024:
            raise HatRegistryError("MANIFEST_DECODE_FAILED", "manifest array limit exceeded")
        for nested in value: _bounded(nested, depth + 1)
    elif isinstance(value, str):
        if len(value) > 16384 or "\x00" in value or value.startswith(("/", "../", "http://", "https://", "file://")):
            raise HatRegistryError("MANIFEST_CONTRACT_REJECTED", "unsafe or oversized manifest string")

def decode_manifest(raw: bytes, *, schema_path: Path) -> ManifestIdentity:
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES or b"\x00" in raw:
        raise HatRegistryError("MANIFEST_DECODE_FAILED", "invalid manifest bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
        data = json.loads(text, object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except HatRegistryError: raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HatRegistryError("MANIFEST_DECODE_FAILED", "strict JSON decode failed") from exc
    if not isinstance(data, dict):
        raise HatRegistryError("MANIFEST_SCHEMA_REJECTED", "manifest must be an object")
    _bounded(data)
    allowed = {"schema_version","hat_id","hat_version","display_name","domain_ids","kernel_api_compatibility","supported_languages","scope_dimension_definitions","capabilities","source_authority_policy","retrieval_contract","claim_contract","conflict_contract","memory_policy","security_policy","extension_points"}
    if set(data) != allowed:
        raise HatRegistryError("MANIFEST_SCHEMA_REJECTED", "required or unknown top-level property")
    try:
        policy = data["security_policy"]
        if policy != {"external_action_authority":"NONE","canonical_write_authority":"NONE","patch_approval_authority":"NONE","patch_commit_authority":"NONE","executable_user_code":False,"private_memory_access":False}:
            raise HatRegistryError("HIDDEN_AUTHORITY_DECLARATION", "security policy is not zero-authority")
        definitions = tuple(HatScopeDimensionDefinition(name=x["name"], value_type=ScopeValueType(x["value_type"]), comparison_mode=ScopeComparisonMode(x["comparison_mode"]), required=x["required"], default_behavior=MissingDimensionBehavior(x["default_behavior"]), missing_creates_ambiguous=x["missing_creates_ambiguous"], default_value=x.get("default_value"), description=x.get("description", "")) for x in data["scope_dimension_definitions"])
        manifest = HatManifest(schema_version=data["schema_version"], hat_id=data["hat_id"], hat_version=data["hat_version"], display_name=data["display_name"], domain_ids=tuple(data["domain_ids"]), kernel_api_compatibility=data["kernel_api_compatibility"], supported_languages=tuple(data["supported_languages"]), scope_dimension_definitions=definitions, capabilities=tuple(data["capabilities"]), source_authority_policy=data["source_authority_policy"], retrieval_contract=data["retrieval_contract"], claim_contract=data["claim_contract"], conflict_contract=data["conflict_contract"], memory_policy=data["memory_policy"], security_policy=HatSecurityPolicy(), extension_points=data["extension_points"])
        parse_semver(manifest.hat_version)
        unknown = set(manifest.capabilities) - set(CAPABILITY_METHODS)
        if unknown: raise HatRegistryError("UNKNOWN_CAPABILITY", "unknown capability")
        for contract in (manifest.source_authority_policy, manifest.retrieval_contract, manifest.claim_contract, manifest.conflict_contract, manifest.memory_policy):
            if not any(key in contract for key in ("policy_version", "contract_version")):
                raise HatRegistryError("MANIFEST_CONTRACT_REJECTED", "nested contract lacks version identity")
    except HatRegistryError: raise
    except Exception as exc:
        raise HatRegistryError("MANIFEST_CONTRACT_REJECTED", "typed manifest construction failed") from exc
    typed = canonical_sha256(manifest)
    return ManifestIdentity(manifest, hashlib.sha256(raw).hexdigest(), hashlib.sha256(canonical_json_bytes(data)).hexdigest(), typed, hashlib.sha256(schema_path.read_bytes()).hexdigest())

def decide_compatibility(identity: ManifestIdentity) -> CompatibilityDecision:
    return CompatibilityDecision.COMPATIBLE if compatibility(identity.manifest.kernel_api_compatibility) else CompatibilityDecision.INCOMPATIBLE_KERNEL_API
