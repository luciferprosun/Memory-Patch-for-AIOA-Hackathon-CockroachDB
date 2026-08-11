"""Step 38 German Law golden-case and hash-only integration utilities.

This module is deliberately a thin proof boundary.  It does not retrieve a
corpus, call a model, decide a claim, approve Personal Memory, or publish a
source.  Those authorities remain with the Step 13-37 services.  The helpers
here load bounded case definitions, verify the evidence-blind Draft V1
projection, bind a before/after trace to existing typed receipts, and expose a
strict hash-bound projection for the already-verified BMJErnAnO fixture.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from aioa_memory_kernel.answers import (
    FinalAnswerOutcome,
    FinalOutputStatus,
    verify_final_answer_outcome_hash,
    verify_verified_answer_hash,
)
from aioa_memory_kernel.claims import (
    ClaimEvidenceRelation,
    ClaimEvidenceLink,
    PacketInputSnapshot,
    exact_text_spans,
    normalize_claim_for_match,
    verify_claim_evidence_link_hash,
    verify_packet_input_snapshot_hash,
)
from aioa_memory_kernel.contracts.enums import (
    EvidenceStatus,
    KnowledgeRoute,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.corrections import (
    CorrectionActionType,
    CorrectionPacketV1A,
    canonical_packet_json,
    verify_citation_hash,
    verify_correction_packet_hash,
    verify_fact_reference_hash,
    verify_packet_against_snapshot,
    verify_prohibited_claim_hash,
    verify_required_correction_hash,
)
from aioa_memory_kernel.evidence import (
    FrozenEvidenceBundle,
    verify_bundle_item_hash,
    verify_evidence_bundle_hash,
)
from aioa_memory_kernel.modeling import (
    DraftV1,
    ModelGenerationRequest,
    ProviderTextRequest,
    TextGenerationProvider,
    TimeoutPolicy,
    build_provider_call_request,
    load_approved_provider_spec,
    verify_draft_v1_hash,
    verify_provider_response_hash,
    verify_provider_text_request_hash,
)
from aioa_memory_kernel.verification import (
    CORRECTED_EVIDENCE_PROOF_VERSION,
    CorrectedEvidenceProof,
    CorrectedEvidenceVerdict,
    CorrectedEvidenceVerifierRequest,
    CorrectedEvidenceVerifierSignal,
    DraftV2PipelineResult,
    VerificationSummaryStatus,
    verify_corrected_evidence_proofs_against_packet,
    verify_corrected_evidence_request_hash,
    verify_draft_v2_pipeline_result_hash,
)
from aioa_memory_kernel.verification.prompt import (
    DRAFT_V2_PROVIDER_PURPOSE,
    load_draft_v2_generation_parameters,
    load_draft_v2_prompt_template,
)


STEP38_SCHEMA_VERSION = "1.0.0"
STEP38_SUITE_ID = "step38-german-law-golden-cases-1a"
STEP38_TRACE_VERSION = "step38-before-after-trace-1a"
STEP38_PROJECTION_VERSION = "step38-bmjernano-exact-evidence-projection-1a"
STEP38_TEMPORAL_PROJECTION_VERSION = (
    "step38-bmjernano-fixture-temporal-projection-1a"
)
STEP38_EVIDENCE_CONTEXT_VERSION = "step38-evidence-bound-correction-context-1a"
STEP38_DRAFT_V2_TARGET_VERSION = "step38-draft-v2-target-projection-1a"

REAL_SOURCE_ID = "de-federal-gii-bjnr1330a0023"
REAL_OFFICIAL_IDENTIFIER = "BJNR1330A0023"
REAL_HAT_ID = "german-law"
REAL_HAT_VERSION = "1.0.0"
REAL_HAT_SCOPE_ID = "german-law-global-1a"
REAL_VERSION_IDENTITY = (
    "legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95"
)
REAL_PROVISION_HASHES = {
    "I.": "323c88960cc5eeca3e2d4b6c3c34630947f85ec82c75e1e398492a319bd13147",
    "II.": "6a12a5f19d7a4b61d71be5c5583d0a3a41b3111fcf00803892200fc42260d99e",
    "III.": "fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1",
}

_LOGICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_MAX_QUESTION_BYTES = 4096
_MAX_CASES = 32
_MAX_COMPONENTS = 64
_MAX_EVIDENCE_CONTEXT_ITEMS = 16
_MAX_EVIDENCE_CONTEXT_BYTES = 16 * 1024
_MAX_DRAFT_V2_TARGETS = 32
_CITATION_MARKER = re.compile(r"\s*\[citation:([^\]]+)\]")
_TERMINAL_PUNCTUATION = frozenset(".!?;")
_BMJERNANO_ENTRY_SENTENCE = "Diese Anordnung tritt am 1. Januar 2024 in Kraft."
_BMJERNANO_ENTRY_DATE_TEXT = "1. Januar 2024"
_EnumT = TypeVar("_EnumT", bound=StableStringEnum)


class Step38FixtureClass(StableStringEnum):
    REAL_GERMAN_LAW_CORPUS_FIXTURE = "REAL_GERMAN_LAW_CORPUS_FIXTURE"
    SYNTHETIC_EDGE_CASE = "SYNTHETIC_EDGE_CASE"


class GoldenCaseKind(StableStringEnum):
    PRIMARY = "PRIMARY"
    BACKUP = "BACKUP"
    SUPPORTED = "SUPPORTED"
    TEMPORAL = "TEMPORAL"
    FAIL_CLOSED = "FAIL_CLOSED"
    ROUTE_NEGATIVE = "ROUTE_NEGATIVE"


class GoldenCorrectionCondition(StableStringEnum):
    WRONG_EFFECTIVE_DATE_MUST_BE_REPLACED_WITH_EXACT_SOURCE_DATE = (
        "WRONG_EFFECTIVE_DATE_MUST_BE_REPLACED_WITH_EXACT_SOURCE_DATE"
    )
    SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED = (
        "SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED"
    )
    NO_MATERIAL_CORRECTION_REQUIRED = "NO_MATERIAL_CORRECTION_REQUIRED"
    CURRENT_VERSION_MUST_NOT_LEAK_INTO_HISTORICAL_ANSWER = (
        "CURRENT_VERSION_MUST_NOT_LEAK_INTO_HISTORICAL_ANSWER"
    )
    CONFLICT_MUST_NOT_BE_RESOLVED_AS_CERTAIN = (
        "CONFLICT_MUST_NOT_BE_RESOLVED_AS_CERTAIN"
    )
    GERMAN_LAW_HAT_MUST_NOT_BE_FORCED = "GERMAN_LAW_HAT_MUST_NOT_BE_FORCED"


class GoldenExpectedFinalOutput(StableStringEnum):
    VERIFIED_ANSWER = "VERIFIED_ANSWER"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    PASS_THROUGH = "PASS_THROUGH"


_REAL_CASE_KINDS = {
    "primary-entry-into-force": GoldenCaseKind.PRIMARY,
    "backup-special-case-reservation": GoldenCaseKind.BACKUP,
    "supported-entry-into-force-clean": GoldenCaseKind.SUPPORTED,
}


@dataclass(frozen=True, slots=True)
class RealCorpusProvision:
    provision_identifier: str
    content_sha256: str

    def __post_init__(self) -> None:
        identifier = _text(
            self.provision_identifier,
            "provision_identifier",
            32,
        )
        require_sha256_hex(self.content_sha256, "content_sha256")
        if REAL_PROVISION_HASHES.get(identifier) != self.content_sha256:
            raise ContractValidationError("real corpus provision identity changed")


@dataclass(frozen=True, slots=True)
class RealCorpusFixture:
    fixture_class: Step38FixtureClass
    hat_id: str
    hat_version: str
    hat_scope_id: str
    source_id: str
    official_identifier: str
    version_identity: str
    provisions: tuple[RealCorpusProvision, ...]

    def __post_init__(self) -> None:
        if self.fixture_class is not Step38FixtureClass.REAL_GERMAN_LAW_CORPUS_FIXTURE:
            raise ContractValidationError("real corpus fixture class changed")
        if (
            self.hat_id != REAL_HAT_ID
            or self.hat_version != REAL_HAT_VERSION
            or self.hat_scope_id != REAL_HAT_SCOPE_ID
            or self.source_id != REAL_SOURCE_ID
            or self.official_identifier != REAL_OFFICIAL_IDENTIFIER
            or self.version_identity != REAL_VERSION_IDENTITY
        ):
            raise ContractValidationError("real corpus fixture identity changed")
        provisions = tuple(self.provisions)
        if (
            len(provisions) != len(REAL_PROVISION_HASHES)
            or any(not isinstance(item, RealCorpusProvision) for item in provisions)
            or tuple(item.provision_identifier for item in provisions)
            != tuple(REAL_PROVISION_HASHES)
        ):
            raise ContractValidationError("real corpus provisions are incomplete")
        object.__setattr__(self, "provisions", provisions)


def _text(value: object, field_name: str, maximum_bytes: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 for character in value)
    ):
        raise ContractValidationError(
            f"{field_name} must be bounded canonical NFC text"
        )
    return value


def _logical_id(value: object, field_name: str) -> str:
    result = _text(value, field_name, 128)
    if _LOGICAL_ID.fullmatch(result) is None:
        raise ContractValidationError(f"{field_name} must be a logical identifier")
    return result


def _timestamp(value: object, field_name: str) -> datetime:
    text = _text(value, field_name, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _string_tuple(
    value: object,
    field_name: str,
    *,
    maximum: int = 32,
    maximum_item_bytes: int = 512,
    ordered: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} must be bounded and ordered")
    result = tuple(_text(item, field_name, maximum_item_bytes) for item in value)
    if len(result) != len(set(result)):
        raise ContractValidationError(f"{field_name} must be unique")
    if ordered and result != tuple(sorted(result)):
        raise ContractValidationError(f"{field_name} must be canonically ordered")
    return result


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _exact_mapping(
    value: object,
    expected_fields: frozenset[str],
    context: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != expected_fields:
        raise ContractValidationError(f"{context} has missing or unknown fields")
    return value


def _closed_enum(
    enum_type: type[_EnumT],
    value: object,
    field_name: str,
) -> _EnumT:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContractValidationError(f"{field_name} is outside the closed enum") from exc


def _json_string_array(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{field_name} must be an array")
    return tuple(value)


_SUITE_FIELDS = frozenset(
    {"schema_version", "suite_id", "real_corpus_fixture", "cases"}
)
_REAL_FIXTURE_FIELDS = frozenset(
    {
        "fixture_class",
        "hat_id",
        "hat_version",
        "hat_scope_id",
        "source_id",
        "official_identifier",
        "version_identity",
        "provisions",
    }
)
_REAL_PROVISION_FIELDS = frozenset(
    {"provision_identifier", "content_sha256"}
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "case_kind",
        "fixture_class",
        "question",
        "knowledge_as_of",
        "expected_route",
        "expected_source_id",
        "expected_provision_ids",
        "expected_evidence_status",
        "expected_correction_condition",
        "expected_final_output",
        "personal_memory_branch",
    }
)


def _real_fixture_from_json(value: object) -> RealCorpusFixture:
    data = _exact_mapping(value, _REAL_FIXTURE_FIELDS, "real corpus fixture")
    provisions_value = data["provisions"]
    if not isinstance(provisions_value, list):
        raise ContractValidationError("real corpus provisions must be an array")
    provisions = []
    for value in provisions_value:
        provision = _exact_mapping(
            value,
            _REAL_PROVISION_FIELDS,
            "real corpus provision",
        )
        provisions.append(
            RealCorpusProvision(
                provision_identifier=provision["provision_identifier"],
                content_sha256=provision["content_sha256"],
            )
        )
    return RealCorpusFixture(
        fixture_class=_closed_enum(
            Step38FixtureClass,
            data["fixture_class"],
            "real_corpus_fixture.fixture_class",
        ),
        hat_id=data["hat_id"],
        hat_version=data["hat_version"],
        hat_scope_id=data["hat_scope_id"],
        source_id=data["source_id"],
        official_identifier=data["official_identifier"],
        version_identity=data["version_identity"],
        provisions=tuple(provisions),
    )


@dataclass(frozen=True, slots=True)
class GermanLawGoldenCase:
    case_id: str
    case_kind: GoldenCaseKind
    fixture_class: Step38FixtureClass
    question: str
    knowledge_as_of: datetime
    expected_route: KnowledgeRoute
    expected_source_id: str | None
    expected_provision_ids: tuple[str, ...]
    expected_evidence_status: EvidenceStatus
    expected_correction_condition: GoldenCorrectionCondition
    expected_final_output: GoldenExpectedFinalOutput
    personal_memory_branch: bool
    question_digest: str = field(init=False)
    case_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _logical_id(self.case_id, "case_id")
        if not isinstance(self.case_kind, GoldenCaseKind):
            raise ContractValidationError("case_kind must be typed")
        if not isinstance(self.fixture_class, Step38FixtureClass):
            raise ContractValidationError("fixture_class must be typed")
        question = _text(self.question, "question", _MAX_QUESTION_BYTES)
        if not isinstance(self.knowledge_as_of, datetime):
            raise ContractValidationError("knowledge_as_of must be a datetime")
        if self.knowledge_as_of.tzinfo is None:
            raise ContractValidationError("knowledge_as_of must be timezone-aware")
        object.__setattr__(self, "knowledge_as_of", self.knowledge_as_of.astimezone(UTC))
        for value, enum_type, name in (
            (self.expected_route, KnowledgeRoute, "expected_route"),
            (self.expected_evidence_status, EvidenceStatus, "expected_evidence_status"),
            (
                self.expected_correction_condition,
                GoldenCorrectionCondition,
                "expected_correction_condition",
            ),
            (
                self.expected_final_output,
                GoldenExpectedFinalOutput,
                "expected_final_output",
            ),
        ):
            if not isinstance(value, enum_type):
                raise ContractValidationError(f"{name} must be typed")
        if self.expected_source_id is not None:
            _text(self.expected_source_id, "expected_source_id", 255)
        provisions = _string_tuple(
            self.expected_provision_ids,
            "expected_provision_ids",
            maximum=16,
        )
        object.__setattr__(self, "expected_provision_ids", provisions)
        if not isinstance(self.personal_memory_branch, bool):
            raise ContractValidationError("personal_memory_branch must be boolean")
        if (
            self.fixture_class is Step38FixtureClass.REAL_GERMAN_LAW_CORPUS_FIXTURE
            and (
                self.expected_source_id != REAL_SOURCE_ID
                or _REAL_CASE_KINDS.get(self.case_id) is not self.case_kind
                or not provisions
                or not set(provisions).issubset(REAL_PROVISION_HASHES)
                or self.expected_route is not KnowledgeRoute.HAT_ASSIST
                or self.expected_evidence_status is not EvidenceStatus.SUFFICIENT
                or self.expected_final_output
                is not GoldenExpectedFinalOutput.VERIFIED_ANSWER
            )
        ):
            raise ContractValidationError("real case identity cannot be relabeled")
        if (
            self.fixture_class is Step38FixtureClass.SYNTHETIC_EDGE_CASE
            and self.case_id in _REAL_CASE_KINDS
        ):
            raise ContractValidationError("real case identity cannot be synthetic")
        digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
        object.__setattr__(self, "question_digest", digest)
        object.__setattr__(
            self,
            "case_hash",
            canonical_sha256(self, exclude_fields=("case_hash", "question")),
        )


@dataclass(frozen=True, slots=True)
class GermanLawGoldenCaseSuite:
    schema_version: str
    suite_id: str
    real_corpus_fixture: RealCorpusFixture
    cases: tuple[GermanLawGoldenCase, ...]
    suite_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP38_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 38 suite schema")
        if self.suite_id != STEP38_SUITE_ID:
            raise ContractValidationError("unexpected Step 38 suite identity")
        if not isinstance(self.real_corpus_fixture, RealCorpusFixture):
            raise ContractValidationError("real corpus fixture must be typed")
        if not isinstance(self.cases, tuple) or not 1 <= len(self.cases) <= _MAX_CASES:
            raise ContractValidationError("golden cases must be a bounded tuple")
        if any(not isinstance(item, GermanLawGoldenCase) for item in self.cases):
            raise ContractValidationError("golden cases must be typed")
        if len({item.case_id for item in self.cases}) != len(self.cases):
            raise ContractValidationError("golden case identities must be unique")
        kinds = {item.case_kind for item in self.cases}
        required = {
            GoldenCaseKind.PRIMARY,
            GoldenCaseKind.BACKUP,
            GoldenCaseKind.SUPPORTED,
            GoldenCaseKind.TEMPORAL,
            GoldenCaseKind.FAIL_CLOSED,
            GoldenCaseKind.ROUTE_NEGATIVE,
        }
        if not required.issubset(kinds):
            raise ContractValidationError("golden case matrix is incomplete")
        object.__setattr__(
            self,
            "suite_hash",
            canonical_sha256(
                {
                    "schema_version": self.schema_version,
                    "suite_id": self.suite_id,
                    "real_corpus_fixture": self.real_corpus_fixture,
                    "case_hashes": tuple(item.case_hash for item in self.cases),
                }
            ),
        )

    def case(self, case_id: str) -> GermanLawGoldenCase:
        _logical_id(case_id, "case_id")
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise KeyError(case_id)


def load_german_law_golden_cases(path: Path) -> GermanLawGoldenCaseSuite:
    """Load the bounded machine-readable suite without touching external data."""

    if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
        raise ContractValidationError("golden-case fixture must be a regular file")
    if path.stat().st_size > 256 * 1024:
        raise ContractValidationError("golden-case fixture exceeds its bound")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractValidationError("golden-case fixture is invalid") from exc
    payload = _exact_mapping(payload, _SUITE_FIELDS, "golden-case fixture")
    if not isinstance(payload["cases"], list):
        raise ContractValidationError("golden-case cases must be an array")
    values: list[GermanLawGoldenCase] = []
    for item in payload["cases"]:
        item = _exact_mapping(item, _CASE_FIELDS, "golden case")
        values.append(
            GermanLawGoldenCase(
                case_id=item["case_id"],
                case_kind=_closed_enum(
                    GoldenCaseKind,
                    item["case_kind"],
                    "case_kind",
                ),
                fixture_class=_closed_enum(
                    Step38FixtureClass,
                    item["fixture_class"],
                    "fixture_class",
                ),
                question=item["question"],
                knowledge_as_of=_timestamp(item["knowledge_as_of"], "knowledge_as_of"),
                expected_route=_closed_enum(
                    KnowledgeRoute,
                    item["expected_route"],
                    "expected_route",
                ),
                expected_source_id=item["expected_source_id"],
                expected_provision_ids=_json_string_array(
                    item["expected_provision_ids"],
                    "expected_provision_ids",
                ),
                expected_evidence_status=_closed_enum(
                    EvidenceStatus,
                    item["expected_evidence_status"],
                    "expected_evidence_status",
                ),
                expected_correction_condition=_closed_enum(
                    GoldenCorrectionCondition,
                    item["expected_correction_condition"],
                    "expected_correction_condition",
                ),
                expected_final_output=_closed_enum(
                    GoldenExpectedFinalOutput,
                    item["expected_final_output"],
                    "expected_final_output",
                ),
                personal_memory_branch=item["personal_memory_branch"],
            )
        )
    return GermanLawGoldenCaseSuite(
        schema_version=payload["schema_version"],
        suite_id=payload["suite_id"],
        real_corpus_fixture=_real_fixture_from_json(payload["real_corpus_fixture"]),
        cases=tuple(values),
    )


@dataclass(frozen=True, slots=True)
class Step38Component:
    step: int
    component_id: str
    module_name: str
    responsibility: str
    grants_new_authority: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.step, int)
            or isinstance(self.step, bool)
            or not 13 <= self.step <= 37
        ):
            raise ContractValidationError("component step is outside Step 13-37")
        _logical_id(self.component_id, "component_id")
        _text(self.module_name, "module_name", 255)
        _text(self.responsibility, "responsibility", 255)
        if self.grants_new_authority is not False:
            raise ContractValidationError("Step 38 components cannot grant authority")


_COMPONENTS = (
    Step38Component(
        13,
        "german-law-hat",
        "aioa_memory_kernel.german_law",
        "German Law HAT and source policy",
    ),
    Step38Component(
        14,
        "corpus-inventory",
        "aioa_memory_kernel.corpus",
        "canonical corpus inventory",
    ),
    Step38Component(
        15,
        "temporal-normalization",
        "aioa_memory_kernel.german_law.normalization",
        "temporal and jurisdiction normalization",
    ),
    Step38Component(
        16,
        "publication-verification",
        "aioa_memory_kernel.german_law.publication",
        "publication bundle verification",
    ),
    Step38Component(
        17,
        "routing-policy",
        "aioa_memory_kernel.routing",
        "route and knowledge policy",
    ),
    Step38Component(
        18,
        "exact-retrieval",
        "aioa_memory_kernel.retrieval",
        "exact and full-text retrieval",
    ),
    Step38Component(
        19,
        "vector-retrieval",
        "aioa_memory_kernel.embeddings",
        "embedding and vector retrieval",
    ),
    Step38Component(
        20,
        "evidence-bundle",
        "aioa_memory_kernel.evidence",
        "hybrid evidence assembly",
    ),
    Step38Component(
        21,
        "temporal-resolution",
        "aioa_memory_kernel.temporal",
        "conflict, freshness and applicability",
    ),
    Step38Component(
        22,
        "draft-v1",
        "aioa_memory_kernel.modeling",
        "evidence-blind provider draft",
    ),
    Step38Component(
        23,
        "claim-binding",
        "aioa_memory_kernel.claims",
        "claim extraction and evidence binding",
    ),
    Step38Component(
        24,
        "correction-packet",
        "aioa_memory_kernel.corrections",
        "hash-bound correction packet",
    ),
    Step38Component(
        25,
        "draft-v2-verification",
        "aioa_memory_kernel.verification",
        "corrected draft and layered verification",
    ),
    Step38Component(
        26,
        "verified-answer",
        "aioa_memory_kernel.answers",
        "fail-closed final output",
    ),
    Step38Component(
        27,
        "personal-memory",
        "aioa_memory_kernel.personal_memory",
        "owner-private persistence",
    ),
    Step38Component(
        28,
        "correction-candidate",
        "aioa_memory_kernel.personal_memory.candidates",
        "candidate-only bridge",
    ),
    Step38Component(
        29,
        "patch-proposal",
        "aioa_memory_kernel.personal_memory.proposals",
        "evidence-bound proposal",
    ),
    Step38Component(
        30,
        "approval-commit-activation",
        "aioa_memory_kernel.personal_memory.lifecycle",
        "owner approval and technical lifecycle",
    ),
    Step38Component(
        31,
        "active-patch-retrieval",
        "aioa_memory_kernel.personal_memory.retrieval",
        "private cross-model applicability",
    ),
    Step38Component(
        32,
        "personal-memory-lifecycle",
        "aioa_memory_kernel.personal_memory.lifecycle32",
        "supersession, revocation and deletion",
    ),
    Step38Component(
        33,
        "audit-ledger",
        "aioa_memory_kernel.audit_ledger",
        "append-only audit chain",
    ),
    Step38Component(
        34,
        "review-workspace",
        "aioa_memory_kernel.review_workspace",
        "human review fallback",
    ),
    Step38Component(
        35,
        "owner-ui",
        "aioa_memory_kernel.personal_memory_ui",
        "owner-facing read and action boundary",
    ),
    Step38Component(
        36,
        "credential-separation",
        "aioa_memory_kernel.security",
        "least-privilege credential boundary",
    ),
    Step38Component(
        37,
        "failure-recovery",
        "aioa_memory_kernel.reliability",
        "test-only deterministic recovery proof",
    ),
)


def step38_component_inventory(*, require_importable: bool = True) -> tuple[Step38Component, ...]:
    """Return the closed integration inventory and optionally prove modules exist."""

    if len(_COMPONENTS) > _MAX_COMPONENTS:
        raise RuntimeError("Step 38 component inventory exceeds its bound")
    if require_importable:
        missing = tuple(
            item.module_name
            for item in _COMPONENTS
            if importlib.util.find_spec(item.module_name) is None
        )
        if missing:
            raise ContractValidationError(
                f"required Step 38 components are unavailable: {missing!r}"
            )
    return _COMPONENTS


@dataclass(frozen=True, slots=True)
class EvidenceBlindnessProof:
    generation_request_hash: str
    provider_call_request_hash: str
    original_query_digest: str
    expected_query_matched: bool
    projected_field_names: tuple[str, ...]
    evidence_fields_projected: bool
    tools_enabled: bool
    proof_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.generation_request_hash, "generation_request_hash"),
            (self.provider_call_request_hash, "provider_call_request_hash"),
            (self.original_query_digest, "original_query_digest"),
        ):
            require_sha256_hex(value, name)
        object.__setattr__(
            self,
            "projected_field_names",
            _string_tuple(
                self.projected_field_names,
                "projected_field_names",
                ordered=True,
            ),
        )
        if (
            self.expected_query_matched is not True
            or self.evidence_fields_projected is not False
            or self.tools_enabled is not False
        ):
            raise ContractValidationError("Draft V1 proof must remain evidence-blind")
        object.__setattr__(
            self,
            "proof_hash",
            canonical_sha256(self, exclude_fields=("proof_hash",)),
        )


def prove_draft_v1_evidence_blind(
    request: ModelGenerationRequest,
    expected_original_query: str,
) -> EvidenceBlindnessProof:
    """Prove that Step 22 projects only its pinned prompt and original query."""

    if not isinstance(request, ModelGenerationRequest):
        raise ContractValidationError("request must be a ModelGenerationRequest")
    expected = _text(
        expected_original_query,
        "expected_original_query",
        _MAX_QUESTION_BYTES,
    )
    call = build_provider_call_request(request)
    names = tuple(sorted(item.name for item in fields(call)))
    forbidden = {
        "evidence",
        "evidence_bundle",
        "temporal_result",
        "correction_packet",
        "draft_v2",
        "verified_answer",
    }
    projected = any(name in forbidden or "evidence" in name for name in names)
    identity = call.provider_identity
    tools_enabled = not all(
        (
            identity.tooling_disabled,
            identity.function_calling_disabled,
            identity.web_browsing_disabled,
            identity.code_execution_disabled,
        )
    )
    return EvidenceBlindnessProof(
        generation_request_hash=request.request_hash,
        provider_call_request_hash=call.request_hash,
        original_query_digest=call.original_query_digest,
        expected_query_matched=(
            request.original_query == expected
            and call.original_query == expected
            and hashlib.sha256(expected.encode("utf-8")).hexdigest()
            == call.original_query_digest
        ),
        projected_field_names=names,
        evidence_fields_projected=projected,
        tools_enabled=tools_enabled,
    )


@dataclass(frozen=True, slots=True)
class HashBoundOfficialEvidenceProjection:
    projection_version: str
    source_id: str
    official_identifier: str
    version_identity: str
    provision_hashes: tuple[str, ...]
    exact_official_evidence: tuple[str, ...]
    canonical_evidence_authority: bool
    source_publication_authority: bool
    projection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.projection_version != STEP38_PROJECTION_VERSION:
            raise ContractValidationError("unsupported semantic projection version")
        if (
            self.source_id != REAL_SOURCE_ID
            or self.official_identifier != REAL_OFFICIAL_IDENTIFIER
            or self.version_identity != REAL_VERSION_IDENTITY
        ):
            raise ContractValidationError("semantic projection source identity changed")
        hashes = tuple(self.provision_hashes)
        if hashes != tuple(
            REAL_PROVISION_HASHES[key] for key in ("I.", "II.", "III.")
        ):
            raise ContractValidationError("semantic projection provision hashes changed")
        for value in hashes:
            require_sha256_hex(value, "provision_hash")
        evidence = _string_tuple(
            self.exact_official_evidence,
            "exact_official_evidence",
            maximum=8,
            maximum_item_bytes=_MAX_EVIDENCE_CONTEXT_BYTES,
        )
        if len(evidence) != 3:
            raise ContractValidationError("official evidence projection is incomplete")
        for text, expected_hash in zip(evidence, hashes, strict=True):
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash:
                raise IntegrityError(
                    "official evidence text does not match its provision hash"
                )
        object.__setattr__(self, "exact_official_evidence", evidence)
        if self.canonical_evidence_authority is not False:
            raise ContractValidationError("a projection is not canonical evidence")
        if self.source_publication_authority is not False:
            raise ContractValidationError("a projection cannot publish a source")
        object.__setattr__(
            self,
            "projection_hash",
            canonical_sha256(self, exclude_fields=("projection_hash",)),
        )


def project_verified_bmjernano_evidence(
    official_text_by_provision: Mapping[str, str],
) -> HashBoundOfficialEvidenceProjection:
    """Project exact official provision bytes after their hashes verify.

    No paraphrase or derived assertion is attached to the real source identity.
    The projection itself has no publication or decision authority.
    """

    if set(official_text_by_provision) != set(REAL_PROVISION_HASHES):
        raise ContractValidationError("all exact BMJErnAnO provisions are required")
    for provision, expected in REAL_PROVISION_HASHES.items():
        text = official_text_by_provision[provision]
        if (
            not isinstance(text, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != expected
        ):
            raise IntegrityError("BMJErnAnO provision hash mismatch")
    first = official_text_by_provision["I."]
    second = official_text_by_provision["II."]
    third = official_text_by_provision["III."]
    if (
        "bis einschließlich A 15" not in first
        or "Für besondere Fälle" not in second
        or "1. Januar 2024" not in third
        or "nicht mehr anzuwenden" not in third
    ):
        raise IntegrityError("BMJErnAnO bounded fact marker missing")
    return HashBoundOfficialEvidenceProjection(
        projection_version=STEP38_PROJECTION_VERSION,
        source_id=REAL_SOURCE_ID,
        official_identifier=REAL_OFFICIAL_IDENTIFIER,
        version_identity=REAL_VERSION_IDENTITY,
        provision_hashes=tuple(
            REAL_PROVISION_HASHES[key] for key in ("I.", "II.", "III.")
        ),
        exact_official_evidence=(first, second, third),
        canonical_evidence_authority=False,
        source_publication_authority=False,
    )


@dataclass(frozen=True, slots=True)
class GermanLawTemporalProjectionReceipt:
    """Fixture-bound structured date parsed from exact provision III bytes.

    This receipt is neither pre-existing Step 18 temporal metadata nor model
    inference.  It documents the deterministic Step 38 adapter that supplies
    Step 21 with a structured ``effective_from`` value for the real fixture.
    """

    projection_version: str
    source_id: str
    version_identity: str
    provision_identifier: str
    provision_content_sha256: str
    exact_source_span: str
    exact_source_span_sha256: str
    parsed_effective_date_text: str
    effective_from: datetime
    projection_method: str
    fixture_bound: bool
    preexisting_temporal_metadata_used: bool
    model_inference_used: bool
    canonical_evidence_authority: bool
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.projection_version != STEP38_TEMPORAL_PROJECTION_VERSION:
            raise ContractValidationError("unsupported temporal projection version")
        if (
            self.source_id != REAL_SOURCE_ID
            or self.version_identity != REAL_VERSION_IDENTITY
            or self.provision_identifier != "III."
            or self.provision_content_sha256 != REAL_PROVISION_HASHES["III."]
        ):
            raise ContractValidationError("temporal projection source binding changed")
        require_sha256_hex(
            self.provision_content_sha256,
            "provision_content_sha256",
        )
        span = _text(self.exact_source_span, "exact_source_span", 512)
        if span != _BMJERNANO_ENTRY_SENTENCE:
            raise ContractValidationError("temporal projection source span changed")
        require_sha256_hex(
            self.exact_source_span_sha256,
            "exact_source_span_sha256",
        )
        if (
            hashlib.sha256(span.encode("utf-8")).hexdigest()
            != self.exact_source_span_sha256
        ):
            raise IntegrityError("temporal projection span hash mismatch")
        if self.parsed_effective_date_text != _BMJERNANO_ENTRY_DATE_TEXT:
            raise ContractValidationError("temporal projection date text changed")
        if not isinstance(self.effective_from, datetime):
            raise ContractValidationError("effective_from must be a datetime")
        if self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None:
            raise ContractValidationError("effective_from must be timezone-aware")
        normalized = self.effective_from.astimezone(UTC)
        if normalized != datetime(2024, 1, 1, tzinfo=UTC):
            raise ContractValidationError("temporal projection parsed date changed")
        object.__setattr__(self, "effective_from", normalized)
        if self.projection_method != "FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE":
            raise ContractValidationError("temporal projection method changed")
        if (
            self.fixture_bound is not True
            or self.preexisting_temporal_metadata_used is not False
            or self.model_inference_used is not False
            or self.canonical_evidence_authority is not False
        ):
            raise ContractValidationError("temporal projection authority changed")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )

    @property
    def effective_from_date(self) -> str:
        return self.effective_from.date().isoformat()


def project_bmjernano_temporal_facts(
    provision_iii_text: str,
) -> GermanLawTemporalProjectionReceipt:
    """Parse the exact German entry date after the real fixture hash verifies."""

    if (
        not isinstance(provision_iii_text, str)
        or hashlib.sha256(provision_iii_text.encode("utf-8")).hexdigest()
        != REAL_PROVISION_HASHES["III."]
    ):
        raise IntegrityError("BMJErnAnO provision III hash mismatch")
    if not provision_iii_text.startswith(_BMJERNANO_ENTRY_SENTENCE):
        raise IntegrityError("BMJErnAnO entry-into-force sentence missing")
    matched = re.fullmatch(
        r"(?P<day>[0-9]{1,2})\. (?P<month>Januar) (?P<year>[0-9]{4})",
        _BMJERNANO_ENTRY_DATE_TEXT,
    )
    if matched is None:
        raise IntegrityError("BMJErnAnO German date marker is invalid")
    parsed = datetime(
        int(matched.group("year")),
        1,
        int(matched.group("day")),
        tzinfo=UTC,
    )
    return GermanLawTemporalProjectionReceipt(
        projection_version=STEP38_TEMPORAL_PROJECTION_VERSION,
        source_id=REAL_SOURCE_ID,
        version_identity=REAL_VERSION_IDENTITY,
        provision_identifier="III.",
        provision_content_sha256=REAL_PROVISION_HASHES["III."],
        exact_source_span=_BMJERNANO_ENTRY_SENTENCE,
        exact_source_span_sha256=hashlib.sha256(
            _BMJERNANO_ENTRY_SENTENCE.encode("utf-8")
        ).hexdigest(),
        parsed_effective_date_text=_BMJERNANO_ENTRY_DATE_TEXT,
        effective_from=parsed,
        projection_method="FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE",
        fixture_bound=True,
        preexisting_temporal_metadata_used=False,
        model_inference_used=False,
        canonical_evidence_authority=False,
    )


@dataclass(frozen=True, slots=True)
class EvidenceBoundCorrectionItem:
    """One exact Step 20 excerpt span reachable from a packet citation."""

    citation_id: str
    citation_hash: str
    original_evidence_link: ClaimEvidenceLink
    evidence_link_hash: str
    step20_bundle_hash: str
    step20_item_hash: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    relation: ClaimEvidenceRelation
    exact_excerpt: str
    exact_excerpt_sha256: str
    item_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.citation_id, "citation_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.citation_hash, "citation_hash"),
            (self.evidence_link_hash, "evidence_link_hash"),
            (self.step20_bundle_hash, "step20_bundle_hash"),
            (self.step20_item_hash, "step20_item_hash"),
            (self.exact_excerpt_sha256, "exact_excerpt_sha256"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.relation, ClaimEvidenceRelation):
            raise ContractValidationError("relation must be ClaimEvidenceRelation")
        if not isinstance(self.original_evidence_link, ClaimEvidenceLink):
            raise ContractValidationError(
                "original_evidence_link must be ClaimEvidenceLink"
            )
        try:
            verify_claim_evidence_link_hash(self.original_evidence_link)
        except (ContractValidationError, IntegrityError) as exc:
            raise IntegrityError("evidence context link integrity failed") from exc
        link = self.original_evidence_link
        if (
            link.link_hash != self.evidence_link_hash
            or link.step20_bundle_hash != self.step20_bundle_hash
            or link.step20_item_hash != self.step20_item_hash
            or link.source_id != self.source_id
            or link.knowledge_version_id != self.knowledge_version_id
            or link.chunk_id != self.chunk_id
            or link.relation is not self.relation
        ):
            raise IntegrityError("evidence context link identity mismatch")
        excerpt = _text(
            self.exact_excerpt,
            "exact_excerpt",
            _MAX_EVIDENCE_CONTEXT_BYTES,
        )
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != self.exact_excerpt_sha256:
            raise IntegrityError("exact evidence excerpt hash mismatch")
        if (
            link.evidence_span_text_sha256 is not None
            and link.evidence_span_text_sha256 != self.exact_excerpt_sha256
        ):
            raise IntegrityError("evidence context span digest mismatch")
        object.__setattr__(
            self,
            "item_hash",
            canonical_sha256(self, exclude_fields=("item_hash", "exact_excerpt")),
        )


@dataclass(frozen=True, slots=True)
class EvidenceBoundCorrectionContext:
    """Bounded exact evidence for Draft V2 and deterministic verification.

    This is a projection of already-verified Step 20-24 contracts.  It carries
    no source-publication, approval, commit, or final-answer authority.
    """

    context_version: str
    packet_input_snapshot_hash: str
    correction_packet_hash: str
    correction_packet_document_sha256: str
    step20_bundle_hashes: tuple[str, ...]
    ordered_items: tuple[EvidenceBoundCorrectionItem, ...]
    canonical_evidence_authority: bool
    final_answer_authority: bool
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.context_version != STEP38_EVIDENCE_CONTEXT_VERSION:
            raise ContractValidationError("unsupported evidence context version")
        for value, name in (
            (self.packet_input_snapshot_hash, "packet_input_snapshot_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (
                self.correction_packet_document_sha256,
                "correction_packet_document_sha256",
            ),
        ):
            require_sha256_hex(value, name)
        bundle_hashes = tuple(self.step20_bundle_hashes)
        if (
            not bundle_hashes
            or len(bundle_hashes) > 2
            or len(set(bundle_hashes)) != len(bundle_hashes)
        ):
            raise ContractValidationError("evidence context bundle hashes are invalid")
        for value in bundle_hashes:
            require_sha256_hex(value, "step20_bundle_hash")
        items = tuple(self.ordered_items)
        if (
            not items
            or len(items) > _MAX_EVIDENCE_CONTEXT_ITEMS
            or any(not isinstance(item, EvidenceBoundCorrectionItem) for item in items)
        ):
            raise ContractValidationError("evidence context items are outside bounds")
        for item in items:
            verify_evidence_bound_correction_item(item)
        expected = tuple(sorted(items, key=lambda item: item.citation_id))
        if items != expected or len({item.citation_id for item in items}) != len(items):
            raise ContractValidationError("evidence context items are not canonical")
        if (
            sum(len(item.exact_excerpt.encode("utf-8")) for item in items)
            > _MAX_EVIDENCE_CONTEXT_BYTES
        ):
            raise ContractValidationError("evidence context byte budget exceeded")
        if self.canonical_evidence_authority is not False:
            raise ContractValidationError("a Step 38 context is not source authority")
        if self.final_answer_authority is not False:
            raise ContractValidationError("a Step 38 context is not answer authority")
        object.__setattr__(self, "step20_bundle_hashes", bundle_hashes)
        object.__setattr__(self, "ordered_items", items)
        object.__setattr__(
            self,
            "context_hash",
            canonical_sha256(
                {
                    "context_version": self.context_version,
                    "packet_input_snapshot_hash": self.packet_input_snapshot_hash,
                    "correction_packet_hash": self.correction_packet_hash,
                    "correction_packet_document_sha256": (
                        self.correction_packet_document_sha256
                    ),
                    "step20_bundle_hashes": bundle_hashes,
                    "item_hashes": tuple(item.item_hash for item in items),
                    "canonical_evidence_authority": False,
                    "final_answer_authority": False,
                }
            ),
        )

    def item(self, citation_id: str) -> EvidenceBoundCorrectionItem | None:
        for item in self.ordered_items:
            if item.citation_id == citation_id:
                return item
        return None

    def provider_projection(self) -> Mapping[str, Any]:
        """Return the exact bounded payload appended only to Draft V2."""

        return {
            "context_hash": self.context_hash,
            "correction_packet_hash": self.correction_packet_hash,
            "correction_packet_document_sha256": (
                self.correction_packet_document_sha256
            ),
            "items": tuple(
                {
                    "citation_id": item.citation_id,
                    "citation_hash": item.citation_hash,
                    "relation": item.relation.value,
                    "source_id": item.source_id,
                    "knowledge_version_id": item.knowledge_version_id,
                    "chunk_id": item.chunk_id,
                    "exact_excerpt": item.exact_excerpt,
                    "exact_excerpt_sha256": item.exact_excerpt_sha256,
                }
                for item in self.ordered_items
            ),
            "authority": {
                "canonical_evidence_authority": False,
                "final_answer_authority": False,
            },
        }


def build_evidence_bound_correction_context(
    snapshot: PacketInputSnapshot,
    step20_bundles: Sequence[FrozenEvidenceBundle],
    packet: CorrectionPacketV1A,
) -> EvidenceBoundCorrectionContext:
    """Verify citation -> link -> bundle item -> exact span, then freeze context."""

    if not isinstance(snapshot, PacketInputSnapshot):
        raise ContractValidationError("snapshot must be PacketInputSnapshot")
    if not isinstance(packet, CorrectionPacketV1A):
        raise ContractValidationError("packet must be CorrectionPacketV1A")
    bundles = tuple(step20_bundles)
    if not bundles or len(bundles) > 2 or any(
        not isinstance(bundle, FrozenEvidenceBundle) for bundle in bundles
    ):
        raise ContractValidationError("Step 20 bundles are outside Step 23 bounds")
    try:
        verify_packet_input_snapshot_hash(snapshot)
        verify_correction_packet_hash(packet)
        verify_packet_against_snapshot(packet, snapshot)
        for bundle in bundles:
            verify_evidence_bundle_hash(bundle)
            for item in bundle.ordered_items:
                verify_bundle_item_hash(item)
    except (ContractValidationError, IntegrityError, RuntimeError) as exc:
        raise IntegrityError("evidence context input integrity failed") from exc

    by_bundle = {bundle.bundle_hash: bundle for bundle in bundles}
    if (
        len(by_bundle) != len(bundles)
        or tuple(by_bundle) != snapshot.step20_bundle_hashes
    ):
        raise IntegrityError("evidence context bundle lineage mismatch")
    links = {item.link_hash: item for item in snapshot.ordered_evidence_links}
    context_items: list[EvidenceBoundCorrectionItem] = []
    for citation in packet.ordered_citations:
        try:
            verify_citation_hash(citation)
            link = links[citation.evidence_link_hash]
            verify_claim_evidence_link_hash(link)
            bundle = by_bundle[link.step20_bundle_hash]
            item = bundle.ordered_items[link.step20_item_ordinal - 1]
        except (IndexError, KeyError, ContractValidationError, IntegrityError) as exc:
            raise IntegrityError("citation-to-bundle lineage mismatch") from exc
        if (
            item.item_ordinal != link.step20_item_ordinal
            or item.item_hash != link.step20_item_hash
            or item.identity.identity_hash != link.candidate_identity_hash
            or item.identity.source_id != link.source_id
            or item.identity.knowledge_version_id != link.knowledge_version_id
            or item.identity.chunk_id != link.chunk_id
            or item.identity.content_sha256 != link.content_sha256
            or citation.claim_id != link.claim_id
            or citation.relation is not link.relation
            or citation.content_sha256 != link.content_sha256
        ):
            raise IntegrityError("citation-to-bundle identity mismatch")
        if link.evidence_start_offset is None or link.evidence_end_offset is None:
            exact = item.excerpt.text
        else:
            exact = item.excerpt.text[
                link.evidence_start_offset : link.evidence_end_offset
            ]
        digest = hashlib.sha256(exact.encode("utf-8")).hexdigest()
        if digest != link.evidence_span_text_sha256:
            raise IntegrityError("citation evidence span mismatch")
        context_items.append(
            EvidenceBoundCorrectionItem(
                citation_id=citation.citation_id,
                citation_hash=citation.citation_hash,
                original_evidence_link=link,
                evidence_link_hash=link.link_hash,
                step20_bundle_hash=bundle.bundle_hash,
                step20_item_hash=item.item_hash,
                source_id=link.source_id,
                knowledge_version_id=link.knowledge_version_id,
                chunk_id=link.chunk_id,
                relation=link.relation,
                exact_excerpt=exact,
                exact_excerpt_sha256=digest,
            )
        )
    return EvidenceBoundCorrectionContext(
        context_version=STEP38_EVIDENCE_CONTEXT_VERSION,
        packet_input_snapshot_hash=snapshot.snapshot_hash,
        correction_packet_hash=packet.packet_hash,
        correction_packet_document_sha256=hashlib.sha256(
            canonical_packet_json(packet).encode("utf-8")
        ).hexdigest(),
        step20_bundle_hashes=snapshot.step20_bundle_hashes,
        ordered_items=tuple(sorted(context_items, key=lambda item: item.citation_id)),
        canonical_evidence_authority=False,
        final_answer_authority=False,
    )


def verify_evidence_bound_correction_item(
    value: EvidenceBoundCorrectionItem,
) -> None:
    if not isinstance(value, EvidenceBoundCorrectionItem):
        raise ContractValidationError(
            "value must be EvidenceBoundCorrectionItem"
        )
    verify_claim_evidence_link_hash(value.original_evidence_link)
    reconstructed = EvidenceBoundCorrectionItem(
        citation_id=value.citation_id,
        citation_hash=value.citation_hash,
        original_evidence_link=value.original_evidence_link,
        evidence_link_hash=value.evidence_link_hash,
        step20_bundle_hash=value.step20_bundle_hash,
        step20_item_hash=value.step20_item_hash,
        source_id=value.source_id,
        knowledge_version_id=value.knowledge_version_id,
        chunk_id=value.chunk_id,
        relation=value.relation,
        exact_excerpt=value.exact_excerpt,
        exact_excerpt_sha256=value.exact_excerpt_sha256,
    )
    if reconstructed.item_hash != value.item_hash:
        raise IntegrityError("evidence context item hash mismatch")


def verify_evidence_bound_correction_context(
    value: EvidenceBoundCorrectionContext,
) -> None:
    if not isinstance(value, EvidenceBoundCorrectionContext):
        raise ContractValidationError(
            "value must be EvidenceBoundCorrectionContext"
        )
    for item in value.ordered_items:
        verify_evidence_bound_correction_item(item)
    reconstructed = EvidenceBoundCorrectionContext(
        context_version=value.context_version,
        packet_input_snapshot_hash=value.packet_input_snapshot_hash,
        correction_packet_hash=value.correction_packet_hash,
        correction_packet_document_sha256=(
            value.correction_packet_document_sha256
        ),
        step20_bundle_hashes=value.step20_bundle_hashes,
        ordered_items=value.ordered_items,
        canonical_evidence_authority=value.canonical_evidence_authority,
        final_answer_authority=value.final_answer_authority,
    )
    if reconstructed.context_hash != value.context_hash:
        raise IntegrityError("evidence context hash mismatch")


def verify_corrected_evidence_context_against_inputs(
    context: EvidenceBoundCorrectionContext,
    step20_bundles: Sequence[FrozenEvidenceBundle],
    packet: CorrectionPacketV1A,
    claim_links: Sequence[ClaimEvidenceLink],
) -> None:
    """Reconstruct a corrected context from its packet, links, and exact bytes."""

    verify_evidence_bound_correction_context(context)
    verify_correction_packet_hash(packet)
    bundles = tuple(step20_bundles)
    links = tuple(claim_links)
    if (
        not bundles
        or len(bundles) > 2
        or any(not isinstance(item, FrozenEvidenceBundle) for item in bundles)
        or not links
        or any(not isinstance(item, ClaimEvidenceLink) for item in links)
    ):
        raise ContractValidationError("corrected context inputs are invalid")
    by_bundle: dict[str, FrozenEvidenceBundle] = {}
    for bundle in bundles:
        verify_evidence_bundle_hash(bundle)
        if bundle.bundle_hash in by_bundle:
            raise IntegrityError("corrected context bundle identity duplicates")
        by_bundle[bundle.bundle_hash] = bundle
        for item in bundle.ordered_items:
            verify_bundle_item_hash(item)
    link_map: dict[str, ClaimEvidenceLink] = {}
    for link in links:
        verify_claim_evidence_link_hash(link)
        if link.link_hash in link_map:
            raise IntegrityError("corrected context link identity duplicates")
        link_map[link.link_hash] = link
    citation_map = {item.citation_id: item for item in packet.ordered_citations}
    if (
        context.packet_input_snapshot_hash != packet.step23_input_snapshot_hash
        or context.correction_packet_hash != packet.packet_hash
        or context.correction_packet_document_sha256
        != hashlib.sha256(canonical_packet_json(packet).encode("utf-8")).hexdigest()
        or context.step20_bundle_hashes != packet.step20_evidence_bundle_hashes
        or set(context.step20_bundle_hashes) != set(by_bundle)
        or tuple(item.citation_id for item in context.ordered_items)
        != tuple(sorted(citation_map))
    ):
        raise IntegrityError("corrected context packet or bundle binding mismatch")
    context_link_hashes = {
        item.evidence_link_hash for item in context.ordered_items
    }
    if not set(link_map).issubset(context_link_hashes):
        raise IntegrityError("corrected context omits a supplied evidence link")
    for context_item in context.ordered_items:
        citation = citation_map[context_item.citation_id]
        verify_citation_hash(citation)
        link = context_item.original_evidence_link
        supplied_link = link_map.get(context_item.evidence_link_hash)
        bundle = by_bundle.get(context_item.step20_bundle_hash)
        if bundle is None:
            raise IntegrityError("corrected context link or bundle is missing")
        try:
            bundle_item = bundle.ordered_items[link.step20_item_ordinal - 1]
        except IndexError as exc:
            raise IntegrityError("corrected context item ordinal is invalid") from exc
        if link.evidence_start_offset is None or link.evidence_end_offset is None:
            exact_excerpt = bundle_item.excerpt.text
        else:
            exact_excerpt = bundle_item.excerpt.text[
                link.evidence_start_offset : link.evidence_end_offset
            ]
        exact_digest = hashlib.sha256(exact_excerpt.encode("utf-8")).hexdigest()
        if (
            (supplied_link is not None and supplied_link != link)
            or context_item.citation_hash != citation.citation_hash
            or citation.evidence_link_hash != link.link_hash
            or citation.claim_id != link.claim_id
            or citation.relation is not link.relation
            or bundle_item.item_hash != link.step20_item_hash
            or bundle_item.identity.identity_hash != link.candidate_identity_hash
            or bundle_item.identity.content_sha256 != link.content_sha256
            or exact_excerpt != context_item.exact_excerpt
            or exact_digest != context_item.exact_excerpt_sha256
            or exact_digest != link.evidence_span_text_sha256
        ):
            raise IntegrityError("corrected context exact evidence binding mismatch")


def verify_corrected_evidence_proofs_against_context(
    pipeline: DraftV2PipelineResult,
    packet: CorrectionPacketV1A,
    context: EvidenceBoundCorrectionContext,
) -> None:
    verify_corrected_evidence_proofs_against_packet(pipeline, packet)
    verify_evidence_bound_correction_context(context)
    if (
        context.packet_input_snapshot_hash != packet.step23_input_snapshot_hash
        or context.correction_packet_hash != packet.packet_hash
    ):
        raise IntegrityError("corrected proof context names another packet")
    for verification in pipeline.ordered_claim_verifications:
        proof = verification.corrected_evidence_proof
        if proof is None:
            continue
        item = context.item(proof.packet_citation_id)
        if (
            proof.evidence_context_hash != context.context_hash
            or item is None
            or item.item_hash not in {
                value.item_hash for value in context.ordered_items
            }
            or item.citation_hash != proof.packet_citation_hash
            or item.original_evidence_link != proof.original_evidence_link
            or item.exact_excerpt_sha256 != proof.evidence_span_text_sha256
        ):
            raise IntegrityError("corrected proof context binding mismatch")


class DraftV2TargetMode(StableStringEnum):
    """The only Step 38 output-shaping mode with an exact verifier proof."""

    EXACT_REFUTES_ONLY = "EXACT_REFUTES_ONLY"


def _render_exact_cited_claim(exact_excerpt: str, citation_id: str) -> str:
    excerpt = _text(
        exact_excerpt,
        "target exact_excerpt",
        _MAX_EVIDENCE_CONTEXT_BYTES,
    )
    _text(citation_id, "target citation_id", 255)
    if "[citation:" in excerpt:
        raise ContractValidationError("target evidence contains a citation marker")
    spans = exact_text_spans(excerpt)
    if len(spans) != 1 or spans[0].text != excerpt:
        raise ContractValidationError("target evidence must be one exact atomic span")
    if excerpt[-1] in _TERMINAL_PUNCTUATION:
        rendered = f"{excerpt[:-1]} [citation:{citation_id}]{excerpt[-1]}"
    else:
        rendered = f"{excerpt} [citation:{citation_id}]"
    rendered_spans = exact_text_spans(rendered)
    if len(rendered_spans) != 1 or rendered_spans[0].text != rendered:
        raise ContractValidationError("rendered target must remain one atomic span")
    if _CITATION_MARKER.sub("", rendered).strip() != excerpt:
        raise IntegrityError("rendered target changes the exact evidence span")
    return rendered


@dataclass(frozen=True, slots=True)
class DraftV2ExactTargetSegment:
    """One exact correction line copied from a verified REFUTES span."""

    segment_ordinal: int
    source_claim_id: str
    correction_id: str
    correction_hash: str
    correction_action: CorrectionActionType
    fact_reference_hash: str
    citation_id: str
    citation_hash: str
    evidence_link_hash: str
    evidence_context_item_hash: str
    exact_excerpt: str
    exact_excerpt_sha256: str
    rendered_text: str
    rendered_text_sha256: str
    segment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.segment_ordinal, int)
            or isinstance(self.segment_ordinal, bool)
            or self.segment_ordinal < 1
            or self.segment_ordinal > _MAX_DRAFT_V2_TARGETS
        ):
            raise ContractValidationError("target segment ordinal is invalid")
        for value, name in (
            (self.source_claim_id, "source_claim_id"),
            (self.correction_id, "correction_id"),
            (self.citation_id, "citation_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.correction_hash, "correction_hash"),
            (self.fact_reference_hash, "fact_reference_hash"),
            (self.citation_hash, "citation_hash"),
            (self.evidence_link_hash, "evidence_link_hash"),
            (self.evidence_context_item_hash, "evidence_context_item_hash"),
            (self.exact_excerpt_sha256, "exact_excerpt_sha256"),
            (self.rendered_text_sha256, "rendered_text_sha256"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.correction_action, CorrectionActionType):
            raise ContractValidationError("correction_action must be typed")
        excerpt = _text(
            self.exact_excerpt,
            "exact_excerpt",
            _MAX_EVIDENCE_CONTEXT_BYTES,
        )
        rendered = _text(
            self.rendered_text,
            "rendered_text",
            _MAX_EVIDENCE_CONTEXT_BYTES + 512,
        )
        if (
            hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
            != self.exact_excerpt_sha256
            or hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            != self.rendered_text_sha256
            or rendered != _render_exact_cited_claim(excerpt, self.citation_id)
        ):
            raise IntegrityError("target segment text binding failed")
        object.__setattr__(
            self,
            "segment_hash",
            canonical_sha256(
                self,
                exclude_fields=("segment_hash", "exact_excerpt", "rendered_text"),
            ),
        )


@dataclass(frozen=True, slots=True)
class DraftV2TargetProjection:
    """Non-authoritative, exact output whitelist derived from packet evidence."""

    projection_version: str
    mode: DraftV2TargetMode
    correction_packet_hash: str
    evidence_context_hash: str
    ordered_segments: tuple[DraftV2ExactTargetSegment, ...]
    omitted_correction_hashes: tuple[str, ...]
    prohibited_claim_hashes: tuple[str, ...]
    separator: str
    omit_original_claims: bool
    paraphrase_allowed: bool
    additional_text_allowed: bool
    canonical_evidence_authority: bool
    final_answer_authority: bool
    model_authority: bool
    expected_output_sha256: str = field(init=False)
    projection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.projection_version != STEP38_DRAFT_V2_TARGET_VERSION:
            raise ContractValidationError("unsupported Draft V2 target projection")
        if self.mode is not DraftV2TargetMode.EXACT_REFUTES_ONLY:
            raise ContractValidationError("unsupported Draft V2 target mode")
        require_sha256_hex(self.correction_packet_hash, "correction_packet_hash")
        require_sha256_hex(self.evidence_context_hash, "evidence_context_hash")
        segments = tuple(self.ordered_segments)
        if (
            not segments
            or len(segments) > _MAX_DRAFT_V2_TARGETS
            or any(not isinstance(item, DraftV2ExactTargetSegment) for item in segments)
        ):
            raise ContractValidationError("Draft V2 target segments are invalid")
        expected_ordinals = tuple(range(1, len(segments) + 1))
        if tuple(item.segment_ordinal for item in segments) != expected_ordinals:
            raise ContractValidationError("Draft V2 target segments are not canonical")
        if (
            len({item.segment_hash for item in segments}) != len(segments)
            or len({item.citation_id for item in segments}) != len(segments)
            or len({item.exact_excerpt_sha256 for item in segments}) != len(segments)
            or len({item.rendered_text for item in segments}) != len(segments)
        ):
            raise ContractValidationError("Draft V2 target segments must be unique")
        omitted = tuple(self.omitted_correction_hashes)
        if omitted != tuple(sorted(set(omitted))):
            raise ContractValidationError("omitted correction hashes are not canonical")
        for value in omitted:
            require_sha256_hex(value, "omitted_correction_hash")
        prohibited = tuple(self.prohibited_claim_hashes)
        if prohibited != tuple(sorted(set(prohibited))):
            raise ContractValidationError("prohibited claim hashes are not canonical")
        for value in prohibited:
            require_sha256_hex(value, "prohibited_claim_hash")
        if (
            self.separator != "\n"
            or self.omit_original_claims is not True
            or self.paraphrase_allowed is not False
            or self.additional_text_allowed is not False
        ):
            raise ContractValidationError("Draft V2 exact output rules were weakened")
        if (
            self.canonical_evidence_authority is not False
            or self.final_answer_authority is not False
            or self.model_authority is not False
        ):
            raise ContractValidationError("Draft V2 target projection has no authority")
        exact_output = self.separator.join(item.rendered_text for item in segments)
        object.__setattr__(self, "ordered_segments", segments)
        object.__setattr__(self, "omitted_correction_hashes", omitted)
        object.__setattr__(self, "prohibited_claim_hashes", prohibited)
        object.__setattr__(
            self,
            "expected_output_sha256",
            hashlib.sha256(exact_output.encode("utf-8")).hexdigest(),
        )
        object.__setattr__(
            self,
            "projection_hash",
            canonical_sha256(
                {
                    "projection_version": self.projection_version,
                    "mode": self.mode,
                    "correction_packet_hash": self.correction_packet_hash,
                    "evidence_context_hash": self.evidence_context_hash,
                    "segment_hashes": tuple(item.segment_hash for item in segments),
                    "omitted_correction_hashes": omitted,
                    "prohibited_claim_hashes": prohibited,
                    "separator": self.separator,
                    "omit_original_claims": True,
                    "paraphrase_allowed": False,
                    "additional_text_allowed": False,
                    "canonical_evidence_authority": False,
                    "final_answer_authority": False,
                    "model_authority": False,
                    "expected_output_sha256": self.expected_output_sha256,
                }
            ),
        )

    @property
    def exact_output(self) -> str:
        return self.separator.join(item.rendered_text for item in self.ordered_segments)

    def provider_projection(self) -> Mapping[str, Any]:
        return {
            "projection_version": self.projection_version,
            "projection_hash": self.projection_hash,
            "mode": self.mode.value,
            "correction_packet_hash": self.correction_packet_hash,
            "evidence_context_hash": self.evidence_context_hash,
            "ordered_segments": tuple(
                {
                    "segment_ordinal": item.segment_ordinal,
                    "source_claim_id": item.source_claim_id,
                    "correction_id": item.correction_id,
                    "correction_action": item.correction_action.value,
                    "citation_id": item.citation_id,
                    "citation_hash": item.citation_hash,
                    "exact_excerpt_sha256": item.exact_excerpt_sha256,
                    "rendered_text": item.rendered_text,
                    "rendered_text_sha256": item.rendered_text_sha256,
                    "segment_hash": item.segment_hash,
                }
                for item in self.ordered_segments
            ),
            "omitted_correction_hashes": self.omitted_correction_hashes,
            "separator": "NEWLINE",
            "instruction": (
                "Return exactly the ordered rendered_text lines separated by one "
                "newline. Omit original claims. Do not add, remove, group, prefix, "
                "suffix, paraphrase, explain, quote, or use Markdown."
            ),
            "expected_output_sha256": self.expected_output_sha256,
            "authority": {
                "canonical_evidence_authority": False,
                "final_answer_authority": False,
                "model_authority": False,
            },
        }

    def verify_generated_output(self, value: object) -> None:
        if not isinstance(value, str) or value != self.exact_output:
            raise ContractValidationError(
                "Draft V2 response violates the exact target whitelist"
            )
        if hashlib.sha256(value.encode("utf-8")).hexdigest() != self.expected_output_sha256:
            raise IntegrityError("Draft V2 target output digest mismatch")


def verify_draft_v2_target_projection(value: DraftV2TargetProjection) -> None:
    if not isinstance(value, DraftV2TargetProjection):
        raise ContractValidationError("value must be DraftV2TargetProjection")
    reconstructed_segments = tuple(
        DraftV2ExactTargetSegment(
            segment_ordinal=item.segment_ordinal,
            source_claim_id=item.source_claim_id,
            correction_id=item.correction_id,
            correction_hash=item.correction_hash,
            correction_action=item.correction_action,
            fact_reference_hash=item.fact_reference_hash,
            citation_id=item.citation_id,
            citation_hash=item.citation_hash,
            evidence_link_hash=item.evidence_link_hash,
            evidence_context_item_hash=item.evidence_context_item_hash,
            exact_excerpt=item.exact_excerpt,
            exact_excerpt_sha256=item.exact_excerpt_sha256,
            rendered_text=item.rendered_text,
            rendered_text_sha256=item.rendered_text_sha256,
        )
        for item in value.ordered_segments
    )
    if any(
        left.segment_hash != right.segment_hash
        for left, right in zip(value.ordered_segments, reconstructed_segments, strict=True)
    ):
        raise IntegrityError("Draft V2 target segment hash mismatch")
    reconstructed = DraftV2TargetProjection(
        projection_version=value.projection_version,
        mode=value.mode,
        correction_packet_hash=value.correction_packet_hash,
        evidence_context_hash=value.evidence_context_hash,
        ordered_segments=reconstructed_segments,
        omitted_correction_hashes=value.omitted_correction_hashes,
        prohibited_claim_hashes=value.prohibited_claim_hashes,
        separator=value.separator,
        omit_original_claims=value.omit_original_claims,
        paraphrase_allowed=value.paraphrase_allowed,
        additional_text_allowed=value.additional_text_allowed,
        canonical_evidence_authority=value.canonical_evidence_authority,
        final_answer_authority=value.final_answer_authority,
        model_authority=value.model_authority,
    )
    if (
        reconstructed.expected_output_sha256 != value.expected_output_sha256
        or reconstructed.projection_hash != value.projection_hash
    ):
        raise IntegrityError("Draft V2 target projection hash mismatch")


def build_draft_v2_target_projection(
    packet: CorrectionPacketV1A,
    context: EvidenceBoundCorrectionContext,
) -> DraftV2TargetProjection:
    """Join only exact REFUTES facts into a deterministic model output whitelist."""

    if not isinstance(packet, CorrectionPacketV1A):
        raise ContractValidationError("packet must be CorrectionPacketV1A")
    if not isinstance(context, EvidenceBoundCorrectionContext):
        raise ContractValidationError("context must be EvidenceBoundCorrectionContext")
    try:
        verify_correction_packet_hash(packet)
        verify_evidence_bound_correction_context(context)
    except (ContractValidationError, IntegrityError, RuntimeError) as exc:
        raise IntegrityError("Draft V2 target inputs failed integrity") from exc
    if (
        context.correction_packet_hash != packet.packet_hash
        or context.packet_input_snapshot_hash != packet.step23_input_snapshot_hash
        or context.correction_packet_document_sha256
        != hashlib.sha256(canonical_packet_json(packet).encode("utf-8")).hexdigest()
    ):
        raise IntegrityError("Draft V2 target packet/context binding failed")
    if not packet.ordered_required_corrections:
        raise ContractValidationError("Draft V2 target requires an exact correction")

    citations = {item.citation_id: item for item in packet.ordered_citations}
    prohibited_normalized: set[str] = set()
    prohibited_hashes: list[str] = []
    for prohibited in packet.ordered_prohibited_claims:
        verify_prohibited_claim_hash(prohibited)
        prohibited_normalized.add(
            normalize_claim_for_match(prohibited.exact_or_normalized_prohibited_content)
        )
        prohibited_hashes.append(prohibited.prohibition_hash)

    segments: list[DraftV2ExactTargetSegment] = []
    omitted_correction_hashes: list[str] = []
    seen_citations: set[str] = set()
    seen_excerpt_hashes: set[str] = set()
    for correction in packet.ordered_required_corrections:
        verify_required_correction_hash(correction)
        for fact in correction.required_replacement_facts:
            verify_fact_reference_hash(fact)
        refuting_facts = tuple(
            fact
            for fact in correction.required_replacement_facts
            if fact.relation is ClaimEvidenceRelation.REFUTES
        )
        omission_safe = correction.correction_action in {
            CorrectionActionType.REMOVE_CLAIM,
            CorrectionActionType.QUALIFY_CLAIM,
            CorrectionActionType.TEMPORAL_CORRECTION,
            CorrectionActionType.SOURCE_AUTHORITY_CORRECTION,
        }
        if not refuting_facts:
            if not omission_safe:
                raise ContractValidationError(
                    "Draft V2 target cannot safely omit this correction"
                )
            omitted_correction_hashes.append(correction.correction_hash)
            continue
        if (
            correction.correction_action is CorrectionActionType.REPLACE_CLAIM
            and len(refuting_facts) != len(correction.required_replacement_facts)
        ):
            raise ContractValidationError(
                "Draft V2 replacement target lacks exact REFUTES coverage"
            )
        if correction.correction_action is CorrectionActionType.ADD_MISSING_CONTEXT:
            raise ContractValidationError(
                "Draft V2 exact target cannot add missing context"
            )
        original_normalized = normalize_claim_for_match(correction.original_claim_text)
        correction_segments = 0
        for fact in refuting_facts:
            citation = citations.get(fact.citation_id)
            item = context.item(fact.citation_id)
            if (
                citation is None
                or item is None
                or citation.relation is not ClaimEvidenceRelation.REFUTES
                or item.relation is not ClaimEvidenceRelation.REFUTES
                or citation.claim_id != correction.claim_id
                or item.original_evidence_link.claim_id != correction.claim_id
                or fact.citation_id in seen_citations
                or fact.evidence_link_hash != citation.evidence_link_hash
                or fact.evidence_link_hash != item.evidence_link_hash
                or fact.candidate_hash != citation.candidate_hash
                or fact.content_sha256 != citation.content_sha256
                or fact.temporal_assessment_hash != citation.temporal_assessment_hash
                or item.citation_hash != citation.citation_hash
                or fact.evidence_link_hash
                not in correction.supporting_evidence_link_hashes
            ):
                raise IntegrityError("Draft V2 target REFUTES join failed")
            if item.exact_excerpt_sha256 in seen_excerpt_hashes:
                if not omission_safe:
                    raise ContractValidationError(
                        "Draft V2 target cannot deduplicate this correction"
                    )
                continue
            target_normalized = normalize_claim_for_match(item.exact_excerpt)
            if (
                target_normalized == original_normalized
                or target_normalized in prohibited_normalized
            ):
                raise ContractValidationError(
                    "Draft V2 target repeats an original or prohibited claim"
                )
            rendered = _render_exact_cited_claim(
                item.exact_excerpt,
                citation.citation_id,
            )
            segments.append(
                DraftV2ExactTargetSegment(
                    segment_ordinal=len(segments) + 1,
                    source_claim_id=correction.claim_id,
                    correction_id=correction.correction_id,
                    correction_hash=correction.correction_hash,
                    correction_action=correction.correction_action,
                    fact_reference_hash=fact.fact_reference_hash,
                    citation_id=citation.citation_id,
                    citation_hash=citation.citation_hash,
                    evidence_link_hash=fact.evidence_link_hash,
                    evidence_context_item_hash=item.item_hash,
                    exact_excerpt=item.exact_excerpt,
                    exact_excerpt_sha256=item.exact_excerpt_sha256,
                    rendered_text=rendered,
                    rendered_text_sha256=hashlib.sha256(
                        rendered.encode("utf-8")
                    ).hexdigest(),
                )
            )
            seen_citations.add(fact.citation_id)
            seen_excerpt_hashes.add(item.exact_excerpt_sha256)
            correction_segments += 1
        if correction_segments == 0:
            if not omission_safe:
                raise ContractValidationError("Draft V2 target omits a required correction")
            omitted_correction_hashes.append(correction.correction_hash)

    projection = DraftV2TargetProjection(
        projection_version=STEP38_DRAFT_V2_TARGET_VERSION,
        mode=DraftV2TargetMode.EXACT_REFUTES_ONLY,
        correction_packet_hash=packet.packet_hash,
        evidence_context_hash=context.context_hash,
        ordered_segments=tuple(segments),
        omitted_correction_hashes=tuple(sorted(omitted_correction_hashes)),
        prohibited_claim_hashes=tuple(sorted(set(prohibited_hashes))),
        separator="\n",
        omit_original_claims=True,
        paraphrase_allowed=False,
        additional_text_allowed=False,
        canonical_evidence_authority=False,
        final_answer_authority=False,
        model_authority=False,
    )
    verify_draft_v2_target_projection(projection)
    return projection


@dataclass(frozen=True, slots=True)
class EvidenceBoundProviderInputReceipt:
    """Auditable binding for the actual augmented Draft V2 provider input."""

    base_provider_request_hash: str
    augmented_provider_request_hash: str
    evidence_context_hash: str
    draft_v2_target_projection_hash: str
    correction_packet_hash: str
    provider_identity_digest: str
    augmented_user_content_sha256: str
    provider_response_hash: str
    provider_purpose: str
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.base_provider_request_hash, "base_provider_request_hash"),
            (self.augmented_provider_request_hash, "augmented_provider_request_hash"),
            (self.evidence_context_hash, "evidence_context_hash"),
            (
                self.draft_v2_target_projection_hash,
                "draft_v2_target_projection_hash",
            ),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.provider_identity_digest, "provider_identity_digest"),
            (self.augmented_user_content_sha256, "augmented_user_content_sha256"),
            (self.provider_response_hash, "provider_response_hash"),
        ):
            require_sha256_hex(value, name)
        if self.provider_purpose != DRAFT_V2_PROVIDER_PURPOSE:
            raise ContractValidationError("provider input receipt is not Draft V2")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def verify_evidence_bound_provider_input_receipt(
    value: EvidenceBoundProviderInputReceipt,
) -> None:
    if not isinstance(value, EvidenceBoundProviderInputReceipt):
        raise ContractValidationError(
            "value must be EvidenceBoundProviderInputReceipt"
        )
    reconstructed = EvidenceBoundProviderInputReceipt(
        base_provider_request_hash=value.base_provider_request_hash,
        augmented_provider_request_hash=value.augmented_provider_request_hash,
        evidence_context_hash=value.evidence_context_hash,
        draft_v2_target_projection_hash=(
            value.draft_v2_target_projection_hash
        ),
        correction_packet_hash=value.correction_packet_hash,
        provider_identity_digest=value.provider_identity_digest,
        augmented_user_content_sha256=value.augmented_user_content_sha256,
        provider_response_hash=value.provider_response_hash,
        provider_purpose=value.provider_purpose,
    )
    if reconstructed.receipt_hash != value.receipt_hash:
        raise IntegrityError("provider input receipt hash mismatch")


def _base_draft_v2_provider_request(
    draft_v1: DraftV1,
    packet: CorrectionPacketV1A,
) -> ProviderTextRequest:
    packet_document = json.loads(canonical_packet_json(packet))
    user_content = canonical_json(
        {
            "correction_packet": packet_document,
            "draft_v1": {
                "draft_v1_hash": draft_v1.draft_hash,
                "exact_text": draft_v1.draft_text,
            },
            "instruction": "Produce the corrected Draft V2 only.",
        }
    )
    prompt = load_draft_v2_prompt_template()
    return ProviderTextRequest(
        provider_identity=load_approved_provider_spec().provider_identity(),
        purpose=DRAFT_V2_PROVIDER_PURPOSE,
        prompt_template_id=prompt.template_id,
        prompt_template_digest=prompt.template_digest,
        system_instruction=prompt.system_instruction,
        user_content=user_content,
        user_content_digest=hashlib.sha256(user_content.encode("utf-8")).hexdigest(),
        generation_parameters=load_draft_v2_generation_parameters(),
    )


def _augment_draft_v2_provider_request(
    request: ProviderTextRequest,
    context: EvidenceBoundCorrectionContext,
    target_projection: DraftV2TargetProjection,
) -> ProviderTextRequest:
    document = json.loads(request.user_content)
    if not isinstance(document, Mapping):
        raise ContractValidationError("Draft V2 provider content must be an object")
    if (
        "verified_evidence_context" in document
        or "draft_v2_target_projection" in document
    ):
        raise ContractValidationError("Draft V2 request already carries Step 38 context")
    verify_evidence_bound_correction_context(context)
    verify_draft_v2_target_projection(target_projection)
    if (
        target_projection.correction_packet_hash != context.correction_packet_hash
        or target_projection.evidence_context_hash != context.context_hash
    ):
        raise IntegrityError("Draft V2 target projection names another context")
    augmented = dict(document)
    augmented["verified_evidence_context"] = context.provider_projection()
    augmented["draft_v2_target_projection"] = target_projection.provider_projection()
    user_content = canonical_json(augmented)
    return ProviderTextRequest(
        provider_identity=request.provider_identity,
        purpose=request.purpose,
        prompt_template_id=request.prompt_template_id,
        prompt_template_digest=request.prompt_template_digest,
        system_instruction=request.system_instruction,
        user_content=user_content,
        user_content_digest=hashlib.sha256(user_content.encode("utf-8")).hexdigest(),
        generation_parameters=request.generation_parameters,
    )


class EvidenceBoundDraftV2Provider:
    """Append verified exact excerpts to Draft V2 calls and no other purpose."""

    __slots__ = (
        "_provider",
        "_packet",
        "_context",
        "_target_projection",
        "_receipts",
    )

    def __init__(
        self,
        provider: TextGenerationProvider,
        packet: CorrectionPacketV1A,
        context: EvidenceBoundCorrectionContext,
        target_projection: DraftV2TargetProjection,
    ) -> None:
        if not callable(getattr(provider, "provider_identity", None)) or not callable(
            getattr(provider, "generate", None)
        ):
            raise TypeError("provider must implement TextGenerationProvider")
        if not isinstance(packet, CorrectionPacketV1A):
            raise TypeError("packet must be CorrectionPacketV1A")
        if not isinstance(context, EvidenceBoundCorrectionContext):
            raise TypeError("context must be EvidenceBoundCorrectionContext")
        if not isinstance(target_projection, DraftV2TargetProjection):
            raise TypeError("target_projection must be DraftV2TargetProjection")
        try:
            verify_correction_packet_hash(packet)
            verify_evidence_bound_correction_context(context)
            verify_draft_v2_target_projection(target_projection)
            canonical_target = build_draft_v2_target_projection(packet, context)
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise IntegrityError("target projection inputs failed integrity") from exc
        if target_projection != canonical_target:
            raise IntegrityError("target projection is not the canonical packet projection")
        approved = load_approved_provider_spec().provider_identity()
        if provider.provider_identity() != approved:
            raise ContractValidationError("provider identity is not approved")
        self._provider = provider
        self._packet = packet
        self._context = context
        self._target_projection = canonical_target
        self._receipts: list[EvidenceBoundProviderInputReceipt] = []

    def provider_identity(self):
        return self._provider.provider_identity()

    @property
    def input_receipts(self) -> tuple[EvidenceBoundProviderInputReceipt, ...]:
        return tuple(self._receipts)

    def generate(self, request: ProviderTextRequest, timeout_policy: TimeoutPolicy):
        if not isinstance(request, ProviderTextRequest):
            raise TypeError("evidence context is available only to ProviderTextRequest")
        if request.purpose != DRAFT_V2_PROVIDER_PURPOSE:
            raise ContractValidationError("evidence context is available only to Draft V2")
        if request.provider_identity != self.provider_identity():
            raise ContractValidationError("provider request identity changed")
        try:
            verify_provider_text_request_hash(request)
            verify_correction_packet_hash(self._packet)
            verify_evidence_bound_correction_context(self._context)
            verify_draft_v2_target_projection(self._target_projection)
            canonical_target = build_draft_v2_target_projection(
                self._packet,
                self._context,
            )
            if canonical_target != self._target_projection:
                raise IntegrityError("Draft V2 target projection changed")
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise IntegrityError("base Draft V2 provider request hash invalid") from exc
        try:
            document = json.loads(request.user_content)
        except json.JSONDecodeError as exc:
            raise ContractValidationError("Draft V2 provider content is invalid") from exc
        if not isinstance(document, Mapping):
            raise ContractValidationError("Draft V2 provider content must be an object")
        packet = document.get("correction_packet")
        canonical_packet_document = json.loads(canonical_packet_json(self._packet))
        if (
            not isinstance(packet, Mapping)
            or packet != canonical_packet_document
            or packet.get("packet_hash") != self._context.correction_packet_hash
            or hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()
            != self._context.correction_packet_document_sha256
        ):
            raise IntegrityError("Draft V2 request packet differs from evidence context")
        forwarded = _augment_draft_v2_provider_request(
            request,
            self._context,
            self._target_projection,
        )
        user_content = forwarded.user_content
        response = self._provider.generate(forwarded, timeout_policy)
        try:
            verify_provider_response_hash(response)
            self._target_projection.verify_generated_output(
                response.response_content
            )
        except (ContractValidationError, IntegrityError) as exc:
            raise IntegrityError("Draft V2 provider response or target invalid") from exc
        receipt = EvidenceBoundProviderInputReceipt(
            base_provider_request_hash=request.request_hash,
            augmented_provider_request_hash=forwarded.request_hash,
            evidence_context_hash=self._context.context_hash,
            draft_v2_target_projection_hash=(
                self._target_projection.projection_hash
            ),
            correction_packet_hash=self._context.correction_packet_hash,
            provider_identity_digest=request.provider_identity.identity_digest,
            augmented_user_content_sha256=hashlib.sha256(
                user_content.encode("utf-8")
            ).hexdigest(),
            provider_response_hash=response.response_hash,
            provider_purpose=request.purpose,
        )
        self._receipts.append(receipt)
        return response


class CanonicalEvidenceExactVerifier:
    """Deterministic corrected-evidence proof over exact cited spans only."""

    __slots__ = ("_context",)

    def __init__(self, context: EvidenceBoundCorrectionContext) -> None:
        if not isinstance(context, EvidenceBoundCorrectionContext):
            raise TypeError("context must be EvidenceBoundCorrectionContext")
        self._context = context

    @staticmethod
    def _signal(
        request: CorrectedEvidenceVerifierRequest,
        verdict: CorrectedEvidenceVerdict,
        references: tuple[str, ...] = (),
        proof: CorrectedEvidenceProof | None = None,
    ) -> CorrectedEvidenceVerifierSignal:
        return CorrectedEvidenceVerifierSignal(
            request_hash=request.request_hash,
            verdict=verdict,
            evidence_reference_ids=references,
            proof=proof,
        )

    def verify(
        self,
        request: CorrectedEvidenceVerifierRequest,
    ) -> CorrectedEvidenceVerifierSignal:
        if not isinstance(request, CorrectedEvidenceVerifierRequest):
            raise TypeError("request must be CorrectedEvidenceVerifierRequest")
        try:
            verify_corrected_evidence_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise IntegrityError("corrected evidence request hash invalid") from exc
        if request.correction_packet_hash != self._context.correction_packet_hash:
            return self._signal(request, CorrectedEvidenceVerdict.UNCERTAIN)
        cited = tuple(sorted(set(_CITATION_MARKER.findall(request.claim_text))))
        if not cited or cited != request.cited_citation_ids:
            return self._signal(request, CorrectedEvidenceVerdict.UNCERTAIN)
        claim_text = _CITATION_MARKER.sub("", request.claim_text).strip()
        if not claim_text:
            return self._signal(request, CorrectedEvidenceVerdict.UNCERTAIN)
        for citation_id in cited:
            item = self._context.item(citation_id)
            if item is None or item.relation is not ClaimEvidenceRelation.REFUTES:
                continue
            exact_spans = {span.text for span in exact_text_spans(item.exact_excerpt)}
            text_sha256 = hashlib.sha256(claim_text.encode("utf-8")).hexdigest()
            link = item.original_evidence_link
            if (
                claim_text in exact_spans
                and link.evidence_span_text_sha256 is not None
                and text_sha256 == item.exact_excerpt_sha256
                and text_sha256 == link.evidence_span_text_sha256
            ):
                proof = CorrectedEvidenceProof(
                    proof_version=CORRECTED_EVIDENCE_PROOF_VERSION,
                    request_hash=request.request_hash,
                    correction_packet_hash=request.correction_packet_hash,
                    target_claim_id=request.claim_id,
                    target_claim_hash=request.claim_hash,
                    target_claim_text_sha256=text_sha256,
                    satisfied_correction_ids=request.satisfied_correction_ids,
                    packet_citation_id=item.citation_id,
                    packet_citation_hash=item.citation_hash,
                    evidence_context_hash=self._context.context_hash,
                    original_evidence_link=link,
                    evidence_span_text_sha256=item.exact_excerpt_sha256,
                )
                return self._signal(
                    request,
                    CorrectedEvidenceVerdict.SUPPORTS,
                    (citation_id,),
                    proof,
                )
        return self._signal(request, CorrectedEvidenceVerdict.UNCERTAIN)


@dataclass(frozen=True, slots=True)
class GermanLawBeforeAfterTrace:
    trace_version: str
    case_id: str
    question_digest: str
    draft_v1_hash: str
    defect_claim_ids: tuple[str, ...]
    correction_packet_hash: str
    evidence_context_hash: str
    draft_v2_target_projection_hash: str
    augmented_provider_input_receipt_hash: str
    evidence_reference_hashes: tuple[str, ...]
    draft_v2_hash: str
    verification_summary_hash: str
    verified_answer_hash: str
    trace_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.trace_version != STEP38_TRACE_VERSION:
            raise ContractValidationError("unsupported Step 38 trace version")
        _logical_id(self.case_id, "case_id")
        for value, name in (
            (self.question_digest, "question_digest"),
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.evidence_context_hash, "evidence_context_hash"),
            (
                self.draft_v2_target_projection_hash,
                "draft_v2_target_projection_hash",
            ),
            (
                self.augmented_provider_input_receipt_hash,
                "augmented_provider_input_receipt_hash",
            ),
            (self.draft_v2_hash, "draft_v2_hash"),
            (self.verification_summary_hash, "verification_summary_hash"),
            (self.verified_answer_hash, "verified_answer_hash"),
        ):
            require_sha256_hex(value, name)
        claims = _string_tuple(
            self.defect_claim_ids,
            "defect_claim_ids",
            maximum=256,
            ordered=True,
        )
        if not claims:
            raise ContractValidationError("a corrected trace requires a defect claim")
        references = tuple(self.evidence_reference_hashes)
        if not references or references != tuple(sorted(set(references))):
            raise ContractValidationError("evidence references must be canonical")
        for value in references:
            require_sha256_hex(value, "evidence_reference_hash")
        object.__setattr__(self, "defect_claim_ids", claims)
        object.__setattr__(self, "evidence_reference_hashes", references)
        object.__setattr__(
            self,
            "trace_hash",
            canonical_sha256(self, exclude_fields=("trace_hash",)),
        )


def build_before_after_trace(
    case: GermanLawGoldenCase,
    draft_v1: DraftV1,
    packet: CorrectionPacketV1A,
    step25_result: DraftV2PipelineResult,
    final_outcome: FinalAnswerOutcome,
    evidence_context: EvidenceBoundCorrectionContext,
    provider_input_receipt: EvidenceBoundProviderInputReceipt,
) -> GermanLawBeforeAfterTrace:
    """Bind the primary correction proof to existing immutable contracts."""

    if not isinstance(case, GermanLawGoldenCase):
        raise ContractValidationError("case must be a GermanLawGoldenCase")
    if not isinstance(evidence_context, EvidenceBoundCorrectionContext):
        raise ContractValidationError("evidence_context must be typed")
    if not isinstance(provider_input_receipt, EvidenceBoundProviderInputReceipt):
        raise ContractValidationError("provider_input_receipt must be typed")
    try:
        verify_draft_v1_hash(draft_v1)
        verify_correction_packet_hash(packet)
        verify_draft_v2_pipeline_result_hash(step25_result)
        verify_corrected_evidence_proofs_against_context(
            step25_result,
            packet,
            evidence_context,
        )
        verify_final_answer_outcome_hash(final_outcome)
        verify_evidence_bound_provider_input_receipt(provider_input_receipt)
    except (ContractValidationError, IntegrityError, RuntimeError) as exc:
        raise IntegrityError("Step 38 trace input integrity failed") from exc
    answer = final_outcome.verified_answer
    generation_result = step25_result.generation_result
    if (
        final_outcome.output_status is not FinalOutputStatus.VERIFIED_ANSWER
        or answer is None
        or generation_result is None
        or step25_result.verification_summary.summary_status
        is not VerificationSummaryStatus.VERIFIED
    ):
        raise ContractValidationError("before/after trace requires a verified answer")
    try:
        verify_verified_answer_hash(answer)
        target_projection = build_draft_v2_target_projection(
            packet,
            evidence_context,
        )
        target_projection.verify_generated_output(step25_result.draft_v2.draft_text)
        base_provider_request = _base_draft_v2_provider_request(draft_v1, packet)
        augmented_provider_request = _augment_draft_v2_provider_request(
            base_provider_request,
            evidence_context,
            target_projection,
        )
        verify_provider_text_request_hash(base_provider_request)
        verify_provider_text_request_hash(augmented_provider_request)
    except (ContractValidationError, IntegrityError, RuntimeError) as exc:
        raise IntegrityError("Step 38 trace reconstruction failed") from exc
    draft_v2 = step25_result.draft_v2
    summary = step25_result.verification_summary
    approved_identity = load_approved_provider_spec().provider_identity()
    expected_answer_references = tuple(
        (
            item.claim_id,
            item.claim_hash,
            item.verification_hash,
            item.final_step25_verdict,
        )
        for item in step25_result.ordered_claim_verifications
    )
    actual_answer_references = tuple(
        (
            item.claim_id,
            item.claim_hash,
            item.verification_hash,
            item.final_verdict,
        )
        for item in answer.claim_verification_references
    )
    if (
        draft_v1.original_query_digest != case.question_digest
        or packet.draft_v1_hash != draft_v1.draft_hash
        or draft_v2.draft_v1_hash != draft_v1.draft_hash
        or draft_v2.correction_packet_hash != packet.packet_hash
        or draft_v2.provider_identity_digest != approved_identity.identity_digest
        or draft_v2.provider_identity_digest
        != base_provider_request.provider_identity.identity_digest
        or draft_v2.prompt_template_digest
        != base_provider_request.prompt_template_digest
        or draft_v2.generation_parameters_digest
        != base_provider_request.generation_parameters.parameters_digest
        or generation_result.provider_identity_digest
        != draft_v2.provider_identity_digest
        or generation_result.provider_response_hash
        != provider_input_receipt.provider_response_hash
        or answer.draft_v2_hash != draft_v2.draft_v2_hash
        or answer.verification_summary_hash != summary.summary_hash
        or answer.correction_packet_hash != packet.packet_hash
        or answer.packet_integrity_receipt_hash
        != draft_v2.packet_integrity_receipt_hash
        or answer.answer_text != draft_v2.draft_text
        or answer.answer_text_sha256 != draft_v2.draft_text_sha256
        or actual_answer_references != expected_answer_references
        or evidence_context.correction_packet_hash != packet.packet_hash
        or provider_input_receipt.base_provider_request_hash
        != base_provider_request.request_hash
        or provider_input_receipt.augmented_provider_request_hash
        != augmented_provider_request.request_hash
        or provider_input_receipt.evidence_context_hash != evidence_context.context_hash
        or provider_input_receipt.draft_v2_target_projection_hash
        != target_projection.projection_hash
        or provider_input_receipt.correction_packet_hash != packet.packet_hash
        or provider_input_receipt.provider_identity_digest
        != approved_identity.identity_digest
        or provider_input_receipt.augmented_user_content_sha256
        != augmented_provider_request.user_content_digest
        or provider_input_receipt.provider_purpose != DRAFT_V2_PROVIDER_PURPOSE
    ):
        raise IntegrityError("Step 38 trace lineage mismatch")
    defects = tuple(
        sorted({item.claim_id for item in packet.ordered_required_corrections})
    )
    references = tuple(sorted(item.citation_hash for item in packet.ordered_citations))
    return GermanLawBeforeAfterTrace(
        trace_version=STEP38_TRACE_VERSION,
        case_id=case.case_id,
        question_digest=case.question_digest,
        draft_v1_hash=draft_v1.draft_hash,
        defect_claim_ids=defects,
        correction_packet_hash=packet.packet_hash,
        evidence_context_hash=evidence_context.context_hash,
        draft_v2_target_projection_hash=target_projection.projection_hash,
        augmented_provider_input_receipt_hash=(
            provider_input_receipt.receipt_hash
        ),
        evidence_reference_hashes=references,
        draft_v2_hash=step25_result.draft_v2.draft_v2_hash,
        verification_summary_hash=step25_result.verification_summary.summary_hash,
        verified_answer_hash=answer.answer_hash,
    )


__all__ = [
    "CanonicalEvidenceExactVerifier",
    "DraftV2ExactTargetSegment",
    "DraftV2TargetMode",
    "DraftV2TargetProjection",
    "EvidenceBoundCorrectionContext",
    "EvidenceBoundCorrectionItem",
    "EvidenceBoundDraftV2Provider",
    "EvidenceBoundProviderInputReceipt",
    "GoldenCaseKind",
    "GoldenCorrectionCondition",
    "GoldenExpectedFinalOutput",
    "GermanLawBeforeAfterTrace",
    "GermanLawGoldenCase",
    "GermanLawGoldenCaseSuite",
    "GermanLawTemporalProjectionReceipt",
    "HashBoundOfficialEvidenceProjection",
    "REAL_OFFICIAL_IDENTIFIER",
    "REAL_HAT_ID",
    "REAL_HAT_SCOPE_ID",
    "REAL_HAT_VERSION",
    "REAL_PROVISION_HASHES",
    "REAL_SOURCE_ID",
    "REAL_VERSION_IDENTITY",
    "STEP38_PROJECTION_VERSION",
    "STEP38_TEMPORAL_PROJECTION_VERSION",
    "STEP38_EVIDENCE_CONTEXT_VERSION",
    "STEP38_DRAFT_V2_TARGET_VERSION",
    "STEP38_SCHEMA_VERSION",
    "STEP38_SUITE_ID",
    "STEP38_TRACE_VERSION",
    "Step38Component",
    "Step38FixtureClass",
    "RealCorpusFixture",
    "RealCorpusProvision",
    "build_before_after_trace",
    "build_draft_v2_target_projection",
    "build_evidence_bound_correction_context",
    "load_german_law_golden_cases",
    "project_verified_bmjernano_evidence",
    "project_bmjernano_temporal_facts",
    "prove_draft_v1_evidence_blind",
    "step38_component_inventory",
    "verify_corrected_evidence_context_against_inputs",
    "verify_corrected_evidence_proofs_against_context",
    "verify_draft_v2_target_projection",
    "verify_evidence_bound_correction_context",
    "verify_evidence_bound_correction_item",
    "verify_evidence_bound_provider_input_receipt",
]
