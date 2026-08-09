"""Step 18 exact/full-text retrieval contracts and hard-boundary tests."""

from __future__ import annotations

import ast
import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from aioa_memory_kernel.contracts.enums import (
    KnowledgeRoute,
    MemoryTargetScope,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.retrieval import (
    CockroachRetrievalRepository,
    ExactIdentifierField,
    ExactIdentifierSelector,
    FullTextQuery,
    KeywordQuery,
    RetrievalBoundaryError,
    RetrievalCandidate,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    RetrievalService,
    StatuteSectionSelector,
    Step18ReasonCode,
    verify_candidate_hash,
    verify_result_hash,
)
from aioa_memory_kernel.routing import KnowledgeRouteResult, Step17ReasonCode
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)


ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_ROOT = ROOT / "src/aioa_memory_kernel/retrieval"
DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
DIGEST_C = "3" * 64
CONTENT = "Auf Grund des Artikels gilt diese Vorschrift für den Bundespräsidenten."
CONTENT_HASH = hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()


def scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension("legal_jurisdiction", "DE_FEDERAL", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
        ScopeDimension("legal_source_class", ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "policy", True),
        ScopeDimension("source_language", "de", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
    )


def route(kind: KnowledgeRoute = KnowledgeRoute.HAT_ASSIST) -> KnowledgeRouteResult:
    selected = kind in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}
    reason = {
        KnowledgeRoute.HAT_ASSIST: Step17ReasonCode.SINGLE_ASSISTING_HAT,
        KnowledgeRoute.HAT_ENFORCE: Step17ReasonCode.MANDATORY_HAT_POLICY,
        KnowledgeRoute.PASS_THROUGH: Step17ReasonCode.NO_ELIGIBLE_HAT,
        KnowledgeRoute.AMBIGUOUS: Step17ReasonCode.MULTIPLE_HAT_CONFLICT,
    }[kind]
    return KnowledgeRouteResult(
        request_id="request-step18-1",
        tenant_id="tenant-step18",
        user_id="user-step18",
        routing_input_hash=DIGEST_A,
        registry_snapshot_hash=DIGEST_B,
        knowledge_route=kind,
        selected_hat_id="german-law" if selected else None,
        selected_hat_version="1.0.0" if selected else None,
        selected_manifest_digest=DIGEST_C if selected else None,
        effective_scope=scope(),
        eligible_candidate_hashes=(DIGEST_A,) if selected else (),
        reason_codes=(reason,),
    )


def private_route() -> KnowledgeRouteResult:
    value = route()
    private_scope = tuple(
        sorted(
            (
                *value.effective_scope,
                ScopeDimension(
                    "personal_memory_space_id",
                    "space-1",
                    ScopeValueType.STRING,
                    ScopeComparisonMode.EXACT,
                    "policy",
                    True,
                ),
                ScopeDimension(
                    "target_scope",
                    MemoryTargetScope.USER_PERSONAL_HAT.value,
                    ScopeValueType.STRING,
                    ScopeComparisonMode.EXACT,
                    "policy",
                    True,
                ),
            ),
            key=lambda item: item.name,
        )
    )
    object.__setattr__(value, "effective_scope", private_scope)
    object.__setattr__(
        value,
        "route_hash",
        canonical_sha256(value, exclude_fields=("route_hash",)),
    )
    return value


def exact_selector(field: ExactIdentifierField = ExactIdentifierField.SOURCE_ID, value: str = "source-1") -> ExactIdentifierSelector:
    return ExactIdentifierSelector(field, (value,))


def request(
    *,
    route_value: KnowledgeRouteResult | None = None,
    mode: RetrievalMode = RetrievalMode.EXACT_IDENTIFIER,
    selector: object | None = None,
    maximum_results: int = 20,
    **overrides: object,
) -> RetrievalRequest:
    selected_route = route_value or route()
    selected_selector = selector or exact_selector()
    values = {
        "route": selected_route,
        "tenant_id": selected_route.tenant_id,
        "user_id": selected_route.user_id,
        "request_id": selected_route.request_id,
        "route_hash": selected_route.route_hash,
        "selected_hat_id": selected_route.selected_hat_id,
        "selected_hat_version": selected_route.selected_hat_version,
        "selected_manifest_digest": selected_route.selected_manifest_digest,
        "effective_scope": selected_route.effective_scope,
        "hat_scope_id": "german-law-global-1a" if selected_route.selected_hat_id else None,
        "retrieval_mode": mode,
        "selector": selected_selector,
        "maximum_results": maximum_results,
    }
    values.update(overrides)
    return RetrievalRequest(**values)  # type: ignore[arg-type]


def row(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-step18",
        "hat_scope_id": "german-law-global-1a",
        "source_id": "source-1",
        "knowledge_version_id": "version-1",
        "chunk_id": "chunk-1",
        "chunk_ordinal": "0",
        "content_sha256": CONTENT_HASH,
        "content_text": CONTENT,
        "language_tag": "de",
        "authority_level": "AUTHORITATIVE_SECONDARY",
        "authority_basis": json.dumps({"official_identifier": "BJNR1330A0023"}),
        "source_kind": "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
        "source_reference": "gii:BJNR1330A0023",
        "publication_state": "PUBLISHED",
        "access_class": "PUBLIC",
        "target_scope": "SHARED_KNOWLEDGE_HAT",
        "owner_user_id": None,
        "personal_memory_space_id": None,
        "scope_digest": DIGEST_A,
        "registry_digest": DIGEST_B,
        "artifact_digest": DIGEST_C,
        "snapshot_id": "snapshot-1",
        "metadata": json.dumps({
            "document_identity": "document-1",
            "official_identifier": "BJNR1330A0023",
            "provision_identifier": "I.",
            "version_identity": "legal-version-1",
        }),
        "version_ordinal": "1",
        "is_current": "true",
        "snapshot_content_sha256": DIGEST_A,
        "scope_dimensions": json.dumps({"jurisdiction": "DE_FEDERAL", "language": "de"}),
    }
    value.update(overrides)
    return value


class FakeTransaction:
    def __init__(self, rows: tuple[dict[str, object], ...] = ()) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    @property
    def active(self) -> bool:
        return True

    def fetch_all(self, sql: str, parameters: object = None) -> tuple[dict[str, object], ...]:
        self.calls.append((sql, tuple(parameters or ())))
        return self.rows


def repository_search(value: RetrievalRequest, *rows: dict[str, object]) -> tuple[tuple[RetrievalCandidate, ...], FakeTransaction]:
    transaction = FakeTransaction(tuple(rows))
    candidates = CockroachRetrievalRepository().search(transaction, value)
    return candidates, transaction


def service_with(rows: tuple[RetrievalCandidate, ...]) -> RetrievalService:
    runner = SerializableTransactionRunner(lambda: None)  # run() is patched in tests
    service = RetrievalService(runner)
    patcher = patch.object(runner, "run", return_value=rows)
    patcher.start()
    service._test_patcher = patcher  # type: ignore[attr-defined]
    return service


class RouteBindingTests(unittest.TestCase):
    def test_valid_hat_assist_route_is_accepted(self) -> None:
        self.assertEqual(request().route.knowledge_route, KnowledgeRoute.HAT_ASSIST)

    def test_valid_hat_enforce_route_is_accepted(self) -> None:
        self.assertEqual(request(route_value=route(KnowledgeRoute.HAT_ENFORCE)).route.knowledge_route, KnowledgeRoute.HAT_ENFORCE)

    def test_pass_through_produces_no_hat_retrieval(self) -> None:
        value = request(route_value=route(KnowledgeRoute.PASS_THROUGH))
        service = service_with(())
        result = service.retrieve(value)
        self.assertEqual(result.reason_codes, (Step18ReasonCode.NO_HAT_SELECTED,))
        service._runner.run.assert_not_called()  # type: ignore[attr-defined]
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_ambiguous_route_is_denied(self) -> None:
        value = request(route_value=route(KnowledgeRoute.AMBIGUOUS))
        service = service_with(())
        with self.assertRaisesRegex(RetrievalBoundaryError, "AMBIGUOUS_ROUTE"):
            service.retrieve(value)
        service._runner.run.assert_not_called()  # type: ignore[attr-defined]
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_hat_enforce_does_not_change_retrieval_capability(self) -> None:
        value = request(route_value=route(KnowledgeRoute.HAT_ENFORCE))
        service = service_with(())
        result = service.retrieve(value)
        self.assertEqual(result.reason_codes, (Step18ReasonCode.NO_MATCH,))
        service._runner.run.assert_called_once()  # type: ignore[attr-defined]
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_stale_route_hash_is_denied(self) -> None:
        value = route()
        object.__setattr__(value, "route_hash", "0" * 64)
        with self.assertRaisesRegex(RetrievalBoundaryError, "ROUTE_HASH_INVALID"):
            request(route_value=value)

    def test_selected_hat_id_mismatch_is_denied(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "HAT_IDENTITY_MISMATCH"):
            request(selected_hat_id="other-hat")

    def test_selected_hat_version_mismatch_is_denied(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "HAT_IDENTITY_MISMATCH"):
            request(selected_hat_version="2.0.0")

    def test_manifest_digest_mismatch_is_denied(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "HAT_IDENTITY_MISMATCH"):
            request(selected_manifest_digest=DIGEST_A)

    def test_tenant_mismatch_is_denied(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "TENANT_MISMATCH"):
            request(tenant_id="tenant-other")

    def test_user_mismatch_is_denied(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "USER_MISMATCH"):
            request(user_id="user-other")

    def test_requested_scope_widening_is_denied(self) -> None:
        widened = scope() + (ScopeDimension("unknown", "wide", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "caller", True),)
        with self.assertRaisesRegex(RetrievalBoundaryError, "ROUTE_SCOPE_MISMATCH"):
            request(effective_scope=widened)

    def test_candidate_scope_must_remain_route_scope(self) -> None:
        candidate, _ = repository_search(request(), row())
        candidate = (replace(candidate[0], effective_scope=()),)
        service = service_with(candidate)
        with self.assertRaisesRegex(RetrievalBoundaryError, "ROUTE_SCOPE_MISMATCH"):
            service.retrieve(request())
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_personal_space_cannot_be_added_after_route_decision(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "ROUTE_SCOPE_MISMATCH"):
            request(personal_memory_space_id="space-1")


class QueryModeTests(unittest.TestCase):
    def _assert_exact(self, field: ExactIdentifierField, value: str, sql_token: str) -> None:
        candidates, tx = repository_search(request(selector=exact_selector(field, value)), row())
        self.assertEqual(len(candidates), 1)
        sql, parameters = tx.calls[0]
        self.assertIn(sql_token, sql)
        self.assertIn(value, parameters)
        self.assertNotIn(value, sql)

    def test_exact_source_id_lookup(self) -> None:
        self._assert_exact(ExactIdentifierField.SOURCE_ID, "source-1", "ts.source_id IN")

    def test_exact_version_id_lookup(self) -> None:
        self._assert_exact(ExactIdentifierField.KNOWLEDGE_VERSION_ID, "version-1", "kv.knowledge_version_id IN")

    def test_exact_chunk_id_lookup(self) -> None:
        self._assert_exact(ExactIdentifierField.CHUNK_ID, "chunk-1", "kc.chunk_id IN")

    def test_exact_official_identifier_lookup(self) -> None:
        self._assert_exact(ExactIdentifierField.OFFICIAL_IDENTIFIER, "BJNR1330A0023", "official_identifier")

    def test_exact_document_identity_lookup(self) -> None:
        self._assert_exact(ExactIdentifierField.DOCUMENT_IDENTITY, "document-1", "document_identity")

    def test_exact_version_identity_lookup(self) -> None:
        self._assert_exact(ExactIdentifierField.VERSION_IDENTITY, "legal-version-1", "version_identity")

    def test_wrong_identifier_returns_no_match(self) -> None:
        candidates, _ = repository_search(request(selector=exact_selector(value="absent")))
        self.assertEqual(candidates, ())

    def test_exact_matching_has_no_substring_or_case_fuzzy_fallback(self) -> None:
        _, tx = repository_search(request(selector=exact_selector(value="Source")))
        sql = tx.calls[0][0]
        self.assertNotIn("LIKE", sql.upper())
        self.assertNotIn("LOWER(", sql.upper())

    def test_multiple_exact_versions_remain_separate(self) -> None:
        second = row(knowledge_version_id="version-2", chunk_id="chunk-2", version_ordinal="2")
        candidates, _ = repository_search(request(), row(), second)
        self.assertEqual([c.knowledge_version_id for c in candidates], ["version-1", "version-2"])

    def test_no_temporal_resolution_silently_selects_winner(self) -> None:
        _, tx = repository_search(request(), row())
        self.assertNotIn("is_current = true", tx.calls[0][0].lower())

    def test_known_statute_section_is_structured_and_parameterized(self) -> None:
        selector = StatuteSectionSelector("BJNR1330A0023", "I.")
        candidates, tx = repository_search(request(mode=RetrievalMode.STATUTE_SECTION, selector=selector), row())
        self.assertEqual(len(candidates), 1)
        sql, parameters = tx.calls[0]
        self.assertIn("provision_identifier", sql)
        self.assertNotIn("BJNR1330A0023", sql)
        self.assertIn("BJNR1330A0023", parameters)

    def test_absent_section_is_no_match(self) -> None:
        value = request(mode=RetrievalMode.STATUTE_SECTION, selector=StatuteSectionSelector("BJNR1330A0023", "§ 999"))
        self.assertEqual(repository_search(value)[0], ())

    def test_wrong_statute_cannot_match_same_section_by_body(self) -> None:
        value = request(mode=RetrievalMode.STATUTE_SECTION, selector=StatuteSectionSelector("OTHER", "I."))
        sql = repository_search(value)[1].calls[0][0]
        self.assertIn("official_identifier", sql)
        self.assertNotIn("content_text =", sql)

    def test_malformed_section_identifier_is_rejected(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "INVALID_SECTION_IDENTIFIER"):
            StatuteSectionSelector("BJNR1330A0023", "'; DROP TABLE x; --")

    def test_free_form_body_cannot_override_structured_selector(self) -> None:
        with self.assertRaises(ContractValidationError):
            request(mode=RetrievalMode.STATUTE_SECTION, selector=FullTextQuery("I."))


class LexicalTests(unittest.TestCase):
    def test_german_full_text_uses_pinned_configuration(self) -> None:
        value = request(mode=RetrievalMode.FULL_TEXT, selector=FullTextQuery("Bundespräsidenten Vorschrift"))
        candidates, tx = repository_search(value, row(retrieval_score="0.50000000"))
        self.assertEqual(candidates[0].retrieval_score, "0.5")
        self.assertIn("plainto_tsquery('german', %s)", tx.calls[0][0])
        self.assertIn("search_config = 'german'", tx.calls[0][0])

    def test_german_stemming_is_delegated_to_pinned_config(self) -> None:
        _, tx = repository_search(request(mode=RetrievalMode.FULL_TEXT, selector=FullTextQuery("Vorschriften")))
        self.assertIn("plainto_tsquery('german'", tx.calls[0][0])

    def test_unrelated_text_can_return_no_match(self) -> None:
        value = request(mode=RetrievalMode.FULL_TEXT, selector=FullTextQuery("Kartoffelpuffer"))
        self.assertEqual(repository_search(value)[0], ())

    def test_full_text_order_is_rank_then_stable_identities(self) -> None:
        sql = repository_search(request(mode=RetrievalMode.FULL_TEXT, selector=FullTextQuery("Artikel")))[1].calls[0][0]
        self.assertIn("DESC, kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id", sql)

    def test_result_limit_is_enforced(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "RESULT_LIMIT_EXCEEDED"):
            request(maximum_results=101)

    def test_database_limit_fetches_only_one_truncation_sentinel(self) -> None:
        value = request(
            mode=RetrievalMode.FULL_TEXT,
            selector=FullTextQuery("Artikel"),
            maximum_results=7,
        )
        _, transaction = repository_search(value)
        self.assertEqual(transaction.calls[0][1][-1], 8)

    def test_query_byte_limit_and_empty_query_are_enforced(self) -> None:
        for text in ("", "x" * 4097):
            with self.subTest(size=len(text)), self.assertRaisesRegex(RetrievalBoundaryError, "QUERY_TOO_LARGE"):
                FullTextQuery(text)

    def test_no_websearch_to_tsquery_dependency(self) -> None:
        text = (RETRIEVAL_ROOT / "repository.py").read_text(encoding="utf-8")
        self.assertNotIn("websearch_to_tsquery", text)

    def test_bounded_keyword_query_is_canonical(self) -> None:
        value = KeywordQuery(("Vorschrift", "Artikel"))
        self.assertEqual(value.keywords, ("Artikel", "Vorschrift"))
        request_value = request(mode=RetrievalMode.KEYWORD, selector=value)
        _, tx = repository_search(request_value)
        self.assertIn("Artikel Vorschrift", tx.calls[0][1])

    def test_duplicate_keywords_cannot_inflate_authority(self) -> None:
        with self.assertRaisesRegex(RetrievalBoundaryError, "QUERY_TOO_LARGE"):
            KeywordQuery(("Artikel", "Artikel"))

    def test_keyword_search_is_lexical_without_model_expansion(self) -> None:
        sql = repository_search(request(mode=RetrievalMode.KEYWORD, selector=KeywordQuery(("Artikel",))))[1].calls[0][0]
        self.assertIn("plainto_tsquery", sql)
        self.assertNotIn("model_call", sql.casefold())

    def test_sql_injection_string_is_parameter_data(self) -> None:
        attack = "Artikel'); SELECT pg_sleep(9); --"
        _, tx = repository_search(request(mode=RetrievalMode.FULL_TEXT, selector=FullTextQuery(attack)))
        sql, parameters = tx.calls[0]
        self.assertNotIn(attack, sql)
        self.assertIn(attack, parameters)


class HardFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sql, self.parameters = repository_search(request())[1].calls[0]

    def test_tenant_filter_precedes_candidates_and_every_join_binds_tenant(self) -> None:
        self.assertIn("sre.tenant_id = %s", self.sql)
        self.assertGreaterEqual(self.sql.count("tenant_id ="), 6)

    def test_hat_scope_filter_precedes_candidates_and_every_join_binds_scope(self) -> None:
        self.assertIn("sre.hat_scope_id = %s", self.sql)
        self.assertGreaterEqual(self.sql.count("hat_scope_id ="), 6)

    def test_selected_hat_identity_is_bound_in_trusted_scope(self) -> None:
        self.assertIn("hs.knowledge_hat_id = %s", self.sql)
        self.assertIn("hs.knowledge_hat_version = %s", self.sql)

    def test_selected_manifest_digest_is_bound_before_candidates(self) -> None:
        self.assertIn("hm.manifest_hash = %s", self.sql)
        self.assertIn(DIGEST_C, self.parameters)

    def test_unpublished_quarantined_withdrawn_and_rejected_are_absent(self) -> None:
        self.assertIn("current_publication_state = 'PUBLISHED'", self.sql)
        for state in ("QUARANTINED", "WITHDRAWN", "REJECTED"):
            self.assertNotIn(f"= '{state}'", self.sql)

    def test_source_authority_is_a_hard_filter(self) -> None:
        self.assertIn("authority_level IN ('OFFICIAL_PRIMARY', 'AUTHORITATIVE_SECONDARY')", self.sql)

    def test_shared_access_is_a_hard_filter(self) -> None:
        self.assertIn("access_class IN ('PUBLIC', 'TENANT_RESTRICTED')", self.sql)
        self.assertIn("hs.target_scope = %s", self.sql)
        self.assertIn(MemoryTargetScope.SHARED_KNOWLEDGE_HAT.value, self.parameters)
        self.assertIn("owner_user_id IS NULL", self.sql)
        self.assertIn("personal_memory_space_id IS NULL", self.sql)
        self.assertIn("hs.owner_user_id IS NULL", self.sql)
        self.assertIn("hs.personal_memory_space_id IS NULL", self.sql)

    def test_user_private_access_binds_exact_owner_and_space(self) -> None:
        route_value = private_route()
        private_request = request(
            route_value=route_value,
            effective_scope=route_value.effective_scope,
            personal_memory_space_id="space-1",
        )
        sql, parameters = repository_search(private_request)[1].calls[0]
        self.assertIn("access_class = 'USER_PRIVATE'", sql)
        self.assertIn("owner_user_id = %s", sql)
        self.assertIn("personal_memory_space_id = %s", sql)
        self.assertIn("hs.owner_user_id = %s", sql)
        self.assertIn("hs.personal_memory_space_id = %s", sql)
        self.assertIn(MemoryTargetScope.USER_PERSONAL_HAT.value, parameters)
        self.assertIn("user-step18", parameters)
        self.assertIn("space-1", parameters)

    def test_route_scope_jurisdiction_language_and_source_class_are_hard_filters(self) -> None:
        self.assertIn("scope_dimensions->>'jurisdiction' = %s", self.sql)
        self.assertIn("scope_dimensions->>'language' = %s", self.sql)
        self.assertIn("source_kind IN (%s)", self.sql)

    def test_unknown_optional_scope_dimension_also_fails_closed(self) -> None:
        original = route()
        unsupported = ScopeDimension(
            "unknown_optional",
            "value",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            "policy",
            False,
        )
        object.__setattr__(
            original,
            "effective_scope",
            tuple(sorted((*original.effective_scope, unsupported), key=lambda item: item.name)),
        )
        object.__setattr__(
            original,
            "route_hash",
            canonical_sha256(original, exclude_fields=("route_hash",)),
        )
        value = request(
            route_value=original,
            effective_scope=original.effective_scope,
        )
        with self.assertRaisesRegex(RetrievalBoundaryError, "ROUTE_SCOPE_MISMATCH"):
            repository_search(value)

    def test_correct_public_source_remains_eligible(self) -> None:
        candidates, _ = repository_search(request(), row())
        self.assertEqual(candidates[0].access_class, SourceAccessClass.PUBLIC)

    def test_correct_tenant_restricted_source_remains_eligible(self) -> None:
        candidates, _ = repository_search(
            request(), row(access_class="TENANT_RESTRICTED")
        )
        self.assertEqual(candidates[0].access_class, SourceAccessClass.TENANT_RESTRICTED)

    def test_nonpublished_candidate_is_rejected_by_contract(self) -> None:
        for state in (
            "REGISTERED",
            "REVIEW_REQUIRED",
            "ELIGIBLE",
            "QUARANTINED",
            "WITHDRAWN",
            "REJECTED",
        ):
            with self.subTest(state=state), self.assertRaisesRegex(
                RetrievalBoundaryError, "SOURCE_NOT_PUBLISHED"
            ):
                repository_search(request(), row(publication_state=state))

    def test_unapproved_authority_candidate_is_rejected_by_contract(self) -> None:
        for authority in (
            "INFORMATIONAL_SECONDARY",
            "USER_SUPPLIED",
            "DERIVED",
            "UNKNOWN",
        ):
            with self.subTest(authority=authority), self.assertRaisesRegex(
                RetrievalBoundaryError, "SOURCE_AUTHORITY_REJECTED"
            ):
                repository_search(request(), row(authority_level=authority))

    def test_wrong_tenant_candidate_fails_closed(self) -> None:
        candidates, _ = repository_search(request(), row(tenant_id="tenant-other"))
        service = service_with(candidates)
        with self.assertRaisesRegex(RetrievalBoundaryError, "TENANT_MISMATCH"):
            service.retrieve(request())
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_wrong_hat_candidate_fails_closed(self) -> None:
        candidates, _ = repository_search(request(), row(hat_scope_id="other-hat-scope"))
        service = service_with(candidates)
        with self.assertRaisesRegex(RetrievalBoundaryError, "HAT_SCOPE_MISMATCH"):
            service.retrieve(request())
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_wrong_user_private_candidate_fails_closed(self) -> None:
        route_value = private_route()
        value = request(
            route_value=route_value,
            effective_scope=route_value.effective_scope,
            personal_memory_space_id="space-1",
        )
        private = row(access_class="USER_PRIVATE", target_scope="USER_PERSONAL_HAT", owner_user_id="user-other", personal_memory_space_id="space-1")
        candidates, _ = repository_search(value, private)
        service = service_with(candidates)
        with self.assertRaisesRegex(RetrievalBoundaryError, "OWNER_SCOPE_REJECTED"):
            service.retrieve(value)
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_exact_owner_private_candidate_remains_eligible(self) -> None:
        route_value = private_route()
        value = request(
            route_value=route_value,
            effective_scope=route_value.effective_scope,
            personal_memory_space_id="space-1",
        )
        private = row(
            access_class="USER_PRIVATE",
            target_scope="USER_PERSONAL_HAT",
            owner_user_id="user-step18",
            personal_memory_space_id="space-1",
        )
        candidates, _ = repository_search(value, private)
        service = service_with(candidates)
        result = service.retrieve(value)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.candidates[0].owner_user_id, "user-step18")
        service._test_patcher.stop()  # type: ignore[attr-defined]


class SqlAndIntegrityTests(unittest.TestCase):
    def test_query_values_are_parameterized_and_structure_is_closed(self) -> None:
        sql, parameters = repository_search(request(selector=exact_selector(value="source-secret")))[1].calls[0]
        self.assertNotIn("source-secret", sql)
        self.assertIn("source-secret", parameters)
        with self.assertRaises(ContractValidationError):
            ExactIdentifierSelector("table_name", ("x",))  # type: ignore[arg-type]

    def test_caller_cannot_supply_order_by_table_column_or_tsquery_syntax(self) -> None:
        fields = {item.value for item in ExactIdentifierField}
        self.assertNotIn("TABLE", fields)
        self.assertNotIn("COLUMN", fields)
        self.assertNotIn("ORDER_BY", fields)
        self.assertNotIn("TSQUERY", fields)

    def test_production_repository_has_no_database_mutation_or_ddl(self) -> None:
        tree = ast.parse((RETRIEVAL_ROOT / "repository.py").read_text(encoding="utf-8"))
        string_constants = "\n".join(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str))
        for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "UPSERT", "ALTER TABLE", "CREATE TABLE", "DROP TABLE"):
            self.assertNotIn(token, string_constants.upper())

    def test_candidate_hash_and_order_are_deterministic(self) -> None:
        candidates1, _ = repository_search(request(), row())
        candidates2, _ = repository_search(request(), row())
        self.assertEqual(candidates1[0].candidate_hash, candidates2[0].candidate_hash)
        self.assertEqual(candidates1, candidates2)

    def test_multiline_nfc_content_preserves_exact_bytes(self) -> None:
        content = "§ 1\nDie Straße bleibt öffentlich.\n"
        candidates, _ = repository_search(
            request(),
            row(
                content_text=content,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            ),
        )
        self.assertEqual(candidates[0].content, content)

    def test_content_hash_is_validated(self) -> None:
        with self.assertRaises(ContractValidationError):
            repository_search(request(), row(content_sha256=DIGEST_A))

    def test_registry_digest_is_validated(self) -> None:
        with self.assertRaises(ContractValidationError):
            repository_search(request(), row(registry_digest="bad"))

    def test_candidate_list_is_deeply_immutable(self) -> None:
        candidates, _ = repository_search(request(), row())
        result = RetrievalResult("request-step18-1", "tenant-step18", "user-step18", route().route_hash, "german-law", "1.0.0", DIGEST_C, "german-law-global-1a", scope(), RetrievalMode.EXACT_IDENTIFIER, DIGEST_A, candidates, False, (Step18ReasonCode.RETRIEVAL_OK,))
        self.assertIsInstance(result.candidates, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.truncated = True  # type: ignore[misc]
        with self.assertRaises(TypeError):
            result.candidates[0].structured_metadata["x"] = 1  # type: ignore[index]

    def test_native_float_is_converted_before_canonical_candidate_hash(self) -> None:
        candidates, _ = repository_search(
            request(mode=RetrievalMode.FULL_TEXT, selector=FullTextQuery("Artikel")),
            row(retrieval_score=0.5),
        )
        self.assertEqual(candidates[0].retrieval_score, "0.5")
        self.assertIsInstance(candidates[0].retrieval_score, str)

    def test_tampered_candidate_and_result_fail_verification(self) -> None:
        candidates, _ = repository_search(request(), row())
        object.__setattr__(candidates[0], "chunk_id", "tampered")
        with self.assertRaises(IntegrityError):
            verify_candidate_hash(candidates[0])
        valid, _ = repository_search(request(), row())
        result = RetrievalResult("request-step18-1", "tenant-step18", "user-step18", route().route_hash, "german-law", "1.0.0", DIGEST_C, "german-law-global-1a", scope(), RetrievalMode.EXACT_IDENTIFIER, DIGEST_A, valid, False, (Step18ReasonCode.RETRIEVAL_OK,))
        object.__setattr__(result, "truncated", True)
        with self.assertRaises(IntegrityError):
            verify_result_hash(result)

    def test_service_truncates_at_requested_result_limit(self) -> None:
        first, _ = repository_search(request(), row())
        second, _ = repository_search(
            request(), row(chunk_id="chunk-2", chunk_ordinal="1")
        )
        value = request(maximum_results=1)
        service = service_with(first + second)
        result = service.retrieve(value)
        self.assertEqual(result.candidate_count, 1)
        self.assertTrue(result.truncated)
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_service_canonicalizes_repository_candidate_order(self) -> None:
        first, _ = repository_search(request(), row(chunk_id="chunk-1", chunk_ordinal="0"))
        second, _ = repository_search(request(), row(chunk_id="chunk-2", chunk_ordinal="1"))
        service = service_with(second + first)
        result = service.retrieve(request())
        self.assertEqual(
            tuple(candidate.chunk_id for candidate in result.candidates),
            ("chunk-1", "chunk-2"),
        )
        service._test_patcher.stop()  # type: ignore[attr-defined]

    def test_request_and_result_hashes_are_deterministic(self) -> None:
        first_request = request()
        second_request = request()
        self.assertEqual(first_request.request_hash, second_request.request_hash)
        first_result = RetrievalService._empty(
            first_request, Step18ReasonCode.NO_MATCH
        )
        second_result = RetrievalService._empty(
            second_request, Step18ReasonCode.NO_MATCH
        )
        self.assertEqual(first_result.result_hash, second_result.result_hash)


class StepBoundaryTests(unittest.TestCase):
    def test_no_embedding_vector_hybrid_reranker_or_evidence_bundle_api(self) -> None:
        import aioa_memory_kernel.retrieval as retrieval
        names = set(dir(retrieval))
        for forbidden in ("Embedding", "VectorSearch", "HybridRetriever", "Reranker", "EvidenceBundle"):
            self.assertNotIn(forbidden, names)

    def test_no_model_provider_http_network_aws_or_s3_import(self) -> None:
        forbidden = {"requests", "urllib", "httpx", "socket", "boto3", "subprocess"}
        for path in RETRIEVAL_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {
                node.names[0].name.split(".")[0] if isinstance(node, ast.Import) else node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom)) and (not isinstance(node, ast.ImportFrom) or node.module)
            }
            self.assertTrue(imported.isdisjoint(forbidden), path)

    def test_no_schema_migration_added_for_step18(self) -> None:
        self.assertEqual(tuple((ROOT / "sql").rglob("*step18*")), ())

    def test_existing_schema_and_index_are_reused(self) -> None:
        text = (ROOT / "sql/cockroachdb/migrations/0002_step4_knowledge_lineage_and_retrieval.sql").read_text(encoding="utf-8")
        self.assertIn("chunk_search_documents", text)
        self.assertIn("chunk_search_documents_vector_idx", text)
        self.assertIn("TSVECTOR", text)

    def test_step17_route_contract_is_imported_not_duplicated(self) -> None:
        value = request()
        self.assertIsInstance(value.route, KnowledgeRouteResult)
        self.assertEqual(value.route_hash, value.route.route_hash)

    def test_reason_vocabulary_is_closed_and_exact(self) -> None:
        self.assertEqual(Step18ReasonCode.RETRIEVAL_OK.value, "RETRIEVAL_OK")
        self.assertNotIn("VECTOR_MATCH", {item.value for item in Step18ReasonCode})

    def test_models_preserve_german_unicode_nfc(self) -> None:
        query = FullTextQuery("Straße übermäßig")
        self.assertEqual(query.query_text, "Straße übermäßig")


class DocumentationAndEvidenceTests(unittest.TestCase):
    def test_controlled_validation_evidence_is_hash_bound_and_complete(self) -> None:
        path = ROOT / "docs/evidence/retrieval/step18-exact-fulltext-retrieval-validation.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        claimed = evidence.pop("validation_digest")
        self.assertEqual(canonical_sha256(evidence), claimed)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["real_step16_fixture"]["status"], "PASS")
        for mode in (
            "exact_source",
            "exact_official",
            "statute_section",
            "full_text",
            "keyword",
        ):
            self.assertEqual(evidence["retrieval_matrix"][mode]["status"], "PASS")
        self.assertEqual(set(evidence["hard_filters"].values()), {"PASS"})

    def test_controlled_validation_cleanup_is_exact_and_non_forced(self) -> None:
        path = ROOT / "docs/evidence/retrieval/step18-exact-fulltext-retrieval-validation.json"
        cleanup = json.loads(path.read_text(encoding="utf-8"))["cleanup"]
        self.assertEqual(cleanup["shutdown_method"], "EXACT_OWNED_PID_SIGTERM")
        self.assertTrue(cleanup["graceful_shutdown_requested"])
        self.assertTrue(cleanup["pid_exited"])
        self.assertTrue(cleanup["ports_closed"])
        self.assertTrue(cleanup["temporary_store_removed"])
        self.assertFalse(cleanup["force_kill_used"])

    def test_step18_architecture_adr_operations_and_closure_exist(self) -> None:
        required = {
            "docs/architecture/EXACT_FULL_TEXT_RETRIEVAL_BASELINE_1A.md": (
                "Mandatory Step 17 binding",
                "Hard filtering before candidate generation",
                "Step 19",
                "Step 20",
            ),
            "docs/adr/ADR-025-exact-full-text-retrieval-hard-scope-filtering.md": (
                "KnowledgeRouteResult",
                "Source authority is an eligibility rule",
                "deferred to Step 19",
            ),
            "docs/operations/STEP_18_RETRIEVAL_VALIDATION_1A.md": (
                "run_step18_retrieval_validation.py",
                "force_kill_used=false",
            ),
            "docs/audits/STEP_18_EXACT_FULL_TEXT_RETRIEVAL_CLOSURE_1A.md": (
                "e1895e533c5f97bd06ffa2348cbdc1ee6419e42f",
                "Step 19: NOT STARTED",
                "Step 20: NOT STARTED",
            ),
        }
        for relative, tokens in required.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, relative)

    def test_roadmap_closes_step26_without_starting_step27(self) -> None:
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn(
            "- [x] **Step 18 — Exact and Full-Text Retrieval Baseline 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 19 — Embedding Generation and Vector Retrieval Foundation 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 20 — Hybrid Retrieval, Evidence Bundle and Deterministic Ranking 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 21 — Temporal Resolver, Conflict Detection and Freshness Policy 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 22 — Provider-Neutral Model Adapter and Draft V1 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 23 — Claim Extraction and Evidence Binding 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 24 — Correction Packet Construction and Integrity 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 25 — Draft V2 Generation and Layered Claim Verifier 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 26 — Verified Answer Assembly and Fail-Closed Output 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 27 — Personal Memory HAT Persistence, Quotas and Model Bindings 1A**",
            roadmap,
        )
        self.assertIn(
            "- [ ] **Step 28 — Knowledge Hub and Critic Prompt Loop Correction Candidate Bridge 1A**",
            roadmap,
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Step 18 exact identifiers", agents)
        self.assertIn("Step 19 immutable local-model embeddings", agents)
        self.assertIn("Step 20 verified Step 18/19 input binding", agents)
        self.assertIn("Step 21 verified Step 20 bundle binding", agents)
        self.assertIn("Step 22 provider-neutral original-query-only", agents)
        self.assertIn("Step 23 exact-span deterministic claim extraction", agents)
        self.assertIn("Step 24 verified frozen Step 23 input binding", agents)
        self.assertIn("Step 25 verified Correction Packet integrity gating", agents)
        self.assertIn("Step 26 complete upstream integrity binding", agents)
        self.assertIn("Step 27 owner-private empty Personal Memory HAT slots", agents)
        self.assertIn("Step 28: NOT STARTED", agents)


if __name__ == "__main__":
    unittest.main()
