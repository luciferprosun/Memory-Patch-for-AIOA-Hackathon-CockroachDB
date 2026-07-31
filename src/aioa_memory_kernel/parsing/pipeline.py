"""Pure deterministic Step 11 parsing, sectioning, finding, and chunking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .chunking import chunk_sections
from .findings import scan_security_findings
from .models import (
    ChunkingProfile,
    FindingSeverity,
    LanguageTag,
    ParseArtifact,
    ParsedDocument,
    ParsedSection,
    QuarantineDecision,
    ResourceLimits,
    SECURITY_RULESET_NAME,
    SECURITY_RULESET_VERSION,
)
from .registry import ParserRegistry


@dataclass(frozen=True, slots=True)
class ParsingRequest:
    tenant_id: str
    owner_user_id: str | None
    saga_id: str
    source_id: str
    snapshot_id: str
    knowledge_version_id: str
    knowledge_version_ordinal: int
    hat_scope_id: str
    s3_version_id: str
    locked_storage_evidence_digest: str
    input_sha256: str
    input_byte_length: int
    media_type: str
    completed_at: datetime
    language_tag: LanguageTag | None = None


class GenericParsingPipeline:
    """No-I/O deterministic pipeline over exact immutable snapshot bytes."""

    def __init__(
        self,
        *,
        registry: ParserRegistry | None = None,
        limits: ResourceLimits | None = None,
        chunking_profile: ChunkingProfile | None = None,
    ) -> None:
        self.registry = registry or ParserRegistry()
        self.limits = limits or ResourceLimits()
        self.chunking_profile = chunking_profile or ChunkingProfile()

    def parse(self, request: ParsingRequest, payload: bytes) -> ParseArtifact:
        profile, parsed = self.registry.parse(
            request.media_type,
            payload,
            expected_sha256=request.input_sha256,
            expected_length=request.input_byte_length,
            limits=self.limits,
        )
        document_identity = canonical_sha256(
            {
                "contract": "ParsedDocumentIdentity1A",
                "tenant_id": request.tenant_id,
                "owner_user_id": request.owner_user_id,
                "saga_id": request.saga_id,
                "source_id": request.source_id,
                "snapshot_id": request.snapshot_id,
                "knowledge_version_id": request.knowledge_version_id,
                "knowledge_version_ordinal": request.knowledge_version_ordinal,
                "hat_scope_id": request.hat_scope_id,
                "s3_version_id": request.s3_version_id,
                "locked_storage_evidence_digest": request.locked_storage_evidence_digest,
                "input_sha256": request.input_sha256,
                "input_byte_length": request.input_byte_length,
                "media_type": request.media_type,
                "parser_profile_digest": profile.profile_digest,
                "normalization_profile_digest": profile.normalization.profile_digest,
                "chunking_profile_digest": self.chunking_profile.profile_digest,
                "security_ruleset": {
                    "name": SECURITY_RULESET_NAME,
                    "version": SECURITY_RULESET_VERSION,
                },
                "resource_policy_digest": self.limits.policy_digest,
            }
        )
        document_id = f"parsedoc-{document_identity}"
        sections = tuple(
            ParsedSection(
                document_id=document_id,
                section_id="",
                section_ordinal=draft.ordinal,
                parent_section_id=None,
                section_kind=draft.kind,
                structural_locator=draft.structural_locator,
                normalized_start_offset=draft.start,
                normalized_end_offset=draft.end,
                content=draft.content,
                metadata=draft.metadata,
                parser_profile_digest=profile.profile_digest,
            )
            for draft in parsed.sections
        )
        chunks = chunk_sections(
            tenant_id=request.tenant_id,
            source_id=request.source_id,
            hat_scope_id=request.hat_scope_id,
            knowledge_version_id=request.knowledge_version_id,
            document_id=document_id,
            normalized_text=parsed.rendered_text,
            sections=sections,
            profile=self.chunking_profile,
            limits=self.limits,
            language_tag=request.language_tag,
        )
        findings = scan_security_findings(
            document_id,
            parsed.rendered_text,
            sections,
            self.limits,
        )
        blocking = tuple(
            sorted(
                {
                    "BLOCKING_PROMPT_INJECTION_SIGNAL"
                    for finding in findings
                    if finding.severity is FindingSeverity.BLOCKING
                }
            )
        )
        quarantine = QuarantineDecision(bool(blocking), blocking)
        section_manifest = canonical_sha256(tuple(section.section_id for section in sections))
        chunk_manifest = canonical_sha256(tuple(chunk.chunk_id for chunk in chunks))
        finding_manifest = canonical_sha256(tuple(finding.finding_id for finding in findings))
        artifact_digest = canonical_sha256(
            {
                "document_id": document_id,
                "normalized_content_sha256": parsed.normalized.normalized_sha256,
                "section_manifest_digest": section_manifest,
                "chunk_manifest_digest": chunk_manifest,
                "finding_manifest_digest": finding_manifest,
                "quarantine_decision_digest": quarantine.decision_digest,
                "parser_profile": {
                    "name": profile.name,
                    "version": profile.version,
                    "contract": profile.contract_version,
                },
                "normalization_profile": {
                    "name": profile.normalization.name,
                    "version": profile.normalization.version,
                },
            }
        )
        document = ParsedDocument(
            tenant_id=request.tenant_id,
            owner_user_id=request.owner_user_id,
            saga_id=request.saga_id,
            source_id=request.source_id,
            snapshot_id=request.snapshot_id,
            knowledge_version_id=request.knowledge_version_id,
            knowledge_version_ordinal=request.knowledge_version_ordinal,
            hat_scope_id=request.hat_scope_id,
            s3_version_id=request.s3_version_id,
            locked_storage_evidence_digest=request.locked_storage_evidence_digest,
            input_sha256=request.input_sha256,
            input_byte_length=request.input_byte_length,
            media_type=request.media_type,
            parser_name=profile.name,
            parser_version=profile.version,
            parser_contract_version=profile.contract_version,
            decoder_profile=profile.normalization.encoding,
            bom_policy=profile.normalization.bom_policy,
            bom_observed=parsed.normalized.bom_observed,
            normalization_profile=profile.normalization.name,
            normalization_version=profile.normalization.version,
            normalized_content_sha256=parsed.normalized.normalized_sha256,
            normalized_character_length=len(parsed.rendered_text),
            document_id=document_id,
            section_count=len(sections),
            chunk_count=len(chunks),
            security_finding_count=len(findings),
            section_manifest_digest=section_manifest,
            chunk_manifest_digest=chunk_manifest,
            finding_manifest_digest=finding_manifest,
            parse_artifact_digest=artifact_digest,
            completed_at=request.completed_at,
            language_tag=request.language_tag,
        )
        return ParseArtifact(
            document=document,
            normalized_text=parsed.rendered_text,
            sections=sections,
            chunks=chunks,
            findings=findings,
            quarantine=quarantine,
        )
