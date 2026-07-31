"""Deterministic, authority-neutral generic parsing pipeline 1A.

Imports perform no filesystem, database, AWS, network, subprocess, model, or
HAT action. Concrete external boundaries are injected explicitly.
"""

from .errors import (
    ParsingError,
    ParsingPersistenceConflictError,
    ParsingQuarantineError,
    ParsingResourceLimitError,
    ParsingValidationError,
    UnsupportedMediaTypeError,
)
from .findings import scan_security_findings
from .models import (
    CHUNKING_PROFILE_NAME,
    CHUNKING_PROFILE_VERSION,
    NORMALIZATION_PROFILE_NAME,
    NORMALIZATION_PROFILE_VERSION,
    OFFSET_BASIS,
    PARSER_CONTRACT_VERSION,
    PARSING_SCHEMA_VERSION,
    SECURITY_RULESET_NAME,
    SECURITY_RULESET_VERSION,
    VALIDATOR_CONTRACT_VERSION,
    ChunkingProfile,
    FindingAction,
    FindingCategory,
    FindingSeverity,
    LanguageTag,
    NormalizationProfile,
    ParseArtifact,
    ParsedChunk,
    ParsedDocument,
    ParsedSection,
    ParserProfile,
    ParseValidationResult,
    PromptInjectionFinding,
    QuarantineDecision,
    QuarantineReason,
    ResourceLimits,
    SectionKind,
)
from .pipeline import GenericParsingPipeline, ParsingRequest
from .ports import GenericParseArtifactValidatorPort, GenericParsingPipelinePort
from .registry import ParserRegistry
from .repository import CockroachParsingArtifactRepository
from .service import ParsingPersistenceService
from .validation import ParseArtifactValidator


__all__ = [
    "CHUNKING_PROFILE_NAME",
    "CHUNKING_PROFILE_VERSION",
    "CockroachParsingArtifactRepository",
    "ChunkingProfile",
    "FindingAction",
    "FindingCategory",
    "FindingSeverity",
    "GenericParseArtifactValidatorPort",
    "GenericParsingPipeline",
    "GenericParsingPipelinePort",
    "LanguageTag",
    "NORMALIZATION_PROFILE_NAME",
    "NORMALIZATION_PROFILE_VERSION",
    "NormalizationProfile",
    "OFFSET_BASIS",
    "PARSER_CONTRACT_VERSION",
    "PARSING_SCHEMA_VERSION",
    "ParseArtifact",
    "ParseArtifactValidator",
    "ParseValidationResult",
    "ParsedChunk",
    "ParsedDocument",
    "ParsedSection",
    "ParserProfile",
    "ParserRegistry",
    "ParsingError",
    "ParsingPersistenceConflictError",
    "ParsingPersistenceService",
    "ParsingQuarantineError",
    "ParsingRequest",
    "ParsingResourceLimitError",
    "ParsingValidationError",
    "PromptInjectionFinding",
    "QuarantineDecision",
    "QuarantineReason",
    "ResourceLimits",
    "SECURITY_RULESET_NAME",
    "SECURITY_RULESET_VERSION",
    "SectionKind",
    "UnsupportedMediaTypeError",
    "VALIDATOR_CONTRACT_VERSION",
    "scan_security_findings",
]
