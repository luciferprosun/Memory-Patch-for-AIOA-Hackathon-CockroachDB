"""Immutable acquisition policy and evidence records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from pathlib import PurePosixPath

from aioa_memory_kernel.contracts.serialization import canonical_sha256

GIB = 1024**3


class SourceStatus(str, Enum):
    COMPLETE = "COMPLETE"
    SKIPPED_SAFE = "SKIPPED_SAFELY_WITH_EXACT_REASON"
    BLOCKED_CHANGED = "BLOCKED_BY_CHANGED_OFFICIAL_CONDITIONS"
    PARTIAL = "PARTIAL_SAFE_RESUME_REQUIRED"
    FAILED = "FAILED_SAFELY"


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    schema_version: str = "1.0.0"
    policy_id: str = "german-law-official-corpus-acquisition-1a"
    expected_device_reference: str = (
        "external-volume-sha256:"
        "f6aff52dd4102add848e14b8037edb14d6fab427b6b319c5cf3a2cca4e562c20"
    )
    target_relative_path: str = "HAT's libary/German Law Official Corpus 1A"
    seed_relative_path: str = "HAT's libary/German law.zip"
    seed_sha256: str = (
        "973abc34125545c3fcdfe5ba2e8c6031fe3ccfd0134de380f50d1f2980b29446"
    )
    seed_length: int = 774_599_792
    maximum_root_bytes: int = 120 * GIB
    initial_minimum_free_bytes: int = 350 * GIB
    final_minimum_free_bytes: int = 250 * GIB
    maximum_requests: int = 20_000
    maximum_response_bytes: int = 2 * GIB
    maximum_archive_expanded_bytes: int = 10 * GIB
    maximum_retries: int = 4
    connect_timeout_seconds: int = 30
    read_timeout_seconds: int = 180
    maximum_redirects: int = 3
    user_agent: str = (
        "Memory-Patch-German-Law-Corpus/1A "
        "research-and-provenance-acquisition; "
        "contact=https://github.com/luciferprosun/"
        "Memory-Patch-for-AIOA-Hackathon-CockroachDB"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported acquisition policy schema")
        for value in (self.target_relative_path, self.seed_relative_path):
            parsed = PurePosixPath(value)
            if (
                not value
                or value != value.strip()
                or "\\" in value
                or "\x00" in value
                or parsed.is_absolute()
                or str(parsed) != value
                or any(part in {"", ".", ".."} for part in parsed.parts)
            ):
                raise ValueError("acquisition path must be safe and mount-relative")
        device_digest = self.expected_device_reference.removeprefix(
            "external-volume-sha256:"
        )
        if (
            not self.expected_device_reference.startswith(
                "external-volume-sha256:"
            )
            or re.fullmatch(r"[0-9a-f]{64}", device_digest) is None
        ):
            raise ValueError("device reference is malformed")
        if re.fullmatch(r"[0-9a-f]{64}", self.seed_sha256) is None:
            raise ValueError("seed SHA-256 is malformed")
        if self.seed_length <= 0:
            raise ValueError("seed length is malformed")
        if not (
            self.maximum_root_bytes > 0
            and self.initial_minimum_free_bytes > self.final_minimum_free_bytes
            and self.final_minimum_free_bytes > 0
            and self.maximum_requests > 0
            and self.maximum_response_bytes > 0
            and self.maximum_archive_expanded_bytes > 0
            and 0 <= self.maximum_retries <= 8
            and self.connect_timeout_seconds > 0
            and self.read_timeout_seconds > 0
            and 1 <= self.maximum_redirects <= 5
        ):
            raise ValueError("acquisition bounds are malformed")

    @property
    def digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class HttpObjectReceipt:
    schema_version: str
    source_catalog_id: str
    requested_url: str
    final_url: str
    retrieved_at: str
    http_status: int
    content_type: str
    content_length_header: str | None
    etag: str | None
    last_modified: str | None
    publisher_checksum: str | None
    publisher_checksum_algorithm: str | None
    local_sha256: str
    byte_length: int
    relative_output_path: str
    terms_reference: str
    license_reference: str
    robots_reference: str
    request_sequence: int
    retry_count: int
    validation_status: str
    quarantine_reasons: tuple[str, ...]
    sidecar_digest: str = ""

    def with_digest(self) -> "HttpObjectReceipt":
        from dataclasses import replace

        digest = canonical_sha256(self, exclude_fields=("sidecar_digest",))
        return replace(self, sidecar_digest=digest)


__all__ = ["AcquisitionPolicy", "HttpObjectReceipt", "SourceStatus"]
