"""Deterministic, exact-span Draft V1 claim extraction for Step 23."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from aioa_memory_kernel.modeling import DraftV1, verify_draft_v1_hash

from .models import (
    MAX_CLAIMS,
    ClaimAtomicity,
    ClaimBoundaryError,
    ClaimProcessingPolicy,
    ClaimReasonCode,
    ClaimRecord,
    ClaimType,
    load_claim_processing_policy,
    normalize_claim_for_match,
    reason_codes,
)


_BULLET_PREFIX = re.compile(r"(?:[-*•]\s+|\(?\d{1,3}[.)]\s+)")
_COMPOUND = re.compile(
    r"\b(?:und|oder|aber|sowie|während|hingegen|and|or|but|whereas)\b",
    re.IGNORECASE,
)
_NOMINAL_COORDINATION = re.compile(
    r"\b(?P<left>[A-ZÄÖÜ][^\W_]*)\s+"
    r"(?:und|oder|sowie|and|or)\s+"
    r"(?P<right>[A-ZÄÖÜ][^\W_]*)\b"
)
_LEGAL_ROMAN_REFERENCES = frozenset({"I", "II", "III"})
_DATE_OR_NUMBER = re.compile(r"(?:\b\d{1,4}(?:[./-]\d{1,2}){0,2}\b|%)")
_NON_FACTUAL_PREFIXES = (
    "hallo",
    "guten tag",
    "danke",
    "vielen dank",
    "bitte beachten",
    "meiner meinung nach",
    "ich denke",
    "ich hoffe",
    "dies ist nur ein entwurf",
    "ich bin ein sprachmodell",
)
_SOURCE_MARKERS = (
    "laut ",
    "quelle",
    "amtlich",
    "offiziell",
    "official source",
    "source says",
)
_TEMPORAL_MARKERS = (
    "aktuell",
    "derzeit",
    "gegenwärtig",
    "seit ",
    " bis ",
    "wirksam",
    "in kraft",
    "aufgehoben",
    "außer kraft",
    "superseded",
    "repealed",
    "effective",
    "as of",
)
_LEGAL_MARKERS = (
    "§",
    "gesetz",
    "verordnung",
    "vorschrift",
    "anspruch",
    "artikel ",
    "art. ",
    "recht",
    "pflicht",
    "darf ",
    "muss ",
)
_RELATIONAL_MARKERS = (
    "bezieht sich",
    "gehört zu",
    "entspricht",
    "ist gleich",
    "is related to",
    "equals",
)
_ABBREVIATIONS = frozenset(
    {
        "abs.",
        "art.",
        "bzw.",
        "ca.",
        "dr.",
        "ggf.",
        "nr.",
        "prof.",
        "sog.",
        "u.a.",
        "z.b.",
    }
)
_GERMAN_MONTH_NAMES = frozenset(
    {
        "januar",
        "februar",
        "märz",
        "april",
        "mai",
        "juni",
        "juli",
        "august",
        "september",
        "oktober",
        "november",
        "dezember",
    }
)


@dataclass(frozen=True, slots=True)
class TextSpan:
    start_offset: int
    end_offset: int
    text: str


def _trim_span(text: str, start: int, end: int) -> TextSpan | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    prefix = _BULLET_PREFIX.match(text, start, end)
    if prefix is not None:
        start = prefix.end()
        while start < end and text[start].isspace():
            start += 1
    if start >= end:
        return None
    return TextSpan(start, end, text[start:end])


def _period_is_boundary(text: str, index: int) -> bool:
    if index > 0 and index + 1 < len(text):
        if text[index - 1].isdigit() and text[index + 1].isdigit():
            return False
    token_start = index
    while token_start > 0 and not text[token_start - 1].isspace():
        token_start -= 1
    ordinal = text[token_start:index]
    if ordinal.isdigit():
        next_start = index + 1
        while next_start < len(text) and text[next_start].isspace():
            next_start += 1
        next_end = next_start
        while next_end < len(text) and text[next_end].isalpha():
            next_end += 1
        if text[next_start:next_end].casefold() in _GERMAN_MONTH_NAMES:
            return False
    if (
        ordinal in _LEGAL_ROMAN_REFERENCES
        and index + 1 < len(text)
        and text[index + 1] in " \t"
    ):
        next_start = index + 1
        while next_start < len(text) and text[next_start] in " \t":
            next_start += 1
        if next_start < len(text) and text[next_start].islower():
            return False
    if text[token_start : index + 1].casefold() in _ABBREVIATIONS:
        return False
    return index + 1 == len(text) or text[index + 1].isspace()


def _without_nominal_coordination(text: str) -> str:
    """Remove only connectors joining adjacent capitalized nominal tokens."""

    value = text
    while True:
        value, replacements = _NOMINAL_COORDINATION.subn(
            r"\g<left> \g<right>",
            value,
        )
        if replacements == 0:
            return value


def exact_text_spans(text: str) -> tuple[TextSpan, ...]:
    """Split text without rewriting it; offsets are Unicode code points."""

    if not isinstance(text, str) or unicodedata.normalize("NFC", text) != text:
        raise ClaimBoundaryError(ClaimReasonCode.CLAIM_TEXT_MISMATCH)
    spans: list[TextSpan] = []
    start = 0
    for index, character in enumerate(text):
        boundary = character in "\n\r;?!"
        if character == ".":
            boundary = _period_is_boundary(text, index)
        if not boundary:
            continue
        end = index if character in "\n\r" else index + 1
        span = _trim_span(text, start, end)
        if span is not None:
            spans.append(span)
        start = index + 1
    span = _trim_span(text, start, len(text))
    if span is not None:
        spans.append(span)
    return tuple(spans)


def classify_claim(text: str) -> tuple[ClaimType, ClaimAtomicity]:
    normalized = normalize_claim_for_match(text)
    if text.rstrip().endswith("?") or normalized.startswith(_NON_FACTUAL_PREFIXES):
        return ClaimType.NON_FACTUAL, ClaimAtomicity.NON_FACTUAL
    if any(marker in normalized for marker in _SOURCE_MARKERS):
        claim_type = ClaimType.SOURCE_ASSERTION
    elif any(marker in normalized for marker in _TEMPORAL_MARKERS):
        claim_type = ClaimType.TEMPORAL
    elif any(marker in normalized for marker in _LEGAL_MARKERS):
        claim_type = ClaimType.LEGAL_NORM
    elif _DATE_OR_NUMBER.search(normalized):
        claim_type = ClaimType.QUANTITATIVE
    elif any(marker in normalized for marker in _RELATIONAL_MARKERS):
        claim_type = ClaimType.RELATIONAL
    else:
        claim_type = ClaimType.FACTUAL
    atomicity = (
        ClaimAtomicity.COMPOUND
        if _COMPOUND.search(_without_nominal_coordination(text))
        else ClaimAtomicity.ATOMIC
    )
    return claim_type, atomicity


def extract_claims(
    draft_v1: DraftV1,
    *,
    scope_dimensions: tuple,
    policy: ClaimProcessingPolicy | None = None,
) -> tuple[ClaimRecord, ...]:
    """Extract bounded claims from exact Draft V1 code-point spans."""

    if not isinstance(draft_v1, DraftV1):
        raise ClaimBoundaryError(ClaimReasonCode.INPUT_HASH_INVALID)
    verify_draft_v1_hash(draft_v1)
    approved = policy or load_claim_processing_policy()
    if approved != load_claim_processing_policy():
        raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
    spans = exact_text_spans(draft_v1.draft_text)
    if len(spans) > approved.maximum_claims:
        raise ClaimBoundaryError(ClaimReasonCode.CLAIM_SPAN_INVALID)
    claims: list[ClaimRecord] = []
    for span in spans:
        claim_type, atomicity = classify_claim(span.text)
        codes = [ClaimReasonCode.CLAIM_EXTRACTED]
        if atomicity is ClaimAtomicity.COMPOUND:
            codes.append(ClaimReasonCode.CLAIM_COMPOUND)
        if atomicity is ClaimAtomicity.NON_FACTUAL:
            codes.append(ClaimReasonCode.CLAIM_NON_FACTUAL)
        claim = ClaimRecord(
            draft_id=draft_v1.draft_id,
            draft_v1_hash=draft_v1.draft_hash,
            start_offset=span.start_offset,
            end_offset=span.end_offset,
            exact_claim_text=span.text,
            normalized_match_text=normalize_claim_for_match(span.text),
            claim_type=claim_type,
            atomicity=atomicity,
            scope_dimensions=scope_dimensions,
            reason_codes=reason_codes(*codes),
        )
        if draft_v1.draft_text[claim.start_offset : claim.end_offset] != claim.exact_claim_text:
            raise ClaimBoundaryError(ClaimReasonCode.CLAIM_TEXT_MISMATCH)
        claims.append(claim)
    ordered = tuple(sorted(claims, key=lambda item: (item.start_offset, item.end_offset, item.claim_id)))
    if len({item.claim_id for item in ordered}) != len(ordered):
        raise ClaimBoundaryError(ClaimReasonCode.CLAIM_TEXT_MISMATCH)
    return ordered


__all__ = [
    "TextSpan",
    "classify_claim",
    "exact_text_spans",
    "extract_claims",
]
