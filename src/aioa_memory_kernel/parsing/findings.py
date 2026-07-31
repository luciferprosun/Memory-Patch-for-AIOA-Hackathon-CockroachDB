"""Reviewable deterministic prompt-injection and hidden-content findings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from aioa_memory_kernel.contracts.serialization import sha256_hex

from .errors import ParsingResourceLimitError
from .models import (
    FindingAction,
    FindingCategory,
    FindingSeverity,
    ParsedSection,
    PromptInjectionFinding,
    ResourceLimits,
)


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    category: FindingCategory
    pattern: re.Pattern[str]
    severity: FindingSeverity
    action: FindingAction


_RULES = (
    _Rule(
        "PI_ROLE_MARKER_001",
        FindingCategory.ROLE_OR_SYSTEM_INSTRUCTION_MARKER,
        re.compile(r"(?im)^\s*(?:system|developer|assistant)\s*:\s*\S+"),
        FindingSeverity.WARNING,
        FindingAction.OPERATOR_REVIEW,
    ),
    _Rule(
        "PI_OVERRIDE_001",
        FindingCategory.INSTRUCTION_OVERRIDE_PHRASE,
        re.compile(r"(?i)\bignore\s+(?:all\s+|any\s+|the\s+)?previous\s+instructions?\b"),
        FindingSeverity.BLOCKING,
        FindingAction.QUARANTINE,
    ),
    _Rule(
        "PI_TOOL_EXEC_001",
        FindingCategory.TOOL_OR_COMMAND_EXECUTION_REQUEST,
        re.compile(r"(?i)\b(?:run|execute|invoke|call)\s+(?:this\s+)?(?:shell|command|tool|terminal)\b"),
        FindingSeverity.BLOCKING,
        FindingAction.QUARANTINE,
    ),
    _Rule(
        "PI_SECRET_EXFIL_001",
        FindingCategory.SECRET_OR_CREDENTIAL_EXFILTRATION_REQUEST,
        re.compile(r"(?i)\b(?:reveal|send|print|exfiltrat\w*)\b.{0,48}\b(?:password|secret|token|credential)s?\b"),
        FindingSeverity.BLOCKING,
        FindingAction.QUARANTINE,
    ),
    _Rule(
        "PI_REMOTE_001",
        FindingCategory.REMOTE_OR_INDIRECT_INSTRUCTION,
        re.compile(r"(?i)\b(?:follow|obey|execute)\b.{0,40}\binstructions?\b.{0,40}\b(?:remote|website|url|link)\b"),
        FindingSeverity.WARNING,
        FindingAction.OPERATOR_REVIEW,
    ),
    _Rule(
        "PI_HIDDEN_MARKUP_001",
        FindingCategory.HIDDEN_MARKUP_OR_COMMENT_INSTRUCTION,
        re.compile(r"(?is)<!--.{0,256}(?:ignore|instruction|system|execute).{0,256}-->"),
        FindingSeverity.WARNING,
        FindingAction.OPERATOR_REVIEW,
    ),
    _Rule(
        "PI_ENCODED_001",
        FindingCategory.ENCODED_OR_OBFUSCATED_INSTRUCTION_SIGNAL,
        re.compile(r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{40,}={0,2}|[0-9A-Fa-f]{64,})(?![A-Za-z0-9+/])"),
        FindingSeverity.WARNING,
        FindingAction.OPERATOR_REVIEW,
    ),
    _Rule(
        "PI_RAG_POISON_001",
        FindingCategory.RAG_POISONING_OR_RETRIEVAL_MANIPULATION_SIGNAL,
        re.compile(r"(?i)\b(?:rank|retrieve|retrieval|search)\b.{0,40}\b(?:always|first|highest|prioriti[sz]e)\b"),
        FindingSeverity.WARNING,
        FindingAction.OPERATOR_REVIEW,
    ),
)

_ZERO_WIDTH_OR_BIDI = re.compile(
    "[\u200b\u200c\u200d\u202a-\u202e\u2060\u2066-\u2069\ufeff]"
)
_QUOTED_CONTEXT = re.compile(
    r"(?i)\b(?:example|quoted|quotation|educational|training|detection|security\s+text)\b"
)


def _section_for_offset(
    sections: tuple[ParsedSection, ...],
    start: int,
    end: int,
) -> str | None:
    matches = [
        section
        for section in sections
        if section.normalized_start_offset <= start
        and end <= section.normalized_end_offset
    ]
    if not matches:
        return None
    matches.sort(key=lambda section: section.normalized_end_offset - section.normalized_start_offset)
    return matches[0].section_id


def scan_security_findings(
    document_id: str,
    text: str,
    sections: tuple[ParsedSection, ...],
    limits: ResourceLimits,
) -> tuple[PromptInjectionFinding, ...]:
    findings: list[PromptInjectionFinding] = []

    def record(
        rule: _Rule,
        start: int,
        end: int,
        *,
        severity: FindingSeverity | None = None,
        action: FindingAction | None = None,
    ) -> None:
        bounded_start = max(0, start - 48)
        bounded_end = min(len(text), end + 48)
        findings.append(
            PromptInjectionFinding(
                document_id=document_id,
                finding_id="",
                rule_id=rule.rule_id,
                category=rule.category,
                severity=severity or rule.severity,
                normalized_start_offset=start,
                normalized_end_offset=end,
                section_id=_section_for_offset(sections, start, end),
                evidence_excerpt_sha256=sha256_hex(text[bounded_start:bounded_end]),
                action=action or rule.action,
            )
        )
        if len(findings) > limits.maximum_security_findings:
            raise ParsingResourceLimitError(
                "security finding count exceeds the policy limit",
                sanitized_code="SECURITY_FINDING_LIMIT_EXCEEDED",
            )

    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            context = text[max(0, match.start() - 96) : min(len(text), match.end() + 96)]
            if rule.severity is FindingSeverity.BLOCKING and (
                _QUOTED_CONTEXT.search(context)
                or (match.start() > 0 and text[match.start() - 1] in "'\"`")
            ):
                record(
                    rule,
                    match.start(),
                    match.end(),
                    severity=FindingSeverity.INFO,
                    action=FindingAction.RECORD_ONLY,
                )
            else:
                record(rule, match.start(), match.end())
    zero_rule = _Rule(
        "PI_INVISIBLE_001",
        FindingCategory.ZERO_WIDTH_OR_BIDI_CONTROL_SIGNAL,
        _ZERO_WIDTH_OR_BIDI,
        FindingSeverity.WARNING,
        FindingAction.OPERATOR_REVIEW,
    )
    for match in _ZERO_WIDTH_OR_BIDI.finditer(text):
        record(zero_rule, match.start(), match.end())
    findings.sort(key=lambda item: (item.normalized_start_offset, item.rule_id, item.finding_id))
    return tuple(findings)
