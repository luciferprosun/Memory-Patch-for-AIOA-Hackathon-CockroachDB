"""Deterministic structural validator for complete Step 11 parse artifacts."""

from __future__ import annotations

from aioa_memory_kernel.contracts.serialization import sha256_hex

from .models import (
    FindingSeverity,
    ParseArtifact,
    ParseValidationResult,
    ResourceLimits,
)


class ParseArtifactValidator:
    """Validate ranges, hashes, coverage, identities, and quarantine policy."""

    def __init__(self, limits: ResourceLimits | None = None) -> None:
        self.limits = limits or ResourceLimits()

    def validate(self, artifact: ParseArtifact) -> ParseValidationResult:
        reasons: set[str] = set()
        text = artifact.normalized_text
        previous_section_end = -1
        for expected_ordinal, section in enumerate(artifact.sections):
            if section.section_ordinal != expected_ordinal:
                reasons.add("SECTION_RANGE_INVALID")
            if not 0 <= section.normalized_start_offset < section.normalized_end_offset <= len(text):
                reasons.add("SECTION_RANGE_INVALID")
                continue
            if text[section.normalized_start_offset : section.normalized_end_offset] != section.content:
                reasons.add("SECTION_CONTENT_MISMATCH")
            if sha256_hex(section.content) != section.content_sha256:
                reasons.add("SECTION_CONTENT_MISMATCH")
            if section.section_kind.value == "TEXT_BLOCK" and section.normalized_start_offset < previous_section_end:
                reasons.add("SECTION_RANGE_INVALID")
            previous_section_end = max(previous_section_end, section.normalized_end_offset)
        sections_by_id = {section.section_id: section for section in artifact.sections}
        chunks_by_section: dict[str, list[object]] = {
            section.section_id: [] for section in artifact.sections
        }
        for expected_ordinal, chunk in enumerate(artifact.chunks):
            if chunk.chunk_ordinal != expected_ordinal or chunk.section_id not in chunks_by_section:
                reasons.add("CHUNK_RANGE_INVALID")
                continue
            chunks_by_section[chunk.section_id].append(chunk)
            section = sections_by_id[chunk.section_id]
            if (
                chunk.tenant_id != artifact.document.tenant_id
                or chunk.source_id != artifact.document.source_id
                or chunk.hat_scope_id != artifact.document.hat_scope_id
                or chunk.knowledge_version_id
                != artifact.document.knowledge_version_id
                or chunk.document_id != artifact.document.document_id
                or chunk.normalized_start_offset
                < section.normalized_start_offset
                or chunk.normalized_end_offset
                > section.normalized_end_offset
            ):
                reasons.add("CHUNK_RANGE_INVALID")
            if not 0 <= chunk.normalized_start_offset < chunk.normalized_end_offset <= len(text):
                reasons.add("CHUNK_RANGE_INVALID")
                continue
            if text[chunk.normalized_start_offset : chunk.normalized_end_offset] != chunk.content:
                reasons.add("CHUNK_CONTENT_MISMATCH")
            if sha256_hex(chunk.content) != chunk.content_sha256:
                reasons.add("CHUNK_CONTENT_MISMATCH")
            if len(chunk.content) > self.limits.maximum_chunk_length:
                reasons.add("CHUNK_RANGE_INVALID")
        for section in artifact.sections:
            section_chunks = chunks_by_section[section.section_id]
            if not section_chunks:
                reasons.add("CHUNK_COVERAGE_INVALID")
                continue
            ordered = sorted(section_chunks, key=lambda chunk: chunk.section_chunk_ordinal)
            if [chunk.section_chunk_ordinal for chunk in ordered] != list(range(len(ordered))):
                reasons.add("CHUNK_RANGE_INVALID")
            covered_to = section.normalized_start_offset
            for chunk in ordered:
                if chunk.normalized_start_offset > covered_to:
                    reasons.add("CHUNK_COVERAGE_INVALID")
                covered_to = max(covered_to, chunk.normalized_end_offset)
            if covered_to != section.normalized_end_offset:
                reasons.add("CHUNK_COVERAGE_INVALID")
        section_ids = {section.section_id for section in artifact.sections}
        for finding in artifact.findings:
            if not 0 <= finding.normalized_start_offset < finding.normalized_end_offset <= len(text):
                reasons.add("SECTION_RANGE_INVALID")
            if finding.section_id is not None and finding.section_id not in section_ids:
                reasons.add("SECTION_RANGE_INVALID")
        if any(finding.severity is FindingSeverity.BLOCKING for finding in artifact.findings):
            reasons.add("BLOCKING_PROMPT_INJECTION_SIGNAL")
        if artifact.quarantine.required:
            reasons.update(artifact.quarantine.reason_codes)
        ordered_reasons = tuple(sorted(reasons))
        return ParseValidationResult(
            accepted=not ordered_reasons,
            reason_codes=ordered_reasons,
            parse_artifact_digest=artifact.document.parse_artifact_digest,
        )
