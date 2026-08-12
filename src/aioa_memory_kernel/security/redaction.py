"""Shared bounded secret detection and safe diagnostic redaction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


FORBIDDEN_SECRET_KEY_FRAGMENTS = (
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "aws_secret",
    "bearer",
    "client_secret",
    "credential",
    "database_url",
    "github_token",
    "password",
    "presigned",
    "private_key",
    "refresh_token",
    "secret",
    "secret_access_key",
    "session_token",
)
SAFE_SECURITY_METADATA_KEYS = frozenset(
    {
        "browser_privileged_secret_hits",
        "browser_rendered_secret_hits",
        "credential_count",
        "credential_boundary_summary",
        "credential_inventory_digest",
        "credential_matrix_digest",
        "master_credential_fallback",
        "missing_commit_helper_credential_failed_closed",
        "missing_secret_fail_closed",
        "privileged_secret_hits",
        "production_secret_rotations",
        "provider_secret_redaction",
        "rendered_secret_hits",
        "secret_leakage_count",
        "secret_scan_summary",
    }
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*[^\r\n]+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|refresh[_-]?token|"
        r"secret[_-]?access[_-]?key|session[_-]?token)\s*[:=]\s*"
        r"(?:\"[^\r\n\"]*\"|'[^\r\n']*'|[^\s,;]+)"
    ),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|cockroachdb)://[^\s]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"\b(?:github_pat_|ghp_|sk-ant-|sk-proj-|sk-)[A-Za-z0-9_-]{12,}\b"
    ),
    re.compile(r"(?i)\bx-amz-(?:credential|signature)=[^&\s]+"),
    re.compile(
        r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----[\s\S]*?"
        r"(?:-----END (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|$)"
    ),
)
_MACHINE_PATH = re.compile(r"(?:^|[\s'\"])(?:/home/|/media/|file://|~/|[A-Za-z]:[\\/])")


def _key_forbidden(value: str) -> bool:
    normalized = value.casefold().replace("-", "_")
    if normalized in SAFE_SECURITY_METADATA_KEYS:
        return False
    return any(fragment in normalized for fragment in FORBIDDEN_SECRET_KEY_FRAGMENTS)


def _text_forbidden(value: str, *, reject_machine_paths: bool) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_VALUE_PATTERNS) or (
        reject_machine_paths and _MACHINE_PATH.search(value) is not None
    )


def contains_secret_material(
    value: object,
    *,
    reject_machine_paths: bool = False,
) -> bool:
    if isinstance(value, Mapping):
        return any(
            not isinstance(key, str)
            or _key_forbidden(key)
            or contains_secret_material(item, reject_machine_paths=reject_machine_paths)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            contains_secret_material(item, reject_machine_paths=reject_machine_paths)
            for item in value
        )
    if isinstance(value, str):
        return _text_forbidden(value, reject_machine_paths=reject_machine_paths)
    return False


def assert_secret_free(
    value: object,
    *,
    surface: str,
    reject_machine_paths: bool = False,
) -> None:
    if contains_secret_material(value, reject_machine_paths=reject_machine_paths):
        raise ValueError(f"{surface} contains forbidden secret material")


def redact_text(value: object, *, maximum_bytes: int = 512) -> str:
    text = value if isinstance(value, str) else type(value).__name__
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub("<redacted>", text)
    if _MACHINE_PATH.search(text) is not None:
        text = "<redacted-path>"
    encoded = text.encode("utf-8", errors="replace")[:maximum_bytes]
    return encoded.decode("utf-8", errors="ignore")


def redact_exception(error: BaseException) -> str:
    code = getattr(error, "sanitized_code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code):
        return code
    return type(error).__name__.upper()


__all__ = [
    "FORBIDDEN_SECRET_KEY_FRAGMENTS",
    "SAFE_SECURITY_METADATA_KEYS",
    "assert_secret_free",
    "contains_secret_material",
    "redact_exception",
    "redact_text",
]
