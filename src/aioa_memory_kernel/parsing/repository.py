"""Atomic CockroachDB persistence for complete Step 11 parse artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .errors import ParsingPersistenceConflictError, ParsingValidationError
from .models import (
    FindingAction,
    FindingCategory,
    FindingSeverity,
    LanguageTag,
    ParseArtifact,
    ParsedChunk,
    ParsedDocument,
    ParsedSection,
    PromptInjectionFinding,
    QuarantineDecision,
    SectionKind,
)


DOCUMENT_COLUMNS = """
tenant_id, document_id, saga_id, source_id, snapshot_id,
knowledge_version_id, knowledge_version_ordinal, hat_scope_id, owner_user_id,
s3_version_id, locked_storage_evidence_digest, input_sha256,
input_byte_length, media_type, parser_name, parser_version,
parser_contract_version, decoder_profile, bom_policy, bom_observed,
normalization_profile, normalization_version, normalized_content_text,
normalized_content_sha256, normalized_character_length, language_tag,
offset_basis, section_count, chunk_count, security_finding_count,
section_manifest_digest, chunk_manifest_digest, finding_manifest_digest,
quarantine_required, quarantine_reason_codes, quarantine_decision_digest,
parse_artifact_digest, completed_at, schema_version
""".replace("\n", " ")

SECTION_COLUMNS = """
tenant_id, document_id, section_id, source_id, hat_scope_id, section_ordinal,
parent_section_id, section_kind, structural_locator, normalized_start_offset,
normalized_end_offset, offset_basis, content_sha256, metadata,
parser_profile_digest, created_at
""".replace("\n", " ")

FINDING_COLUMNS = """
tenant_id, document_id, finding_id, source_id, hat_scope_id, section_id,
ruleset_name, ruleset_version, rule_id, category, severity,
normalized_start_offset, normalized_end_offset, evidence_excerpt_sha256,
action, finding_digest, created_at
""".replace("\n", " ")

CHUNK_COLUMNS = """
tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id,
chunk_ordinal, content_text, content_sha256, start_offset, end_offset,
language_tag, metadata, created_at
""".replace("\n", " ")

VERSION_COLUMNS = """
tenant_id, knowledge_version_id, source_id, snapshot_id, hat_scope_id,
parent_knowledge_version_id, version_ordinal, normalized_content_sha256,
normalization_profile, is_current, created_at, provenance
""".replace("\n", " ")


def _json(value: object) -> str:
    return canonical_json(value)


def _json_value(value: object, expected: type, field_name: str) -> Any:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ParsingPersistenceConflictError(
                f"database returned malformed {field_name}",
                sanitized_code="INVALID_PARSE_DATABASE_ROW",
            ) from exc
    if not isinstance(parsed, expected):
        raise ParsingPersistenceConflictError(
            f"database returned invalid {field_name}",
            sanitized_code="INVALID_PARSE_DATABASE_ROW",
        )
    return parsed


def _timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed
    raise ParsingPersistenceConflictError(
        f"database returned invalid {field_name}",
        sanitized_code="INVALID_PARSE_DATABASE_ROW",
    )


def _boolean(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in {"t", "true", "TRUE", "1", 1}:
        return True
    if value in {"f", "false", "FALSE", "0", 0}:
        return False
    raise ParsingPersistenceConflictError(
        f"database returned invalid {field_name}",
        sanitized_code="INVALID_PARSE_DATABASE_ROW",
    )


def _normalized(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return json.loads(canonical_json(value))
    if isinstance(value, (list, tuple)):
        return json.loads(canonical_json(value))
    if isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _typed_database_value(value: object, expected: object) -> object:
    if expected is None:
        return value
    if isinstance(expected, bool):
        return _boolean(value, "boolean comparison")
    if isinstance(expected, int):
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ParsingPersistenceConflictError(
                "database returned invalid integer comparison",
                sanitized_code="INVALID_PARSE_DATABASE_ROW",
            ) from exc
    if isinstance(expected, datetime):
        return _timestamp(value, "timestamp comparison")
    return _normalized(value)


def _same(row: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    try:
        return all(
            key in row
            and _typed_database_value(row[key], value)
            == _typed_database_value(value, value)
            for key, value in expected.items()
        )
    except ParsingPersistenceConflictError:
        return False


def _put_exact(
    transaction: TransactionProtocol,
    *,
    select_sql: str,
    select_parameters: tuple[object, ...],
    insert_sql: str,
    insert_parameters: tuple[object, ...],
    expected: Mapping[str, object],
) -> None:
    existing = transaction.fetch_one(select_sql, select_parameters)
    if existing is not None:
        if not _same(existing, expected):
            raise ParsingPersistenceConflictError(
                "immutable parsing identity is bound to different facts",
                sanitized_code="PERSISTENCE_CONFLICT",
            )
        return
    inserted = transaction.fetch_one(insert_sql, insert_parameters)
    if inserted is not None and _same(inserted, expected):
        return
    raced = transaction.fetch_one(select_sql, select_parameters)
    if raced is not None and _same(raced, expected):
        return
    raise ParsingPersistenceConflictError(
        "immutable parsing identity collided with different facts",
        sanitized_code="PERSISTENCE_CONFLICT",
    )


def _document_from_row(row: Mapping[str, object]) -> ParsedDocument:
    language = row.get("language_tag")
    return ParsedDocument(
        tenant_id=str(row["tenant_id"]),
        owner_user_id=None if row.get("owner_user_id") is None else str(row["owner_user_id"]),
        saga_id=str(row["saga_id"]),
        source_id=str(row["source_id"]),
        snapshot_id=str(row["snapshot_id"]),
        knowledge_version_id=str(row["knowledge_version_id"]),
        knowledge_version_ordinal=int(row["knowledge_version_ordinal"]),
        hat_scope_id=str(row["hat_scope_id"]),
        s3_version_id=str(row["s3_version_id"]),
        locked_storage_evidence_digest=str(row["locked_storage_evidence_digest"]),
        input_sha256=str(row["input_sha256"]),
        input_byte_length=int(row["input_byte_length"]),
        media_type=str(row["media_type"]),
        parser_name=str(row["parser_name"]),
        parser_version=str(row["parser_version"]),
        parser_contract_version=str(row["parser_contract_version"]),
        decoder_profile=str(row["decoder_profile"]),
        bom_policy=str(row["bom_policy"]),
        bom_observed=_boolean(row["bom_observed"], "bom_observed"),
        normalization_profile=str(row["normalization_profile"]),
        normalization_version=str(row["normalization_version"]),
        normalized_content_sha256=str(row["normalized_content_sha256"]),
        normalized_character_length=int(row["normalized_character_length"]),
        document_id=str(row["document_id"]),
        section_count=int(row["section_count"]),
        chunk_count=int(row["chunk_count"]),
        security_finding_count=int(row["security_finding_count"]),
        section_manifest_digest=str(row["section_manifest_digest"]),
        chunk_manifest_digest=str(row["chunk_manifest_digest"]),
        finding_manifest_digest=str(row["finding_manifest_digest"]),
        parse_artifact_digest=str(row["parse_artifact_digest"]),
        completed_at=_timestamp(row["completed_at"], "completed_at"),
        language_tag=None if language is None else LanguageTag(str(language)),
        schema_version=str(row["schema_version"]),
    )


class CockroachParsingArtifactRepository:
    """Persist or replay one complete immutable parse graph in one transaction."""

    def get_by_saga(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> ParseArtifact | None:
        row = transaction.fetch_one(
            f"SELECT {DOCUMENT_COLUMNS} FROM memory_patch.parsed_documents "
            "WHERE tenant_id = %s AND saga_id = %s",
            (tenant_id, saga_id),
        )
        if row is None:
            return None
        try:
            document = _document_from_row(row)
            normalized_text = str(row["normalized_content_text"])
            section_rows = transaction.fetch_all(
                f"SELECT {SECTION_COLUMNS} FROM memory_patch.parsed_sections "
                "WHERE tenant_id = %s AND document_id = %s "
                "ORDER BY section_ordinal",
                (tenant_id, document.document_id),
            )
            sections = tuple(
                ParsedSection(
                    document_id=document.document_id,
                    section_id=str(item["section_id"]),
                    section_ordinal=int(item["section_ordinal"]),
                    parent_section_id=(
                        None if item.get("parent_section_id") is None else str(item["parent_section_id"])
                    ),
                    section_kind=SectionKind(str(item["section_kind"])),
                    structural_locator=(
                        None if item.get("structural_locator") is None else str(item["structural_locator"])
                    ),
                    normalized_start_offset=int(item["normalized_start_offset"]),
                    normalized_end_offset=int(item["normalized_end_offset"]),
                    content=normalized_text[
                        int(item["normalized_start_offset"]):int(item["normalized_end_offset"])
                    ],
                    content_sha256=str(item["content_sha256"]),
                    metadata=_json_value(item["metadata"], dict, "section metadata"),
                    parser_profile_digest=str(item["parser_profile_digest"]),
                    offset_basis=str(item["offset_basis"]),
                )
                for item in section_rows
            )
            chunk_rows = transaction.fetch_all(
                f"SELECT {CHUNK_COLUMNS} FROM memory_patch.knowledge_chunks "
                "WHERE tenant_id = %s AND knowledge_version_id = %s "
                "ORDER BY chunk_ordinal",
                (tenant_id, document.knowledge_version_id),
            )
            chunks: list[ParsedChunk] = []
            for item in chunk_rows:
                metadata = _json_value(item["metadata"], dict, "chunk metadata")
                language = item.get("language_tag")
                chunks.append(
                    ParsedChunk(
                        tenant_id=str(item["tenant_id"]),
                        source_id=str(item["source_id"]),
                        hat_scope_id=str(item["hat_scope_id"]),
                        knowledge_version_id=str(item["knowledge_version_id"]),
                        document_id=str(metadata["document_id"]),
                        section_id=str(metadata["section_id"]),
                        chunk_id=str(item["chunk_id"]),
                        chunk_ordinal=int(item["chunk_ordinal"]),
                        section_chunk_ordinal=int(metadata["section_chunk_ordinal"]),
                        normalized_start_offset=int(item["start_offset"]),
                        normalized_end_offset=int(item["end_offset"]),
                        content=str(item["content_text"]),
                        content_sha256=str(item["content_sha256"]),
                        chunking_profile_digest=str(metadata["chunking_profile_digest"]),
                        overlap_prefix_characters=int(metadata["overlap_prefix_characters"]),
                        language_tag=None if language is None else LanguageTag(str(language)),
                        offset_basis=str(metadata["offset_basis"]),
                    )
                )
            finding_rows = transaction.fetch_all(
                f"SELECT {FINDING_COLUMNS} FROM memory_patch.parse_security_findings "
                "WHERE tenant_id = %s AND document_id = %s "
                "ORDER BY normalized_start_offset, rule_id, finding_id",
                (tenant_id, document.document_id),
            )
            findings = tuple(
                PromptInjectionFinding(
                    document_id=document.document_id,
                    finding_id=str(item["finding_id"]),
                    rule_id=str(item["rule_id"]),
                    category=FindingCategory(str(item["category"])),
                    severity=FindingSeverity(str(item["severity"])),
                    normalized_start_offset=int(item["normalized_start_offset"]),
                    normalized_end_offset=int(item["normalized_end_offset"]),
                    section_id=None if item.get("section_id") is None else str(item["section_id"]),
                    evidence_excerpt_sha256=str(item["evidence_excerpt_sha256"]),
                    action=FindingAction(str(item["action"])),
                    ruleset_name=str(item["ruleset_name"]),
                    ruleset_version=str(item["ruleset_version"]),
                    finding_digest=str(item["finding_digest"]),
                )
                for item in finding_rows
            )
            quarantine_reasons = tuple(
                str(value)
                for value in _json_value(
                    row["quarantine_reason_codes"], list, "quarantine reasons"
                )
            )
            quarantine = QuarantineDecision(
                required=_boolean(row["quarantine_required"], "quarantine_required"),
                reason_codes=quarantine_reasons,
                decision_digest=str(row["quarantine_decision_digest"]),
            )
            return ParseArtifact(
                document=document,
                normalized_text=normalized_text,
                sections=sections,
                chunks=tuple(chunks),
                findings=findings,
                quarantine=quarantine,
            )
        except (KeyError, TypeError, ValueError, ParsingValidationError) as exc:
            raise ParsingPersistenceConflictError(
                "database returned an inconsistent parse artifact",
                sanitized_code="PERSISTENCE_CONFLICT",
            ) from exc

    def put_artifact(
        self,
        transaction: TransactionProtocol,
        artifact: ParseArtifact,
    ) -> ParseArtifact:
        if not isinstance(artifact, ParseArtifact):
            raise ParsingValidationError(
                "repository requires a typed parse artifact",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )
        existing = self.get_by_saga(
            transaction,
            tenant_id=artifact.document.tenant_id,
            saga_id=artifact.document.saga_id,
        )
        if existing is not None:
            if existing != artifact:
                raise ParsingPersistenceConflictError(
                    "saga is bound to a conflicting parse artifact",
                    sanitized_code="PERSISTENCE_CONFLICT",
                )
            return existing
        self._put_version(transaction, artifact)
        self._put_document(transaction, artifact)
        for section in artifact.sections:
            self._put_section(transaction, artifact, section)
        for finding in artifact.findings:
            self._put_finding(transaction, artifact, finding)
        for chunk in artifact.chunks:
            self._put_chunk(transaction, artifact, chunk)
        persisted = self.get_by_saga(
            transaction,
            tenant_id=artifact.document.tenant_id,
            saga_id=artifact.document.saga_id,
        )
        if persisted != artifact:
            raise ParsingPersistenceConflictError(
                "persisted parse graph differs from the canonical artifact",
                sanitized_code="PERSISTENCE_CONFLICT",
            )
        return persisted

    @staticmethod
    def _put_version(transaction: TransactionProtocol, artifact: ParseArtifact) -> None:
        document = artifact.document
        normalization_profile = (
            f"{document.normalization_profile}:{document.normalization_version}"
        )
        provenance = {
            "normalization_profile": normalization_profile,
            "parser_contract_version": document.parser_contract_version,
            "step": "STEP_11_GENERIC_PARSING_PIPELINE_1A",
        }
        expected = {
            "tenant_id": document.tenant_id,
            "knowledge_version_id": document.knowledge_version_id,
            "source_id": document.source_id,
            "snapshot_id": document.snapshot_id,
            "hat_scope_id": document.hat_scope_id,
            "parent_knowledge_version_id": None,
            "version_ordinal": document.knowledge_version_ordinal,
            "normalized_content_sha256": document.normalized_content_sha256,
            "normalization_profile": normalization_profile,
            "is_current": True,
            "provenance": provenance,
        }
        _put_exact(
            transaction,
            select_sql=f"SELECT {VERSION_COLUMNS} FROM memory_patch.knowledge_versions "
            "WHERE tenant_id = %s AND knowledge_version_id = %s",
            select_parameters=(document.tenant_id, document.knowledge_version_id),
            insert_sql=f"INSERT INTO memory_patch.knowledge_versions ({VERSION_COLUMNS}) "
            "VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, true, %s, %s::JSONB) "
            f"ON CONFLICT DO NOTHING RETURNING {VERSION_COLUMNS}",
            insert_parameters=(
                document.tenant_id,
                document.knowledge_version_id,
                document.source_id,
                document.snapshot_id,
                document.hat_scope_id,
                document.knowledge_version_ordinal,
                document.normalized_content_sha256,
                normalization_profile,
                document.completed_at,
                _json(provenance),
            ),
            expected=expected,
        )

    @staticmethod
    def _put_document(transaction: TransactionProtocol, artifact: ParseArtifact) -> None:
        document = artifact.document
        reasons = list(artifact.quarantine.reason_codes)
        expected = {
            **{
                field: getattr(document, field)
                for field in (
                    "tenant_id", "document_id", "saga_id", "source_id", "snapshot_id",
                    "knowledge_version_id", "knowledge_version_ordinal", "hat_scope_id",
                    "owner_user_id", "s3_version_id", "locked_storage_evidence_digest",
                    "input_sha256", "input_byte_length", "media_type", "parser_name",
                    "parser_version", "parser_contract_version", "decoder_profile",
                    "bom_policy", "bom_observed", "normalization_profile",
                    "normalization_version", "normalized_content_sha256",
                    "normalized_character_length", "section_count", "chunk_count",
                    "security_finding_count", "section_manifest_digest",
                    "chunk_manifest_digest", "finding_manifest_digest",
                    "parse_artifact_digest", "completed_at", "schema_version",
                )
            },
            "normalized_content_text": artifact.normalized_text,
            "language_tag": None if document.language_tag is None else document.language_tag.value,
            "offset_basis": "NORMALIZED_UNICODE_CODE_POINTS_NFC",
            "quarantine_required": artifact.quarantine.required,
            "quarantine_reason_codes": reasons,
            "quarantine_decision_digest": artifact.quarantine.decision_digest,
        }
        columns = [part.strip() for part in DOCUMENT_COLUMNS.split(",")]
        parameters = tuple(
            _json(expected[column])
            if column == "quarantine_reason_codes"
            else expected[column]
            for column in columns
        )
        placeholders = ["%s::JSONB" if column == "quarantine_reason_codes" else "%s" for column in columns]
        _put_exact(
            transaction,
            select_sql=f"SELECT {DOCUMENT_COLUMNS} FROM memory_patch.parsed_documents "
            "WHERE tenant_id = %s AND document_id = %s",
            select_parameters=(document.tenant_id, document.document_id),
            insert_sql=f"INSERT INTO memory_patch.parsed_documents ({DOCUMENT_COLUMNS}) "
            f"VALUES ({', '.join(placeholders)}) ON CONFLICT DO NOTHING "
            f"RETURNING {DOCUMENT_COLUMNS}",
            insert_parameters=parameters,
            expected=expected,
        )

    @staticmethod
    def _put_section(
        transaction: TransactionProtocol,
        artifact: ParseArtifact,
        section: ParsedSection,
    ) -> None:
        document = artifact.document
        expected = {
            "tenant_id": document.tenant_id,
            "document_id": document.document_id,
            "section_id": section.section_id,
            "source_id": document.source_id,
            "hat_scope_id": document.hat_scope_id,
            "section_ordinal": section.section_ordinal,
            "parent_section_id": section.parent_section_id,
            "section_kind": section.section_kind.value,
            "structural_locator": section.structural_locator,
            "normalized_start_offset": section.normalized_start_offset,
            "normalized_end_offset": section.normalized_end_offset,
            "offset_basis": section.offset_basis,
            "content_sha256": section.content_sha256,
            "metadata": section.metadata,
            "parser_profile_digest": section.parser_profile_digest,
            "created_at": document.completed_at,
        }
        _put_exact(
            transaction,
            select_sql=f"SELECT {SECTION_COLUMNS} FROM memory_patch.parsed_sections "
            "WHERE tenant_id = %s AND document_id = %s AND section_id = %s",
            select_parameters=(document.tenant_id, document.document_id, section.section_id),
            insert_sql=f"INSERT INTO memory_patch.parsed_sections ({SECTION_COLUMNS}) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s, %s) "
            f"ON CONFLICT DO NOTHING RETURNING {SECTION_COLUMNS}",
            insert_parameters=(
                document.tenant_id, document.document_id, section.section_id,
                document.source_id, document.hat_scope_id, section.section_ordinal,
                section.parent_section_id, section.section_kind.value,
                section.structural_locator, section.normalized_start_offset,
                section.normalized_end_offset, section.offset_basis,
                section.content_sha256, _json(section.metadata),
                section.parser_profile_digest, document.completed_at,
            ),
            expected=expected,
        )

    @staticmethod
    def _put_finding(
        transaction: TransactionProtocol,
        artifact: ParseArtifact,
        finding: PromptInjectionFinding,
    ) -> None:
        document = artifact.document
        expected = {
            "tenant_id": document.tenant_id,
            "document_id": document.document_id,
            "finding_id": finding.finding_id,
            "source_id": document.source_id,
            "hat_scope_id": document.hat_scope_id,
            "section_id": finding.section_id,
            "ruleset_name": finding.ruleset_name,
            "ruleset_version": finding.ruleset_version,
            "rule_id": finding.rule_id,
            "category": finding.category.value,
            "severity": finding.severity.value,
            "normalized_start_offset": finding.normalized_start_offset,
            "normalized_end_offset": finding.normalized_end_offset,
            "evidence_excerpt_sha256": finding.evidence_excerpt_sha256,
            "action": finding.action.value,
            "finding_digest": finding.finding_digest,
            "created_at": document.completed_at,
        }
        values = tuple(expected[column.strip()] for column in FINDING_COLUMNS.split(","))
        _put_exact(
            transaction,
            select_sql=f"SELECT {FINDING_COLUMNS} FROM memory_patch.parse_security_findings "
            "WHERE tenant_id = %s AND document_id = %s AND finding_id = %s",
            select_parameters=(document.tenant_id, document.document_id, finding.finding_id),
            insert_sql=f"INSERT INTO memory_patch.parse_security_findings ({FINDING_COLUMNS}) "
            f"VALUES ({', '.join(['%s'] * len(values))}) ON CONFLICT DO NOTHING "
            f"RETURNING {FINDING_COLUMNS}",
            insert_parameters=values,
            expected=expected,
        )

    @staticmethod
    def _put_chunk(
        transaction: TransactionProtocol,
        artifact: ParseArtifact,
        chunk: ParsedChunk,
    ) -> None:
        document = artifact.document
        metadata = {
            "chunking_profile_digest": chunk.chunking_profile_digest,
            "document_id": chunk.document_id,
            "offset_basis": chunk.offset_basis,
            "overlap_prefix_characters": chunk.overlap_prefix_characters,
            "section_chunk_ordinal": chunk.section_chunk_ordinal,
            "section_id": chunk.section_id,
        }
        expected = {
            "tenant_id": chunk.tenant_id,
            "chunk_id": chunk.chunk_id,
            "knowledge_version_id": chunk.knowledge_version_id,
            "source_id": chunk.source_id,
            "hat_scope_id": chunk.hat_scope_id,
            "chunk_ordinal": chunk.chunk_ordinal,
            "content_text": chunk.content,
            "content_sha256": chunk.content_sha256,
            "start_offset": chunk.normalized_start_offset,
            "end_offset": chunk.normalized_end_offset,
            "language_tag": None if chunk.language_tag is None else chunk.language_tag.value,
            "metadata": metadata,
            "created_at": document.completed_at,
        }
        _put_exact(
            transaction,
            select_sql=f"SELECT {CHUNK_COLUMNS} FROM memory_patch.knowledge_chunks "
            "WHERE tenant_id = %s AND chunk_id = %s",
            select_parameters=(chunk.tenant_id, chunk.chunk_id),
            insert_sql=f"INSERT INTO memory_patch.knowledge_chunks ({CHUNK_COLUMNS}) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s) "
            f"ON CONFLICT DO NOTHING RETURNING {CHUNK_COLUMNS}",
            insert_parameters=(
                chunk.tenant_id, chunk.chunk_id, chunk.knowledge_version_id,
                chunk.source_id, chunk.hat_scope_id, chunk.chunk_ordinal,
                chunk.content, chunk.content_sha256,
                chunk.normalized_start_offset, chunk.normalized_end_offset,
                None if chunk.language_tag is None else chunk.language_tag.value,
                _json(metadata), document.completed_at,
            ),
            expected=expected,
        )


__all__ = [
    "CockroachParsingArtifactRepository",
    "DOCUMENT_COLUMNS",
]
