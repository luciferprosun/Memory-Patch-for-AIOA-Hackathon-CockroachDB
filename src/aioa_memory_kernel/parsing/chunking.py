"""Model-neutral deterministic character chunking within parsed sections."""

from __future__ import annotations

import unicodedata

from .errors import ParsingResourceLimitError, ParsingValidationError
from .models import (
    ChunkingProfile,
    LanguageTag,
    ParsedChunk,
    ParsedSection,
    ResourceLimits,
)


_VARIATION_SELECTORS = tuple(range(0xFE00, 0xFE10)) + tuple(
    range(0xE0100, 0xE01F0)
)


def _unsafe_split(text: str, position: int) -> bool:
    if position <= 0 or position >= len(text):
        return False
    next_code = ord(text[position])
    return (
        unicodedata.combining(text[position]) != 0
        or next_code in _VARIATION_SELECTORS
        or text[position] == "\u200d"
        or text[position - 1] == "\u200d"
    )


def _safe_cut(text: str, proposed: int, maximum: int) -> int:
    cut = proposed
    while cut < maximum and _unsafe_split(text, cut):
        cut += 1
    if cut <= maximum and not _unsafe_split(text, cut):
        return cut
    cut = proposed
    while cut > 0 and _unsafe_split(text, cut):
        cut -= 1
    return cut


def _boundary(
    text: str,
    start: int,
    section_end: int,
    profile: ChunkingProfile,
) -> int:
    hard = min(start + profile.maximum_characters, section_end)
    if hard == section_end:
        return hard
    target = min(start + profile.target_characters, hard)
    low = max(start + profile.minimum_characters, target - profile.boundary_search_window)
    candidate_text = text[low:hard]
    priorities: tuple[tuple[str, ...], ...] = (
        ("\n",),
        (". ", "! ", "? ", ".\n", "!\n", "?\n"),
        (" ", "\t"),
    )
    for markers in priorities:
        best = -1
        marker_length = 0
        for marker in markers:
            found = candidate_text.rfind(marker)
            if found > best:
                best = found
                marker_length = len(marker)
        if best >= 0:
            cut = low + best + marker_length
            cut = _safe_cut(text, cut, hard)
            if cut > start:
                return cut
    cut = _safe_cut(text, hard, hard)
    if cut <= start:
        raise ParsingValidationError(
            "no safe bounded chunk boundary is available",
            sanitized_code="CHUNK_RANGE_INVALID",
        )
    return cut


def chunk_sections(
    *,
    tenant_id: str,
    source_id: str,
    hat_scope_id: str,
    knowledge_version_id: str,
    document_id: str,
    normalized_text: str,
    sections: tuple[ParsedSection, ...],
    profile: ChunkingProfile,
    limits: ResourceLimits,
    language_tag: LanguageTag | None,
) -> tuple[ParsedChunk, ...]:
    chunks: list[ParsedChunk] = []
    for section in sections:
        start = section.normalized_start_offset
        section_end = section.normalized_end_offset
        section_ordinal = 0
        previous_end: int | None = None
        while start < section_end:
            end = _boundary(normalized_text, start, section_end, profile)
            content = normalized_text[start:end]
            if not content or len(content) > profile.maximum_characters or len(content) > limits.maximum_chunk_length:
                raise ParsingValidationError(
                    "chunk length violates the versioned profile",
                    sanitized_code="CHUNK_RANGE_INVALID",
                )
            overlap = 0 if previous_end is None else previous_end - start
            chunks.append(
                ParsedChunk(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    hat_scope_id=hat_scope_id,
                    knowledge_version_id=knowledge_version_id,
                    document_id=document_id,
                    section_id=section.section_id,
                    chunk_id="",
                    chunk_ordinal=len(chunks),
                    section_chunk_ordinal=section_ordinal,
                    normalized_start_offset=start,
                    normalized_end_offset=end,
                    content=content,
                    chunking_profile_digest=profile.profile_digest,
                    overlap_prefix_characters=overlap,
                    language_tag=language_tag,
                )
            )
            if len(chunks) > limits.maximum_chunks:
                raise ParsingResourceLimitError(
                    "document exceeds the chunk-count limit",
                    sanitized_code="RESOURCE_LIMIT_EXCEEDED",
                )
            if end == section_end:
                break
            next_start = max(section.normalized_start_offset, end - profile.overlap_characters)
            while next_start < end and _unsafe_split(normalized_text, next_start):
                next_start += 1
            if next_start >= end:
                next_start = end
            previous_end = end
            start = next_start
            section_ordinal += 1
    return tuple(chunks)
