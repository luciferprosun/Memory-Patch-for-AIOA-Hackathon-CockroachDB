#!/usr/bin/env python3
"""Same-database Step 18-20 adapter for the Step 38 German Law proof.

The caller owns the already-running CockroachDB process, migrated database,
approved local E5 backend, and external-volume cache.  This module never
starts, migrates, drops, or cleans a runtime and never calls a hosted provider,
AWS, or S3.  It projects exact Step 14-16 BMJErnAnO bytes into the caller's
synthetic tenant, exercises the production Step 18/19/20 services, and returns
typed artifacts plus a hash-only runtime attestation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Protocol, TypeVar


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402

from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    EvidenceStatus,
    KnowledgeRoute,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import (  # noqa: E402
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension  # noqa: E402
from aioa_memory_kernel.embeddings import (  # noqa: E402
    EmbeddingBackend,
    EmbeddingBackendIdentity,
    EmbeddingGenerationRequest,
    EmbeddingGenerationResult,
    EmbeddingGenerationService,
    PassageEmbeddingCache,
    VectorRetrievalRequest,
    VectorRetrievalResult,
    VectorRetrievalService,
    load_approved_model_spec,
    verify_generation_result_hash,
    verify_vector_result_hash,
)
from aioa_memory_kernel.embeddings.local_e5 import LocalE5Backend  # noqa: E402
from aioa_memory_kernel.evidence import (  # noqa: E402
    DEFAULT_CONTEXT_BUDGET_BYTES,
    MAX_BUNDLE_ITEMS,
    STEP18_RETRIEVAL_POLICY_VERSION,
    HybridEvidenceOutcome,
    HybridEvidenceService,
    HybridModality,
    HybridRetrievalRequest,
    load_diversity_policy,
    load_ranking_policy,
    verify_evidence_bundle_hash,
    verify_outcome_hash,
)
from aioa_memory_kernel.german_law.corpus import (  # noqa: E402
    build_source_registry_record,
)
from aioa_memory_kernel.german_law.e2e import (  # noqa: E402
    REAL_HAT_ID,
    REAL_HAT_SCOPE_ID,
    REAL_HAT_VERSION,
    REAL_OFFICIAL_IDENTIFIER,
    REAL_PROVISION_HASHES,
    REAL_SOURCE_ID,
    REAL_VERSION_IDENTITY,
    GermanLawTemporalProjectionReceipt,
    GermanLawGoldenCase,
    GermanLawGoldenCaseSuite,
    GoldenCaseKind,
    Step38FixtureClass,
    project_bmjernano_temporal_facts,
)
from aioa_memory_kernel.german_law.e2e_runtime import (  # noqa: E402
    STEP38_DATABASE_RETRIEVAL_PROOF_VERSION,
    Step38DatabaseRetrievalProof,
)
from aioa_memory_kernel.hats import (  # noqa: E402
    CompatibilityDecision,
    HatRegistryService,
    ReviewActor,
    ReviewReceipt,
    RuntimeBinding,
    decide_compatibility,
    decode_manifest,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.retrieval import (  # noqa: E402
    ExactIdentifierField,
    ExactIdentifierSelector,
    FullTextQuery,
    KeywordQuery,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    RetrievalService,
    StatuteSectionSelector,
    verify_result_hash,
)
from aioa_memory_kernel.routing import (  # noqa: E402
    AuthorityPolicyContext,
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    HatPolicyRequirement,
    HatRoutingCandidate,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    RoutingInput,
    TrustedHatRegistrySnapshot,
    evaluate_policy_gate,
    route_knowledge_request,
    verify_policy_result_hash,
    verify_route_hash,
    verify_routing_input_hash,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    SourceAuthorityLevel,
    SourcePublicationState,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose  # noqa: E402


ADAPTER_VERSION = "step38-same-database-real-retrieval-1a"
ATTESTATION_VERSION = "step38-real-retrieval-attestation-1a"
CORPUS_BYTES_CLASS = "REAL_GERMAN_LAW_CORPUS_FIXTURE"
TEMPORAL_PROJECTION_CLASS = (
    "FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE_APPLIED_BEFORE_RETRIEVAL"
)
_FULL_TEXT_QUERY = "Anordnung Januar Kraft"
_KEYWORDS = ("Anordnungen", "Gegenstand")
_BACKUP_FULL_TEXT_QUERY = "besondere Fälle behalte Ernennung Entlassung"
_BACKUP_KEYWORDS = ("besondere Fälle", "behalte", "Ernennung", "Entlassung")
_PRIMARY_LATER_QUESTION = (
    "Ab welchem Datum gilt die BMJErnAnO?"
)
_BACKUP_LATER_QUESTION = (
    "Ist die Ernennung und Entlassung der unter I. genannten Beamtinnen und "
    "Beamten nach Abschnitt II der BMJErnAnO für besondere Fälle "
    "vorbehalten?"
)
_NEGATIVE_SOURCE_IDS = (
    "step38-retrieval-unpublished",
    "step38-retrieval-weak-authority",
    "step38-retrieval-other-hat",
    "step38-retrieval-other-tenant",
)


class Step38RealRetrievalError(RuntimeError):
    """Sanitized fail-closed error from this validation-only adapter."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 38 same-database retrieval proof failed")
        self.code = code


_PhaseResult = TypeVar("_PhaseResult")
_SANITIZED_REAL_PHASE_CODES = frozenset(
    {
        "PRIMARY_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
        "RELATED_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
        "BACKUP_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
    }
)


def _run_sanitized_real_phase(
    code: str,
    operation: Callable[[], _PhaseResult],
) -> _PhaseResult:
    """Map only contract/integrity faults to a closed, non-sensitive code."""

    if code not in _SANITIZED_REAL_PHASE_CODES:
        raise ValueError("unknown Step 38 real retrieval phase code")
    if not callable(operation):
        raise TypeError("operation must be callable")
    try:
        return operation()
    except Step38RealRetrievalError:
        raise
    except (ContractValidationError, IntegrityError):
        raise Step38RealRetrievalError(code) from None


def canonical_later_question(case: GermanLawGoldenCase) -> str:
    """Return the only accepted later query for the selected real case."""

    if not isinstance(case, GermanLawGoldenCase):
        raise TypeError("case must be a GermanLawGoldenCase")
    if case.case_id == "primary-entry-into-force":
        return _PRIMARY_LATER_QUESTION
    if case.case_id == "backup-special-case-reservation":
        return _BACKUP_LATER_QUESTION
    raise Step38RealRetrievalError("SUPPORTED_REAL_GOLDEN_CASE_REQUIRED")


class CallerOwnedSqlClient(Protocol):
    """Narrow caller-owned surface implemented by the Step 30 SQL client."""

    port: int
    sql_port: int | None

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class Step38CorpusRoots:
    step14_bundle_root: Path
    step15_bundle_root: Path
    step16_bundle_root: Path
    source_root: Path

    def __post_init__(self) -> None:
        for name in (
            "step14_bundle_root",
            "step15_bundle_root",
            "step16_bundle_root",
            "source_root",
        ):
            value = getattr(self, name)
            if not isinstance(value, Path):
                raise TypeError(f"{name} must be a Path")


@dataclass(frozen=True, slots=True)
class Step38GermanLawRetrievalInput:
    routing_input: RoutingInput
    route: KnowledgeRouteResult
    policy_context: AuthorityPolicyContext
    policy_result: PolicyGateResult
    golden_case: GermanLawGoldenCase
    question_text: str
    provision_selector: StatuteSectionSelector
    input_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.routing_input, RoutingInput):
            raise TypeError("routing_input must be a RoutingInput")
        if not isinstance(self.route, KnowledgeRouteResult):
            raise TypeError("route must be a KnowledgeRouteResult")
        if not isinstance(self.policy_context, AuthorityPolicyContext):
            raise TypeError("policy_context must be an AuthorityPolicyContext")
        if not isinstance(self.policy_result, PolicyGateResult):
            raise TypeError("policy_result must be a PolicyGateResult")
        if not isinstance(self.golden_case, GermanLawGoldenCase):
            raise TypeError("golden_case must be a GermanLawGoldenCase")
        if not isinstance(self.provision_selector, StatuteSectionSelector):
            raise TypeError("provision_selector must be a StatuteSectionSelector")
        try:
            verify_routing_input_hash(self.routing_input)
            verify_route_hash(self.route)
            verify_policy_result_hash(self.policy_result)
        except (ContractValidationError, IntegrityError) as exc:
            raise Step38RealRetrievalError("ROUTE_OR_POLICY_HASH_INVALID") from exc
        derived_route = route_knowledge_request(self.routing_input)
        if derived_route.route_hash != self.route.route_hash:
            raise Step38RealRetrievalError("ROUTE_NOT_DERIVED_FROM_INPUT")
        if (
            self.route.knowledge_route
            not in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}
            or self.route.selected_hat_id != REAL_HAT_ID
            or self.route.selected_hat_version != REAL_HAT_VERSION
        ):
            raise Step38RealRetrievalError("GERMAN_LAW_ROUTE_REQUIRED")
        if evaluate_policy_gate(
            self.routing_input,
            self.route,
            self.policy_context,
        ) != self.policy_result:
            raise Step38RealRetrievalError("ROUTE_POLICY_BINDING_MISMATCH")
        if (
            self.policy_result.request_id,
            self.policy_result.tenant_id,
            self.policy_result.user_id,
            self.policy_result.route_hash,
        ) != (
            self.route.request_id,
            self.route.tenant_id,
            self.route.user_id,
            self.route.route_hash,
        ):
            raise Step38RealRetrievalError("ROUTE_POLICY_BINDING_MISMATCH")
        if (
            self.golden_case.case_kind
            not in {GoldenCaseKind.PRIMARY, GoldenCaseKind.BACKUP}
            or self.golden_case.fixture_class
            is not Step38FixtureClass.REAL_GERMAN_LAW_CORPUS_FIXTURE
            or self.golden_case.expected_source_id != REAL_SOURCE_ID
            or len(self.golden_case.expected_provision_ids) != 1
            or self.golden_case.expected_provision_ids[0]
            not in REAL_PROVISION_HASHES
            or self.question_text != self.golden_case.question
            or hashlib.sha256(self.question_text.encode("utf-8")).hexdigest()
            != self.golden_case.question_digest
        ):
            raise Step38RealRetrievalError("REAL_GOLDEN_QUESTION_REQUIRED")
        if (
            self.provision_selector.statute_identifier
            != REAL_OFFICIAL_IDENTIFIER
            or self.provision_selector.section_identifier
            != self.golden_case.expected_provision_ids[0]
        ):
            raise Step38RealRetrievalError("GOLDEN_PROVISION_SELECTOR_REQUIRED")
        object.__setattr__(
            self,
            "input_hash",
            canonical_sha256(self, exclude_fields=("input_hash", "question_text")),
        )


@dataclass(frozen=True, slots=True)
class Step38RelatedGermanLawRetrievalInput:
    """A later, non-duplicate German Law query in the same owner scope."""

    routing_input: RoutingInput
    route: KnowledgeRouteResult
    policy_context: AuthorityPolicyContext
    policy_result: PolicyGateResult
    primary_input: Step38GermanLawRetrievalInput
    question_text: str
    provision_selector: StatuteSectionSelector
    input_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.routing_input, RoutingInput):
            raise TypeError("routing_input must be a RoutingInput")
        if not isinstance(self.route, KnowledgeRouteResult):
            raise TypeError("route must be a KnowledgeRouteResult")
        if not isinstance(self.policy_context, AuthorityPolicyContext):
            raise TypeError("policy_context must be an AuthorityPolicyContext")
        if not isinstance(self.policy_result, PolicyGateResult):
            raise TypeError("policy_result must be a PolicyGateResult")
        if not isinstance(self.primary_input, Step38GermanLawRetrievalInput):
            raise TypeError("primary_input must be Step38GermanLawRetrievalInput")
        if not isinstance(self.provision_selector, StatuteSectionSelector):
            raise TypeError("provision_selector must be a StatuteSectionSelector")
        if (
            not isinstance(self.question_text, str)
            or not self.question_text.strip()
            or self.question_text != self.question_text.strip()
            or len(self.question_text.encode("utf-8")) > 4096
            or hashlib.sha256(self.question_text.encode("utf-8")).hexdigest()
            == self.primary_input.golden_case.question_digest
        ):
            raise ContractValidationError(
                "later question must be bounded and differ from the primary question"
            )
        if self.question_text != canonical_later_question(
            self.primary_input.golden_case
        ):
            raise ContractValidationError(
                "later question must match the selected real golden case"
            )
        try:
            verify_routing_input_hash(self.routing_input)
            verify_route_hash(self.route)
            verify_policy_result_hash(self.policy_result)
        except (ContractValidationError, IntegrityError) as exc:
            raise Step38RealRetrievalError("ROUTE_OR_POLICY_HASH_INVALID") from exc
        if (
            route_knowledge_request(self.routing_input).route_hash
            != self.route.route_hash
        ):
            raise Step38RealRetrievalError("ROUTE_NOT_DERIVED_FROM_INPUT")
        if (
            self.route.knowledge_route
            not in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}
            or self.route.selected_hat_id != REAL_HAT_ID
            or self.route.selected_hat_version != REAL_HAT_VERSION
            or self.routing_input.normalized_query_or_subject != self.question_text
            or self.route.request_id == self.primary_input.route.request_id
            or self.route.tenant_id != self.primary_input.route.tenant_id
            or self.route.user_id != self.primary_input.route.user_id
            or self.route.selected_hat_id
            != self.primary_input.route.selected_hat_id
            or self.route.selected_hat_version
            != self.primary_input.route.selected_hat_version
            or self.route.selected_manifest_digest
            != self.primary_input.route.selected_manifest_digest
            or canonical_sha256(self.route.effective_scope)
            != canonical_sha256(self.primary_input.route.effective_scope)
            or (
                self.policy_result.request_id,
                self.policy_result.tenant_id,
                self.policy_result.user_id,
                self.policy_result.route_hash,
            )
            != (
                self.route.request_id,
                self.route.tenant_id,
                self.route.user_id,
                self.route.route_hash,
            )
        ):
            raise Step38RealRetrievalError("RELATED_GERMAN_LAW_ROUTE_INVALID")
        if evaluate_policy_gate(
            self.routing_input,
            self.route,
            self.policy_context,
        ) != self.policy_result:
            raise Step38RealRetrievalError("ROUTE_POLICY_BINDING_MISMATCH")
        if (
            self.provision_selector.statute_identifier
            != REAL_OFFICIAL_IDENTIFIER
            or self.provision_selector.section_identifier
            != self.primary_input.golden_case.expected_provision_ids[0]
        ):
            raise Step38RealRetrievalError("GOLDEN_PROVISION_SELECTOR_REQUIRED")
        object.__setattr__(
            self,
            "input_hash",
            canonical_sha256(
                {
                    "routing_input_hash": self.routing_input.input_hash,
                    "route_hash": self.route.route_hash,
                    "policy_result_hash": self.policy_result.policy_result_hash,
                    "primary_input_hash": self.primary_input.input_hash,
                    "question_digest": hashlib.sha256(
                        self.question_text.encode("utf-8")
                    ).hexdigest(),
                    "provision_selector": self.provision_selector,
                }
            ),
        )


def _canonical_manifest_entry(identity: object, at) -> object:
    """Build the exact trusted Step 12 entry consumed by the Step 17 router."""

    manifest = identity.manifest
    service = HatRegistryService(clock=lambda: at)
    compatibility = decide_compatibility(identity)
    service.register(identity, compatibility)
    service.validate(manifest.hat_id, manifest.hat_version)
    implementation_digest = canonical_sha256(
        {
            "installation_class": "SYSTEM_INSTALLED",
            "hat_id": manifest.hat_id,
            "hat_version": manifest.hat_version,
            "adapter": ADAPTER_VERSION,
        }
    )
    binding = RuntimeBinding(
        "german-law-system-step38",
        manifest.hat_id,
        manifest.hat_version,
        "GermanLawHat",
        manifest.hat_version,
        "hat-sdk-1a",
        implementation_digest,
    )
    receipt = ReviewReceipt(
        manifest.hat_id,
        manifest.hat_version,
        identity.typed_manifest_digest,
        identity.raw_manifest_sha256,
        identity.schema_file_sha256,
        CompatibilityDecision.COMPATIBLE,
        canonical_sha256(manifest.capabilities),
        binding.runtime_binding_id,
        implementation_digest,
        "ENABLE",
        ("TRUSTED_STEP38_CANONICAL_INPUT",),
        ReviewActor.TRUSTED_OPERATOR,
        "operator-redacted",
        at,
    )
    return service.enable(
        manifest.hat_id,
        manifest.hat_version,
        binding,
        receipt,
    )


def build_canonical_primary_retrieval_input(
    suite: GermanLawGoldenCaseSuite,
    *,
    tenant_id: str,
    user_id: str,
    request_id: str,
) -> Step38GermanLawRetrievalInput:
    """Run Step 17 over the real manifest and bind its primary Step 38 case."""

    if not isinstance(suite, GermanLawGoldenCaseSuite):
        raise TypeError("suite must be a GermanLawGoldenCaseSuite")
    case = suite.case("primary-entry-into-force")
    identity = decode_manifest(
        (ROOT / "config/hats/german-law-1.0.0.json").read_bytes(),
        schema_path=ROOT / "schemas/hat-manifest.schema.json",
    )
    entry = _canonical_manifest_entry(identity, case.knowledge_as_of)
    candidate = HatRoutingCandidate(
        identity.manifest.hat_id,
        identity.manifest.hat_version,
        identity.typed_manifest_digest,
        HatPolicyRequirement.ADVISORY,
    )
    requested_scope = (
        ScopeDimension(
            "legal_jurisdiction",
            "DE_FEDERAL",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            "step38-canonical-primary",
            True,
        ),
        ScopeDimension(
            "knowledge_as_of",
            case.knowledge_as_of,
            ScopeValueType.TIMESTAMP,
            ScopeComparisonMode.TIMESTAMP,
            "step38-canonical-primary",
            True,
        ),
        ScopeDimension(
            "source_language",
            "de",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            "step38-canonical-primary",
            True,
        ),
        ScopeDimension(
            "legal_source_class",
            ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
            ScopeValueType.STRING_SET,
            ScopeComparisonMode.IN_SET,
            "step38-canonical-primary",
            True,
        ),
    )
    routing_input = RoutingInput(
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        request_kind="knowledge-question",
        normalized_query_or_subject=case.question,
        requested_domain_id="law.de.federal",
        requested_scope=requested_scope,
        candidate_hat_descriptors=(candidate,),
        trusted_hat_registry_snapshot=TrustedHatRegistrySnapshot(
            "trusted-registry:step38:canonical-primary",
            (entry,),
        ),
        evidence_status=EvidenceStatus.INSUFFICIENT,
        evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
        context_metadata={
            "classification_source": "deterministic-german-law-domain",
            "golden_case_hash": case.case_hash,
        },
    )
    selected_route = route_knowledge_request(routing_input)
    if (
        selected_route.knowledge_route is not KnowledgeRoute.HAT_ASSIST
        or selected_route.selected_manifest_digest
        != identity.typed_manifest_digest
    ):
        raise Step38RealRetrievalError("CANONICAL_GERMAN_LAW_ROUTE_FAILED")
    policy_context = AuthorityPolicyContext(
        request_id=request_id,
        tenant_id=tenant_id,
        user_id=user_id,
        policy_reference="policy:step38:retrieval-preflight",
        policy_digest=canonical_sha256(
            {
                "policy_id": "step38-german-law-retrieval",
                "policy_version": "1a",
            }
        ),
        knowledge_policy_ceiling=KnowledgePolicyDecision.ALLOW_ANSWER,
        execution_authorization_ceiling=ExecutionAuthorizationDecision.DENY,
        scope_allowed=True,
        selected_hat_id=selected_route.selected_hat_id,
        selected_hat_version=selected_route.selected_hat_version,
        selected_manifest_digest=selected_route.selected_manifest_digest,
    )
    selected_policy = evaluate_policy_gate(
        routing_input,
        selected_route,
        policy_context,
    )
    return Step38GermanLawRetrievalInput(
        routing_input=routing_input,
        route=selected_route,
        policy_context=policy_context,
        policy_result=selected_policy,
        golden_case=case,
        question_text=case.question,
        provision_selector=StatuteSectionSelector(
            REAL_OFFICIAL_IDENTIFIER,
            "III.",
        ),
    )


def build_canonical_backup_retrieval_input(
    primary_input: Step38GermanLawRetrievalInput,
    suite: GermanLawGoldenCaseSuite,
    *,
    request_id: str,
) -> Step38GermanLawRetrievalInput:
    """Run the declared real backup case after a correct primary Draft V1.

    The backup keeps the exact tenant, owner, HAT, manifest, and scope of the
    canonical primary attempt.  It changes only the request identity, natural
    question, case binding, and exact provision selector.  The caller reuses
    the already seeded owned database; no second runtime or corpus projection
    is permitted.
    """

    if not isinstance(primary_input, Step38GermanLawRetrievalInput):
        raise TypeError("primary_input must be Step38GermanLawRetrievalInput")
    if not isinstance(suite, GermanLawGoldenCaseSuite):
        raise TypeError("suite must be a GermanLawGoldenCaseSuite")
    if primary_input.golden_case.case_kind is not GoldenCaseKind.PRIMARY:
        raise ContractValidationError("canonical primary input is required")
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or request_id == primary_input.route.request_id
    ):
        raise ContractValidationError("backup request_id must be distinct")
    case = suite.case("backup-special-case-reservation")
    if (
        case.case_kind is not GoldenCaseKind.BACKUP
        or case.fixture_class
        is not Step38FixtureClass.REAL_GERMAN_LAW_CORPUS_FIXTURE
        or case.expected_source_id != REAL_SOURCE_ID
        or case.expected_provision_ids != ("II.",)
    ):
        raise Step38RealRetrievalError("CANONICAL_BACKUP_CASE_INVALID")
    metadata = dict(primary_input.routing_input.context_metadata)
    metadata.update(
        {
            "classification_source": "deterministic-german-law-domain",
            "golden_case_hash": case.case_hash,
            "step38_primary_attempt_input_hash": primary_input.input_hash,
            "step38_query_class": "REAL_BACKUP_GOLDEN_CASE",
        }
    )
    routing_input = replace(
        primary_input.routing_input,
        request_id=request_id,
        normalized_query_or_subject=case.question,
        evidence_status=EvidenceStatus.INSUFFICIENT,
        evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
        context_metadata=metadata,
    )
    route = route_knowledge_request(routing_input)
    policy_context = replace(
        primary_input.policy_context,
        request_id=request_id,
        policy_reference="policy:step38:backup-retrieval-preflight",
        policy_digest=canonical_sha256(
            {
                "policy_id": "step38-german-law-backup-retrieval",
                "policy_version": "1a",
                "primary_attempt_input_hash": primary_input.input_hash,
                "backup_case_hash": case.case_hash,
            }
        ),
    )
    policy_result = evaluate_policy_gate(routing_input, route, policy_context)
    return Step38GermanLawRetrievalInput(
        routing_input=routing_input,
        route=route,
        policy_context=policy_context,
        policy_result=policy_result,
        golden_case=case,
        question_text=case.question,
        provision_selector=StatuteSectionSelector(
            REAL_OFFICIAL_IDENTIFIER,
            "II.",
        ),
    )


def build_canonical_related_retrieval_input(
    primary_input: Step38GermanLawRetrievalInput,
    *,
    question_text: str,
    request_id: str,
) -> Step38RelatedGermanLawRetrievalInput:
    """Run Step 17 again for a distinct later question in the same scope."""

    if not isinstance(primary_input, Step38GermanLawRetrievalInput):
        raise TypeError("primary_input must be Step38GermanLawRetrievalInput")
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or request_id == primary_input.route.request_id
    ):
        raise ContractValidationError("later request_id must be distinct")
    if question_text != canonical_later_question(primary_input.golden_case):
        raise ContractValidationError(
            "later question must match the selected real golden case"
        )
    metadata = dict(primary_input.routing_input.context_metadata)
    metadata.update(
        {
            "classification_source": "deterministic-german-law-domain",
            "step38_primary_input_hash": primary_input.input_hash,
            "step38_query_class": "LATER_RELATED_QUESTION",
        }
    )
    routing_input = replace(
        primary_input.routing_input,
        request_id=request_id,
        normalized_query_or_subject=question_text,
        evidence_status=EvidenceStatus.INSUFFICIENT,
        evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
        context_metadata=metadata,
    )
    route = route_knowledge_request(routing_input)
    policy_context = replace(
        primary_input.policy_context,
        request_id=request_id,
        policy_reference="policy:step38:related-retrieval-preflight",
        policy_digest=canonical_sha256(
            {
                "policy_id": "step38-german-law-related-retrieval",
                "policy_version": "1a",
                "primary_input_hash": primary_input.input_hash,
            }
        ),
    )
    policy_result = evaluate_policy_gate(routing_input, route, policy_context)
    return Step38RelatedGermanLawRetrievalInput(
        routing_input=routing_input,
        route=route,
        policy_context=policy_context,
        policy_result=policy_result,
        primary_input=primary_input,
        question_text=question_text,
        provision_selector=StatuteSectionSelector(
            REAL_OFFICIAL_IDENTIFIER,
            primary_input.golden_case.expected_provision_ids[0],
        ),
    )


@dataclass(frozen=True, slots=True)
class Step38RealRetrievalAttestation:
    attestation_version: str
    adapter_version: str
    input_hash: str
    runtime_instance_digest: str
    database_instance_digest: str
    step14_manifest_digest: str
    step15_manifest_digest: str
    step16_manifest_digest: str
    source_id: str
    official_identifier: str
    version_identity: str
    provision_hashes: tuple[tuple[str, str], ...]
    corpus_bytes_class: str
    temporal_projection_class: str
    temporal_projection_receipt_hash: str
    retrieval_input_kind: str
    lexical_result_hashes: tuple[str, ...]
    embedding_record_hashes: tuple[str, ...]
    embedding_result_hash: str
    embedding_backend_identity_digest: str
    embedding_verified_files_digest: str
    approved_local_e5_backend: bool
    vector_result_hash: str
    hybrid_outcome_hash: str
    evidence_bundle_hash: str
    data_plane_credential_purpose: CredentialPurpose
    data_plane_session_user: str
    cross_tenant_rls_visible_count: int
    negative_source_leak_count: int
    same_database: bool
    provider_calls: int
    aws_calls: int
    s3_mutations: int
    migration_calls: int
    runtime_lifecycle_calls: int
    attestation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.attestation_version != ATTESTATION_VERSION
            or self.adapter_version != ADAPTER_VERSION
        ):
            raise ContractValidationError("unsupported Step 38 retrieval attestation")
        for value, name in (
            (self.input_hash, "input_hash"),
            (self.runtime_instance_digest, "runtime_instance_digest"),
            (self.database_instance_digest, "database_instance_digest"),
            (self.step14_manifest_digest, "step14_manifest_digest"),
            (self.step15_manifest_digest, "step15_manifest_digest"),
            (self.step16_manifest_digest, "step16_manifest_digest"),
            (
                self.temporal_projection_receipt_hash,
                "temporal_projection_receipt_hash",
            ),
            (self.embedding_result_hash, "embedding_result_hash"),
            (
                self.embedding_backend_identity_digest,
                "embedding_backend_identity_digest",
            ),
            (
                self.embedding_verified_files_digest,
                "embedding_verified_files_digest",
            ),
            (self.vector_result_hash, "vector_result_hash"),
            (self.hybrid_outcome_hash, "hybrid_outcome_hash"),
            (self.evidence_bundle_hash, "evidence_bundle_hash"),
        ):
            require_sha256_hex(value, name)
        if (
            self.source_id != REAL_SOURCE_ID
            or self.official_identifier != REAL_OFFICIAL_IDENTIFIER
            or self.version_identity != REAL_VERSION_IDENTITY
            or self.provision_hashes != tuple(REAL_PROVISION_HASHES.items())
            or self.corpus_bytes_class != CORPUS_BYTES_CLASS
            or self.temporal_projection_class != TEMPORAL_PROJECTION_CLASS
            or self.retrieval_input_kind not in {"PRIMARY", "RELATED"}
            or not isinstance(self.approved_local_e5_backend, bool)
            or self.data_plane_credential_purpose
            is not CredentialPurpose.APPLICATION_DATABASE
            or not isinstance(self.data_plane_session_user, str)
            or not self.data_plane_session_user
            or self.data_plane_session_user in {"root", "admin"}
        ):
            raise ContractValidationError("real corpus attestation identity changed")
        for collection, name in (
            (self.lexical_result_hashes, "lexical_result_hashes"),
            (self.embedding_record_hashes, "embedding_record_hashes"),
        ):
            if not collection:
                raise ContractValidationError(f"{name} must be non-empty")
            for value in collection:
                require_sha256_hex(value, name)
        if (
            self.negative_source_leak_count != 0
            or self.cross_tenant_rls_visible_count != 0
            or self.provider_calls != 0
            or self.aws_calls != 0
            or self.s3_mutations != 0
            or self.migration_calls != 0
            or self.runtime_lifecycle_calls != 0
            or self.same_database is not True
        ):
            raise ContractValidationError("retrieval attestation safety invariant failed")
        object.__setattr__(
            self,
            "attestation_hash",
            canonical_sha256(self, exclude_fields=("attestation_hash",)),
        )


@dataclass(frozen=True, slots=True)
class Step38RealRetrievalArtifacts:
    retrieval_input: (
        Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput
    )
    lexical_inputs: tuple[tuple[RetrievalRequest, RetrievalResult], ...]
    embedding_request: EmbeddingGenerationRequest
    embedding_result: EmbeddingGenerationResult
    embedding_replay: EmbeddingGenerationResult
    vector_request: VectorRetrievalRequest
    vector_result: VectorRetrievalResult
    vector_replay: VectorRetrievalResult
    hybrid_request: HybridRetrievalRequest
    hybrid_outcome: HybridEvidenceOutcome
    hybrid_replay: HybridEvidenceOutcome
    temporal_projection_receipt: GermanLawTemporalProjectionReceipt
    embedding_backend_identity: EmbeddingBackendIdentity
    embedding_verified_files: tuple[tuple[str, str, int], ...]
    approved_local_e5_backend: bool
    attestation: Step38RealRetrievalAttestation
    artifacts_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(
            self.retrieval_input,
            (Step38GermanLawRetrievalInput, Step38RelatedGermanLawRetrievalInput),
        ):
            raise TypeError("retrieval_input must be typed")
        if not isinstance(self.embedding_backend_identity, EmbeddingBackendIdentity):
            raise TypeError("embedding_backend_identity must be typed")
        if not isinstance(self.approved_local_e5_backend, bool):
            raise TypeError("approved_local_e5_backend must be boolean")
        verified_files = tuple(self.embedding_verified_files)
        for item in verified_files:
            if (
                not isinstance(item, tuple)
                or len(item) != 3
                or not isinstance(item[0], str)
                or not item[0]
                or not isinstance(item[1], str)
                or not isinstance(item[2], int)
            ):
                raise ContractValidationError("embedding verified files are invalid")
            require_sha256_hex(item[1], "embedding_verified_file_sha256")
        if verified_files != tuple(sorted(set(verified_files))):
            raise ContractValidationError("embedding verified files are not canonical")
        object.__setattr__(self, "embedding_verified_files", verified_files)
        if self.approved_local_e5_backend and (
            self.embedding_backend_identity.backend_name
            != "local-transformers-e5"
            or self.embedding_backend_identity.model_digest
            != load_approved_model_spec().model_digest
            or not verified_files
        ):
            raise IntegrityError("approved Local E5 backend proof is invalid")
        if len(self.lexical_inputs) != 4:
            raise ContractValidationError("all four Step 18 modes are required")
        for request, result in self.lexical_inputs:
            verify_result_hash(result)
            if (
                request.request_hash
                not in self.hybrid_request.lexical_request_hashes
                or (
                    result.request_id,
                    result.tenant_id,
                    result.user_id,
                    result.route_hash,
                )
                != (
                    self.retrieval_input.route.request_id,
                    self.retrieval_input.route.tenant_id,
                    self.retrieval_input.route.user_id,
                    self.retrieval_input.route.route_hash,
                )
            ):
                raise IntegrityError("Step 18 request is detached from Step 20")
        verify_generation_result_hash(self.embedding_result)
        verify_generation_result_hash(self.embedding_replay)
        verify_vector_result_hash(self.vector_result)
        verify_vector_result_hash(self.vector_replay)
        verify_outcome_hash(self.hybrid_outcome)
        verify_outcome_hash(self.hybrid_replay)
        if (
            not isinstance(
                self.temporal_projection_receipt,
                GermanLawTemporalProjectionReceipt,
            )
            or self.attestation.temporal_projection_receipt_hash
            != self.temporal_projection_receipt.receipt_hash
            or self.temporal_projection_receipt.source_id != REAL_SOURCE_ID
            or self.temporal_projection_receipt.version_identity
            != REAL_VERSION_IDENTITY
            or self.temporal_projection_receipt.provision_content_sha256
            != REAL_PROVISION_HASHES["III."]
            or self.temporal_projection_receipt.canonical_evidence_authority
            is not False
            or self.hybrid_outcome.bundle is None
            or self.hybrid_replay.bundle is None
            or self.hybrid_outcome.bundle.bundle_hash
            != self.hybrid_replay.bundle.bundle_hash
            or self.attestation.evidence_bundle_hash
            != self.hybrid_outcome.bundle.bundle_hash
            or self.attestation.input_hash != self.retrieval_input.input_hash
            or self.attestation.retrieval_input_kind
            != (
                "PRIMARY"
                if isinstance(self.retrieval_input, Step38GermanLawRetrievalInput)
                else "RELATED"
            )
            or self.attestation.lexical_result_hashes
            != tuple(result.result_hash for _, result in self.lexical_inputs)
            or self.attestation.embedding_record_hashes
            != tuple(value.record_hash for value in self.embedding_result.records)
            or self.attestation.embedding_result_hash
            != self.embedding_result.result_hash
            or self.attestation.embedding_backend_identity_digest
            != canonical_sha256(self.embedding_backend_identity)
            or self.attestation.embedding_verified_files_digest
            != canonical_sha256(verified_files)
            or self.attestation.approved_local_e5_backend
            is not self.approved_local_e5_backend
            or self.attestation.vector_result_hash != self.vector_result.result_hash
            or self.attestation.hybrid_outcome_hash
            != self.hybrid_outcome.outcome_hash
            or self.vector_replay.result_hash != self.vector_result.result_hash
            or tuple(value.record_hash for value in self.embedding_replay.records)
            != tuple(value.record_hash for value in self.embedding_result.records)
            or self.hybrid_outcome.hybrid_request_hash
            != self.hybrid_request.request_hash
            or self.vector_result.request_id != self.retrieval_input.route.request_id
            or self.vector_result.route_hash != self.retrieval_input.route.route_hash
            or self.embedding_result.request_id
            != self.retrieval_input.route.request_id
            or self.embedding_result.route_hash
            != self.retrieval_input.route.route_hash
        ):
            raise IntegrityError("Step 20 deterministic replay differs")
        object.__setattr__(
            self,
            "artifacts_hash",
            canonical_sha256(
                {
                    "input_hash": self.retrieval_input.input_hash,
                    "lexical_result_hashes": tuple(
                        result.result_hash for _, result in self.lexical_inputs
                    ),
                    "embedding_result_hash": self.embedding_result.result_hash,
                    "embedding_replay_hash": self.embedding_replay.result_hash,
                    "vector_result_hash": self.vector_result.result_hash,
                    "vector_replay_hash": self.vector_replay.result_hash,
                    "hybrid_outcome_hash": self.hybrid_outcome.outcome_hash,
                    "hybrid_replay_hash": self.hybrid_replay.outcome_hash,
                    "temporal_projection_receipt_hash": (
                        self.temporal_projection_receipt.receipt_hash
                    ),
                    "attestation_hash": self.attestation.attestation_hash,
                }
            ),
        )


def _load_fixture(
    roots: Step38CorpusRoots,
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...], object, str]:
    args = SimpleNamespace(
        step14_bundle_root=roots.step14_bundle_root,
        step15_bundle_root=roots.step15_bundle_root,
        step16_bundle_root=roots.step16_bundle_root,
        source_root=roots.source_root,
    )
    item, _first, candidate = step18._real_fixture(args)
    path_values = item.get("alias_provisions_relative_paths")
    if (
        not isinstance(path_values, list)
        or len(path_values) != 1
        or not isinstance(path_values[0], str)
    ):
        raise Step38RealRetrievalError("REAL_CORPUS_PROVISION_PATH_INVALID")
    source_root = roots.source_root.resolve(strict=True)
    provision_path = (source_root / path_values[0]).resolve(strict=True)
    if provision_path.is_symlink() or not provision_path.is_relative_to(source_root):
        raise Step38RealRetrievalError("REAL_CORPUS_PROVISION_PATH_UNSAFE")
    provisions = tuple(
        step18._jsonl_first(
            provision_path,
            lambda value, identifier=identifier: value.get(
                "provision_identifier"
            )
            == identifier,
        )
        for identifier in REAL_PROVISION_HASHES
    )
    for provision, (identifier, expected_hash) in zip(
        provisions,
        REAL_PROVISION_HASHES.items(),
        strict=True,
    ):
        content = provision.get("official_text_de")
        if (
            provision.get("provision_identifier") != identifier
            or provision.get("content_sha256") != expected_hash
            or not isinstance(content, str)
            or hashlib.sha256(content.encode("utf-8")).hexdigest()
            != expected_hash
        ):
            raise Step38RealRetrievalError("REAL_CORPUS_PROVISION_DIGEST_MISMATCH")
    if (
        item.get("source_id") != REAL_SOURCE_ID
        or item.get("official_identifier") != REAL_OFFICIAL_IDENTIFIER
        or item.get("version_identity") != REAL_VERSION_IDENTITY
        or item.get("state") != "PUBLISHED"
    ):
        raise Step38RealRetrievalError("REAL_CORPUS_FIXTURE_IDENTITY_MISMATCH")
    manifest = decode_manifest(
        (ROOT / "config/hats/german-law-1.0.0.json").read_bytes(),
        schema_path=ROOT / "schemas/hat-manifest.schema.json",
    )
    return item, provisions, candidate, manifest.typed_manifest_digest


def _fixture_records(base: object, tenant_id: str) -> tuple[object, ...]:
    authority = base.authority.authority_level
    published = step18._clone_record(
        base,
        tenant_id=tenant_id,
        source_id=REAL_SOURCE_ID,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        state=SourcePublicationState.PUBLISHED,
        authority=authority,
    )
    unpublished = step18._clone_record(
        base,
        tenant_id=tenant_id,
        source_id=_NEGATIVE_SOURCE_IDS[0],
        hat_scope_id=REAL_HAT_SCOPE_ID,
        state=SourcePublicationState.REGISTERED,
        authority=authority,
    )
    weak = step18._clone_record(
        base,
        tenant_id=tenant_id,
        source_id=_NEGATIVE_SOURCE_IDS[1],
        hat_scope_id=REAL_HAT_SCOPE_ID,
        state=SourcePublicationState.PUBLISHED,
        authority=SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
    )
    other_hat = step18._clone_record(
        base,
        tenant_id=tenant_id,
        source_id=_NEGATIVE_SOURCE_IDS[2],
        hat_scope_id="german-law-step38-other",
        state=SourcePublicationState.PUBLISHED,
        authority=authority,
    )
    other_tenant = step18._clone_record(
        base,
        tenant_id=(
            "step38-other-"
            + hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]
        ),
        source_id=_NEGATIVE_SOURCE_IDS[3],
        hat_scope_id=REAL_HAT_SCOPE_ID,
        state=SourcePublicationState.PUBLISHED,
        authority=authority,
    )
    return published, unpublished, weak, other_hat, other_tenant


def _temporal_projection_sql(
    *,
    tenant_id: str,
    receipt: GermanLawTemporalProjectionReceipt,
) -> str:
    """Project the provision-III date into this owned validation fixture.

    Provision III governs the complete BMJErnAnO document.  The update is
    restricted to the exact published source and all three hash-verified
    provisions.  It records the reconstructible receipt and grants neither
    canonical-evidence nor publication authority.
    """

    if not isinstance(receipt, GermanLawTemporalProjectionReceipt):
        raise TypeError("receipt must be GermanLawTemporalProjectionReceipt")
    metadata = {
        "effective_from": receipt.effective_from_date,
        "verified_at": step18.FIXTURE_TIME.isoformat().replace("+00:00", "Z"),
        "version_status": "CURRENT",
        "step38_temporal_projection_receipt_hash": receipt.receipt_hash,
        "step38_temporal_projection_method": receipt.projection_method,
        "step38_temporal_projection_source_content_sha256": (
            receipt.provision_content_sha256
        ),
        "step38_temporal_projection_fixture_bound": True,
        "step38_temporal_projection_model_inference_used": False,
        "step38_temporal_projection_canonical_evidence_authority": False,
    }
    q = migrations.sql_literal
    return (
        "UPDATE memory_patch.knowledge_chunks SET metadata = metadata || "
        + q(canonical_json(metadata))
        + "::JSONB WHERE tenant_id = "
        + q(tenant_id)
        + " AND source_id = "
        + q(REAL_SOURCE_ID)
        + " AND content_sha256 IN ("
        + ", ".join(q(value) for value in REAL_PROVISION_HASHES.values())
        + ")"
    )


def _retrieval_request(
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
    mode: RetrievalMode,
    selector: object,
) -> RetrievalRequest:
    route = value.route
    return RetrievalRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        retrieval_mode=mode,
        selector=selector,  # type: ignore[arg-type]
        maximum_results=20,
    )


def _full_text_query(
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
) -> str:
    selected_case = (
        value.golden_case
        if isinstance(value, Step38GermanLawRetrievalInput)
        else value.primary_input.golden_case
    )
    if selected_case.case_kind is GoldenCaseKind.BACKUP:
        return _BACKUP_FULL_TEXT_QUERY
    return _FULL_TEXT_QUERY


def _keyword_query(
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
) -> tuple[str, ...]:
    selected_case = (
        value.golden_case
        if isinstance(value, Step38GermanLawRetrievalInput)
        else value.primary_input.golden_case
    )
    if selected_case.case_kind is GoldenCaseKind.BACKUP:
        return _BACKUP_KEYWORDS
    return _KEYWORDS


def _embedding_request(
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
) -> EmbeddingGenerationRequest:
    route = value.route
    return EmbeddingGenerationRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        model_digest=load_approved_model_spec().model_digest,
        batch_size=3,
        maximum_items=8,
    )


def _vector_request(
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
) -> VectorRetrievalRequest:
    route = value.route
    return VectorRetrievalRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        query_text=value.question_text,
        model_digest=load_approved_model_spec().model_digest,
        maximum_results=20,
    )


def _hybrid_request(
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
    lexical_inputs: tuple[tuple[RetrievalRequest, RetrievalResult], ...],
    vector_input: tuple[VectorRetrievalRequest, VectorRetrievalResult],
) -> HybridRetrievalRequest:
    route = value.route
    ranking = load_ranking_policy()
    return HybridRetrievalRequest(
        route=route,
        policy_result=value.policy_result,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        policy_result_hash=value.policy_result.policy_result_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        effective_scope=route.effective_scope,
        personal_memory_space_id=None,
        requested_modalities=(
            HybridModality.EXACT_IDENTIFIER,
            HybridModality.STATUTE_SECTION,
            HybridModality.FULL_TEXT,
            HybridModality.KEYWORD,
            HybridModality.VECTOR,
        ),
        lexical_request_hashes=tuple(
            request.request_hash for request, _result in lexical_inputs
        ),
        lexical_result_hashes=tuple(
            result.result_hash for _request, result in lexical_inputs
        ),
        vector_request_hash=vector_input[0].request_hash,
        vector_result_hash=vector_input[1].result_hash,
        embedding_model_digest=load_approved_model_spec().model_digest,
        step18_retrieval_policy_version=STEP18_RETRIEVAL_POLICY_VERSION,
        ranking_policy_id=ranking.policy_id,
        ranking_policy_version=ranking.policy_version,
        ranking_policy_digest=ranking.policy_digest,
        diversity_policy_digest=load_diversity_policy().policy_digest,
        context_budget_bytes=DEFAULT_CONTEXT_BUDGET_BYTES,
        maximum_bundle_items=MAX_BUNDLE_ITEMS,
    )


def _scalar(output: str, expected_column: str) -> str:
    rows = tuple(csv.DictReader(io.StringIO(output), delimiter="\t"))
    if len(rows) != 1 or expected_column not in rows[0]:
        raise Step38RealRetrievalError("CALLER_DATABASE_PROBE_INVALID")
    return str(rows[0][expected_column])


def _assert_database_is_empty(
    root: CallerOwnedSqlClient,
    database: str,
    tenant_id: str,
) -> None:
    count = _scalar(
        root.execute(
            database,
            "SELECT count(*) AS probe_value FROM "
            "memory_patch.source_registry_entries WHERE tenant_id = "
            + migrations.sql_literal(tenant_id)
            + " AND source_id IN ("
            + ", ".join(
                migrations.sql_literal(value)
                for value in (REAL_SOURCE_ID, *_NEGATIVE_SOURCE_IDS)
            )
            + ")",
            timeout=60,
        ),
        "probe_value",
    )
    if count != "0":
        raise Step38RealRetrievalError("RETRIEVAL_FIXTURE_ALREADY_PRESENT")


def _data_plane_probe(
    runner: SerializableTransactionRunner,
    value: Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput,
    *,
    expected_session_user: str,
) -> tuple[str, int]:
    """Prove the data plane uses the app credential and FORCE-RLS context."""

    runner.require_credential_purpose(CredentialPurpose.APPLICATION_DATABASE)
    if (
        not isinstance(expected_session_user, str)
        or not expected_session_user
        or expected_session_user in {"root", "admin"}
    ):
        raise Step38RealRetrievalError("DATA_PLANE_ROLE_INVALID")

    def probe(transaction):
        return transaction.fetch_one(
            "SELECT session_user AS session_user, current_user AS current_user, "
            "(SELECT count(*) FROM memory_patch.source_registry_entries "
            "WHERE source_id = %s) AS cross_tenant_visible",
            (_NEGATIVE_SOURCE_IDS[3],),
        )

    row = runner.run(
        RequestContext(
            value.route.tenant_id,
            None,
            AccessMode.TENANT_SHARED,
        ),
        probe,
        operation_kind="STEP38_RETRIEVAL_ROLE_RLS_PROBE",
    )
    if row is None:
        raise Step38RealRetrievalError("DATA_PLANE_ROLE_PROBE_EMPTY")
    session_user = str(row.get("session_user", ""))
    current_user = str(row.get("current_user", ""))
    try:
        cross_tenant_visible = int(row.get("cross_tenant_visible", -1))
    except (TypeError, ValueError) as exc:
        raise Step38RealRetrievalError("DATA_PLANE_RLS_PROBE_INVALID") from exc
    if (
        session_user != expected_session_user
        or current_user != expected_session_user
        or cross_tenant_visible != 0
    ):
        raise Step38RealRetrievalError("DATA_PLANE_ROLE_OR_RLS_MISMATCH")
    return session_user, cross_tenant_visible


def _embedding_backend_facts(
    backend: EmbeddingBackend,
) -> tuple[EmbeddingBackendIdentity, tuple[tuple[str, str, int], ...], bool]:
    identity = backend.identity()
    if not isinstance(identity, EmbeddingBackendIdentity):
        raise Step38RealRetrievalError("EMBEDDING_BACKEND_IDENTITY_INVALID")
    files_value = getattr(backend, "verified_files", ())
    files = (
        tuple(sorted(set(files_value)))
        if isinstance(files_value, (tuple, list))
        else ()
    )
    approved = isinstance(backend, LocalE5Backend)
    if approved and (
        identity.backend_name != "local-transformers-e5"
        or identity.model_digest != load_approved_model_spec().model_digest
        or not files
    ):
        raise Step38RealRetrievalError("APPROVED_LOCAL_E5_IDENTITY_INVALID")
    return identity, files, approved


def _run_step38_retrieval_on_owned_database(
    retrieval_input: (
        Step38GermanLawRetrievalInput | Step38RelatedGermanLawRetrievalInput
    ),
    *,
    root: CallerOwnedSqlClient,
    database: str,
    database_runner: SerializableTransactionRunner,
    data_plane_session_user: str,
    runtime_instance_digest: str,
    database_instance_digest: str,
    corpus_roots: Step38CorpusRoots,
    embedding_backend: EmbeddingBackend,
    embedding_cache: PassageEmbeddingCache,
    seed_fixture: bool,
    allow_restored_primary: bool = False,
) -> Step38RealRetrievalArtifacts:
    """Exercise Step 18-20 on one caller-owned, already-migrated database."""

    if not isinstance(
        retrieval_input,
        (Step38GermanLawRetrievalInput, Step38RelatedGermanLawRetrievalInput),
    ):
        raise TypeError("retrieval_input must be a typed Step 38 input")
    if not isinstance(database_runner, SerializableTransactionRunner):
        raise TypeError("database_runner must be SerializableTransactionRunner")
    database_runner.require_credential_purpose(
        CredentialPurpose.APPLICATION_DATABASE
    )
    if not isinstance(corpus_roots, Step38CorpusRoots):
        raise TypeError("corpus_roots must be Step38CorpusRoots")
    if not isinstance(embedding_cache, PassageEmbeddingCache):
        raise TypeError("embedding_cache must be PassageEmbeddingCache")
    if not isinstance(seed_fixture, bool) or not isinstance(allow_restored_primary, bool):
        raise TypeError("fixture mode flags must be bool")
    if seed_fixture and allow_restored_primary:
        raise Step38RealRetrievalError("RESTORED_PRIMARY_CANNOT_SEED")
    migrations.validate_database_identifier(database)
    require_sha256_hex(runtime_instance_digest, "runtime_instance_digest")
    require_sha256_hex(database_instance_digest, "database_instance_digest")
    if not callable(getattr(root, "execute", None)):
        raise TypeError("root must be a caller-owned SQL client")

    item, provisions, candidate, manifest_digest = _load_fixture(corpus_roots)
    provision_iii = next(
        value
        for value in provisions
        if value.get("provision_identifier") == "III."
    )
    temporal_projection_receipt = project_bmjernano_temporal_facts(
        str(provision_iii["official_text_de"])
    )
    if retrieval_input.route.selected_manifest_digest != manifest_digest:
        raise Step38RealRetrievalError("GERMAN_LAW_MANIFEST_DIGEST_MISMATCH")
    if seed_fixture:
        if not isinstance(retrieval_input, Step38GermanLawRetrievalInput):
            raise Step38RealRetrievalError("PRIMARY_INPUT_REQUIRED_FOR_SEED")
        _assert_database_is_empty(root, database, retrieval_input.route.tenant_id)
        base = build_source_registry_record(candidate, created_at=step18.FIXTURE_TIME)
        records = _fixture_records(base, retrieval_input.route.tenant_id)
        root.execute(
            database,
            step18._seed_sql(
                records,
                item,
                provisions,
                manifest_digest,
            )
            + ";\n"
            + _temporal_projection_sql(
                tenant_id=retrieval_input.route.tenant_id,
                receipt=temporal_projection_receipt,
            ),
            timeout=300,
        )
    elif not (
        isinstance(retrieval_input, Step38RelatedGermanLawRetrievalInput)
        or (
            isinstance(retrieval_input, Step38GermanLawRetrievalInput)
            and (
                retrieval_input.golden_case.case_kind is GoldenCaseKind.BACKUP
                or (
                    allow_restored_primary
                    and retrieval_input.golden_case.case_kind
                    is GoldenCaseKind.PRIMARY
                )
            )
        )
    ):
        raise Step38RealRetrievalError("REUSE_INPUT_REQUIRED")

    session_user, cross_tenant_visible = _data_plane_probe(
        database_runner,
        retrieval_input,
        expected_session_user=data_plane_session_user,
    )
    backend_identity, verified_files, approved_local_e5 = _embedding_backend_facts(
        embedding_backend
    )
    runner = database_runner
    retrieval_service = RetrievalService(runner)
    requests = (
        _retrieval_request(
            retrieval_input,
            RetrievalMode.EXACT_IDENTIFIER,
            ExactIdentifierSelector(
                ExactIdentifierField.OFFICIAL_IDENTIFIER,
                (REAL_OFFICIAL_IDENTIFIER,),
            ),
        ),
        _retrieval_request(
            retrieval_input,
            RetrievalMode.STATUTE_SECTION,
            retrieval_input.provision_selector,
        ),
        _retrieval_request(
            retrieval_input,
            RetrievalMode.FULL_TEXT,
            FullTextQuery(_full_text_query(retrieval_input)),
        ),
        _retrieval_request(
            retrieval_input,
            RetrievalMode.KEYWORD,
            KeywordQuery(_keyword_query(retrieval_input)),
        ),
    )
    lexical_inputs: list[tuple[RetrievalRequest, RetrievalResult]] = []
    for request in requests:
        result = retrieval_service.retrieve(request)
        replay = retrieval_service.retrieve(request)
        if (
            not result.candidates
            or result.result_hash != replay.result_hash
            or {value.source_id for value in result.candidates}
            != {REAL_SOURCE_ID}
        ):
            raise Step38RealRetrievalError("STEP18_REAL_RETRIEVAL_OR_REPLAY_FAILED")
        lexical_inputs.append((request, result))
    expected_provision_id = retrieval_input.provision_selector.section_identifier
    expected_provision_hash = REAL_PROVISION_HASHES[expected_provision_id]
    statute_result = lexical_inputs[1][1]
    if (
        len(statute_result.candidates) != 1
        or statute_result.candidates[0].structured_metadata.get(
            "provision_identifier"
        )
        != expected_provision_id
        or statute_result.candidates[0].content_sha256
        != expected_provision_hash
    ):
        raise Step38RealRetrievalError("GOLDEN_PROVISION_RETRIEVAL_MISMATCH")

    negative_leaks = 0
    for source_id in _NEGATIVE_SOURCE_IDS:
        negative = retrieval_service.retrieve(
            _retrieval_request(
                retrieval_input,
                RetrievalMode.EXACT_IDENTIFIER,
                ExactIdentifierSelector(
                    ExactIdentifierField.SOURCE_ID,
                    (source_id,),
                ),
            )
        )
        negative_leaks += len(negative.candidates)
    if negative_leaks:
        raise Step38RealRetrievalError("STEP18_HARD_FILTER_NEGATIVE_LEAK")

    generation_request = _embedding_request(retrieval_input)
    generation_service = EmbeddingGenerationService(
        runner,
        embedding_backend,
        embedding_cache,
    )
    generated = generation_service.generate(generation_request)
    generated_replay = generation_service.generate(generation_request)
    if (
        len(generated.records) != len(REAL_PROVISION_HASHES)
        or tuple(value.record_hash for value in generated.records)
        != tuple(value.record_hash for value in generated_replay.records)
        or generated_replay.generated_count != 0
        or generated_replay.cache_hits != len(REAL_PROVISION_HASHES)
    ):
        raise Step38RealRetrievalError("STEP19_EMBEDDING_REPLAY_MISMATCH")

    vector_request = _vector_request(retrieval_input)
    vector_service = VectorRetrievalService(runner, embedding_backend)
    vector_result = vector_service.retrieve(vector_request)
    vector_replay = vector_service.retrieve(vector_request)
    if (
        not vector_result.candidates
        or vector_result.result_hash != vector_replay.result_hash
        or {value.source_id for value in vector_result.candidates}
        != {REAL_SOURCE_ID}
        or expected_provision_hash
        not in {value.content_sha256 for value in vector_result.candidates}
    ):
        raise Step38RealRetrievalError("STEP19_VECTOR_RETRIEVAL_OR_REPLAY_FAILED")

    lexical_tuple = tuple(lexical_inputs)
    vector_input = (vector_request, vector_result)
    hybrid_request = _hybrid_request(
        retrieval_input,
        lexical_tuple,
        vector_input,
    )
    hybrid_service = HybridEvidenceService()
    hybrid_outcome = hybrid_service.assemble(
        hybrid_request,
        lexical_inputs=tuple(reversed(lexical_tuple)),
        vector_input=vector_input,
    )
    hybrid_replay = hybrid_service.assemble(
        hybrid_request,
        lexical_inputs=lexical_tuple,
        vector_input=vector_input,
    )
    bundle = hybrid_outcome.bundle
    if (
        bundle is None
        or hybrid_replay.bundle is None
        or bundle.bundle_hash != hybrid_replay.bundle.bundle_hash
        or expected_provision_hash
        not in {value.identity.content_sha256 for value in bundle.ordered_items}
        or {value.identity.source_id for value in bundle.ordered_items}
        != {REAL_SOURCE_ID}
        or any(
            value.structured_metadata.get("effective_from")
            != temporal_projection_receipt.effective_from_date
            or value.structured_metadata.get(
                "step38_temporal_projection_receipt_hash"
            )
            != temporal_projection_receipt.receipt_hash
            or value.structured_metadata.get(
                "step38_temporal_projection_model_inference_used"
            )
            is not False
            or value.structured_metadata.get(
                "step38_temporal_projection_canonical_evidence_authority"
            )
            is not False
            for value in bundle.ordered_items
        )
    ):
        raise Step38RealRetrievalError("STEP20_REAL_HYBRID_REPLAY_FAILED")
    verify_evidence_bundle_hash(bundle)

    attestation = Step38RealRetrievalAttestation(
        attestation_version=ATTESTATION_VERSION,
        adapter_version=ADAPTER_VERSION,
        input_hash=retrieval_input.input_hash,
        runtime_instance_digest=runtime_instance_digest,
        database_instance_digest=database_instance_digest,
        step14_manifest_digest=step18.EXPECTED_STEP14_DIGEST,
        step15_manifest_digest=step18.EXPECTED_STEP15_DIGEST,
        step16_manifest_digest=step18.EXPECTED_STEP16_DIGEST,
        source_id=REAL_SOURCE_ID,
        official_identifier=REAL_OFFICIAL_IDENTIFIER,
        version_identity=REAL_VERSION_IDENTITY,
        provision_hashes=tuple(REAL_PROVISION_HASHES.items()),
        corpus_bytes_class=CORPUS_BYTES_CLASS,
        temporal_projection_class=TEMPORAL_PROJECTION_CLASS,
        temporal_projection_receipt_hash=(
            temporal_projection_receipt.receipt_hash
        ),
        retrieval_input_kind=(
            "PRIMARY"
            if isinstance(retrieval_input, Step38GermanLawRetrievalInput)
            else "RELATED"
        ),
        lexical_result_hashes=tuple(
            result.result_hash for _request, result in lexical_tuple
        ),
        embedding_record_hashes=tuple(
            value.record_hash for value in generated.records
        ),
        embedding_result_hash=generated.result_hash,
        embedding_backend_identity_digest=canonical_sha256(backend_identity),
        embedding_verified_files_digest=canonical_sha256(verified_files),
        approved_local_e5_backend=approved_local_e5,
        vector_result_hash=vector_result.result_hash,
        hybrid_outcome_hash=hybrid_outcome.outcome_hash,
        evidence_bundle_hash=bundle.bundle_hash,
        data_plane_credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        data_plane_session_user=session_user,
        cross_tenant_rls_visible_count=cross_tenant_visible,
        negative_source_leak_count=negative_leaks,
        same_database=True,
        provider_calls=0,
        aws_calls=0,
        s3_mutations=0,
        migration_calls=0,
        runtime_lifecycle_calls=0,
    )
    return Step38RealRetrievalArtifacts(
        retrieval_input=retrieval_input,
        lexical_inputs=lexical_tuple,
        embedding_request=generation_request,
        embedding_result=generated,
        embedding_replay=generated_replay,
        vector_request=vector_request,
        vector_result=vector_result,
        vector_replay=vector_replay,
        hybrid_request=hybrid_request,
        hybrid_outcome=hybrid_outcome,
        hybrid_replay=hybrid_replay,
        temporal_projection_receipt=temporal_projection_receipt,
        embedding_backend_identity=backend_identity,
        embedding_verified_files=verified_files,
        approved_local_e5_backend=approved_local_e5,
        attestation=attestation,
    )


def run_step38_real_retrieval_on_owned_database(
    retrieval_input: Step38GermanLawRetrievalInput,
    *,
    root: CallerOwnedSqlClient,
    database: str,
    database_runner: SerializableTransactionRunner,
    data_plane_session_user: str,
    runtime_instance_digest: str,
    database_instance_digest: str,
    corpus_roots: Step38CorpusRoots,
    embedding_backend: EmbeddingBackend,
    embedding_cache: PassageEmbeddingCache,
) -> Step38RealRetrievalArtifacts:
    """Seed once, then run the primary query through app-role data plane."""

    if not isinstance(retrieval_input, Step38GermanLawRetrievalInput):
        raise TypeError("retrieval_input must be Step38GermanLawRetrievalInput")
    return _run_sanitized_real_phase(
        "PRIMARY_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
        lambda: _run_step38_retrieval_on_owned_database(
            retrieval_input,
            root=root,
            database=database,
            database_runner=database_runner,
            data_plane_session_user=data_plane_session_user,
            runtime_instance_digest=runtime_instance_digest,
            database_instance_digest=database_instance_digest,
            corpus_roots=corpus_roots,
            embedding_backend=embedding_backend,
            embedding_cache=embedding_cache,
            seed_fixture=True,
        ),
    )


def run_step38_restored_primary_retrieval_on_owned_database(
    retrieval_input: Step38GermanLawRetrievalInput,
    *,
    root: CallerOwnedSqlClient,
    database: str,
    database_runner: SerializableTransactionRunner,
    data_plane_session_user: str,
    runtime_instance_digest: str,
    database_instance_digest: str,
    corpus_roots: Step38CorpusRoots,
    embedding_backend: EmbeddingBackend,
    embedding_cache: PassageEmbeddingCache,
) -> Step38RealRetrievalArtifacts:
    """Replay the primary query against a Step42-restored seeded database.

    This boundary performs no fixture seeding.  It exists only for an isolated
    restore proof where the authoritative source rows and derived retrieval
    records must already have arrived through the native database backup.
    """

    if (
        not isinstance(retrieval_input, Step38GermanLawRetrievalInput)
        or retrieval_input.golden_case.case_kind is not GoldenCaseKind.PRIMARY
    ):
        raise TypeError("retrieval_input must be the typed Step 38 primary input")
    return _run_sanitized_real_phase(
        "PRIMARY_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
        lambda: _run_step38_retrieval_on_owned_database(
            retrieval_input,
            root=root,
            database=database,
            database_runner=database_runner,
            data_plane_session_user=data_plane_session_user,
            runtime_instance_digest=runtime_instance_digest,
            database_instance_digest=database_instance_digest,
            corpus_roots=corpus_roots,
            embedding_backend=embedding_backend,
            embedding_cache=embedding_cache,
            seed_fixture=False,
            allow_restored_primary=True,
        ),
    )


def run_step38_related_retrieval_on_owned_database(
    retrieval_input: Step38RelatedGermanLawRetrievalInput,
    *,
    root: CallerOwnedSqlClient,
    database: str,
    database_runner: SerializableTransactionRunner,
    data_plane_session_user: str,
    runtime_instance_digest: str,
    database_instance_digest: str,
    corpus_roots: Step38CorpusRoots,
    embedding_backend: EmbeddingBackend,
    embedding_cache: PassageEmbeddingCache,
) -> Step38RealRetrievalArtifacts:
    """Reuse the exact seeded DB for a distinct later Step 17-20 query."""

    if not isinstance(retrieval_input, Step38RelatedGermanLawRetrievalInput):
        raise TypeError(
            "retrieval_input must be Step38RelatedGermanLawRetrievalInput"
        )
    return _run_sanitized_real_phase(
        "RELATED_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
        lambda: _run_step38_retrieval_on_owned_database(
            retrieval_input,
            root=root,
            database=database,
            database_runner=database_runner,
            data_plane_session_user=data_plane_session_user,
            runtime_instance_digest=runtime_instance_digest,
            database_instance_digest=database_instance_digest,
            corpus_roots=corpus_roots,
            embedding_backend=embedding_backend,
            embedding_cache=embedding_cache,
            seed_fixture=False,
        ),
    )


def run_step38_backup_retrieval_on_owned_database(
    retrieval_input: Step38GermanLawRetrievalInput,
    *,
    root: CallerOwnedSqlClient,
    database: str,
    database_runner: SerializableTransactionRunner,
    data_plane_session_user: str,
    runtime_instance_digest: str,
    database_instance_digest: str,
    corpus_roots: Step38CorpusRoots,
    embedding_backend: EmbeddingBackend,
    embedding_cache: PassageEmbeddingCache,
) -> Step38RealRetrievalArtifacts:
    """Run the real backup case in the already seeded primary database."""

    if (
        not isinstance(retrieval_input, Step38GermanLawRetrievalInput)
        or retrieval_input.golden_case.case_kind is not GoldenCaseKind.BACKUP
    ):
        raise TypeError("retrieval_input must be the typed Step 38 backup input")
    return _run_sanitized_real_phase(
        "BACKUP_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
        lambda: _run_step38_retrieval_on_owned_database(
            retrieval_input,
            root=root,
            database=database,
            database_runner=database_runner,
            data_plane_session_user=data_plane_session_user,
            runtime_instance_digest=runtime_instance_digest,
            database_instance_digest=database_instance_digest,
            corpus_roots=corpus_roots,
            embedding_backend=embedding_backend,
            embedding_cache=embedding_cache,
            seed_fixture=False,
        ),
    )


def build_database_retrieval_proof(
    artifacts: Step38RealRetrievalArtifacts,
) -> Step38DatabaseRetrievalProof:
    """Project only reconstructible, secret-free runtime facts for Step 38."""

    if not isinstance(artifacts, Step38RealRetrievalArtifacts):
        raise TypeError("artifacts must be Step38RealRetrievalArtifacts")
    bundle = artifacts.hybrid_outcome.bundle
    if bundle is None:
        raise Step38RealRetrievalError("RETRIEVAL_PROOF_REQUIRES_BUNDLE")
    attestation = artifacts.attestation
    return Step38DatabaseRetrievalProof(
        proof_version=STEP38_DATABASE_RETRIEVAL_PROOF_VERSION,
        retrieval_input_hash=artifacts.retrieval_input.input_hash,
        primary_retrieval_input_hash=(
            artifacts.retrieval_input.primary_input.input_hash
            if isinstance(
                artifacts.retrieval_input,
                Step38RelatedGermanLawRetrievalInput,
            )
            else None
        ),
        retrieval_input_kind=attestation.retrieval_input_kind,
        route_hash=artifacts.retrieval_input.route.route_hash,
        step20_outcome_hash=artifacts.hybrid_outcome.outcome_hash,
        evidence_bundle_hash=bundle.bundle_hash,
        temporal_projection_receipt_hash=(
            artifacts.temporal_projection_receipt.receipt_hash
        ),
        real_retrieval_artifacts_hash=artifacts.artifacts_hash,
        real_retrieval_attestation_hash=attestation.attestation_hash,
        runtime_instance_digest=attestation.runtime_instance_digest,
        database_instance_digest=attestation.database_instance_digest,
        data_plane_credential_purpose=attestation.data_plane_credential_purpose,
        data_plane_session_user=attestation.data_plane_session_user,
        embedding_backend_identity_digest=(
            attestation.embedding_backend_identity_digest
        ),
        embedding_verified_files_digest=(
            attestation.embedding_verified_files_digest
        ),
        approved_local_e5_backend=attestation.approved_local_e5_backend,
        cross_tenant_rls_visible_count=attestation.cross_tenant_rls_visible_count,
        owned_database=attestation.same_database,
        step18_exact_retrieval=True,
        step19_vector_retrieval=True,
        step20_hybrid_assembly=True,
    )


__all__ = [
    "ADAPTER_VERSION",
    "ATTESTATION_VERSION",
    "CORPUS_BYTES_CLASS",
    "TEMPORAL_PROJECTION_CLASS",
    "CallerOwnedSqlClient",
    "Step38CorpusRoots",
    "Step38GermanLawRetrievalInput",
    "Step38RelatedGermanLawRetrievalInput",
    "Step38RealRetrievalArtifacts",
    "Step38RealRetrievalAttestation",
    "Step38RealRetrievalError",
    "build_canonical_primary_retrieval_input",
    "build_canonical_backup_retrieval_input",
    "build_canonical_related_retrieval_input",
    "canonical_later_question",
    "build_database_retrieval_proof",
    "run_step38_real_retrieval_on_owned_database",
    "run_step38_restored_primary_retrieval_on_owned_database",
    "run_step38_backup_retrieval_on_owned_database",
    "run_step38_related_retrieval_on_owned_database",
]
