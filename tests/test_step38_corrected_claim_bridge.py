"""Step 38 corrected-claim bridge into Step 29 evidence binding."""

from __future__ import annotations

import copy
import inspect
import unittest
from dataclasses import replace
from types import SimpleNamespace

from tests.test_step23_claim_evidence_binding import pipeline as step23_pipeline
from tests.test_step25_draft_v2_layered_verifier import (
    FakeProvider,
    FixedClock,
    authentication,
    response_for,
)
from tests.test_step29_personal_memory_patch_proposal import fixture

from aioa_memory_kernel.answers import ClaimVerificationReference
from aioa_memory_kernel.claims import (
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceRelation,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.corrections import build_correction_packet
from aioa_memory_kernel.german_law.e2e import (
    CanonicalEvidenceExactVerifier,
    build_evidence_bound_correction_context,
)
from aioa_memory_kernel.personal_memory import (
    PersonalMemoryPatchProposalService,
    PersonalMemoryPatchValidationError,
    bind_personal_memory_patch_evidence,
    build_personal_memory_patch_evidence_binding,
    parse_personal_memory_patch_state,
    personal_memory_patch_state_to_jsonb,
)
from aioa_memory_kernel.personal_memory.proposals import (
    CORRECTED_CLAIM_EVIDENCE_BRIDGE_KIND,
    CORRECTED_CLAIM_EVIDENCE_REFERENCE_CONTRACT_TYPE,
    PersonalMemoryCorrectedClaimEvidenceReference,
)
from aioa_memory_kernel.verification import (
    CorrectedEvidenceVerdict,
    CorrectedEvidenceVerifierSignal,
    DeterministicFakeSemanticVerifier,
    DraftV2PipelineResult,
    DraftV2LayeredVerifier,
    DraftV2Service,
    EvidenceBindingResult,
    FinalStep25ClaimVerdict,
    Step25ReasonCode,
    verify_corrected_evidence_proofs_against_packet,
)


ACTIVE_BINDING_HASH = "0f6c648249456aeaa43bfd598c9c83e83f4d7874e03962f33fc3cba179c98266"
ACTIVE_REFERENCE_HASH = "e15e8dd354488fe15f455612741b4d18c22273cb007d90b80be0e53694dd9c1b"


def _run_corrected_v2(draft_v1, packet, text, context):
    authenticator, _, request = authentication(draft_v1, packet)
    provider = FakeProvider(request, [response_for(request, text)])
    verifier = DraftV2LayeredVerifier(
        DeterministicFakeSemanticVerifier(),
        corrected_evidence_verifier=CanonicalEvidenceExactVerifier(context),
    )
    return DraftV2Service(
        provider,
        authenticator,
        verifier=verifier,
        clock=FixedClock(),
        sleep=lambda _: None,
    ).generate_and_verify(request)


def _corrected_fixture() -> SimpleNamespace:
    base = fixture()
    outcome, temporal, draft_v1, _, snapshot = step23_pipeline(
        "Das Gesetz gilt.",
        contents=("Das Gesetz gilt nicht.",),
    )
    packet = build_correction_packet(snapshot)
    context = build_evidence_bound_correction_context(
        snapshot,
        (outcome.bundle,),
        packet,
    )
    source_citation = next(
        item
        for item in packet.ordered_citations
        if item.relation is ClaimEvidenceRelation.REFUTES
    )
    target_citation = source_citation
    draft_v2_text = (
        "Das Gesetz gilt nicht "
        f"[citation:{source_citation.citation_id}]."
    )
    step25_result = _run_corrected_v2(
        draft_v1,
        packet,
        draft_v2_text,
        context,
    )
    target_claim = step25_result.ordered_claims[0]
    target_verification = step25_result.ordered_claim_verifications[0]
    answer_reference = ClaimVerificationReference(
        claim_id=target_claim.claim_id,
        claim_hash=target_claim.claim_hash,
        verification_hash=target_verification.verification_hash,
        final_verdict=target_verification.final_step25_verdict,
    )
    answer = replace(
        base.answer,
        evidence_bundle_hash=outcome.bundle.bundle_hash,
        temporal_resolution_hash=temporal.result_hash,
        correction_packet_hash=packet.packet_hash,
        packet_integrity_receipt_hash=(
            step25_result.draft_v2.packet_integrity_receipt_hash
        ),
        draft_v2_hash=step25_result.draft_v2.draft_v2_hash,
        verification_summary_hash=(
            step25_result.verification_summary.summary_hash
        ),
        answer_text=step25_result.draft_v2.draft_text,
        ordered_citations=(target_citation,),
        claim_verification_references=(answer_reference,),
    )
    target_link = next(
        item
        for item in snapshot.ordered_evidence_links
        if item.link_hash == target_citation.evidence_link_hash
    )
    proposal = replace(
        base.proposed.proposal,
        candidate_claim_ids=(source_citation.claim_id,),
        candidate_evidence_reference_hashes=(target_link.link_hash,),
        proposal_statement=target_claim.exact_claim_text,
        source_result_hash=answer.answer_hash,
        draft_v1_hash=draft_v1.draft_hash,
        draft_v2_hash=step25_result.draft_v2.draft_v2_hash,
        correction_packet_hash=packet.packet_hash,
        verification_summary_hash=(
            step25_result.verification_summary.summary_hash
        ),
        verified_answer_hash=answer.answer_hash,
    )
    state = replace(base.proposed, proposal=proposal)
    source_assessment = next(
        item
        for item in snapshot.ordered_candidate_assessments
        if item.claim_id == source_citation.claim_id
    )
    return SimpleNamespace(
        base=base,
        outcome=outcome,
        temporal=temporal,
        draft_v1=draft_v1,
        snapshot=snapshot,
        packet=packet,
        evidence_context=context,
        source_citation=source_citation,
        source_assessment=source_assessment,
        target_citation=target_citation,
        target_link=target_link,
        target_claim=target_claim,
        target_verification=target_verification,
        step25_result=step25_result,
        answer=answer,
        answer_reference=answer_reference,
        proposal=proposal,
        state=state,
    )


def _changed(value: SimpleNamespace, **overrides) -> SimpleNamespace:
    fields = vars(value).copy()
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _build(value: SimpleNamespace, **overrides):
    state = overrides.pop("state", value.state)
    arguments = {
        "bundles": (value.outcome.bundle,),
        "temporal_result": value.temporal,
        "claim_links": (value.target_link,),
        "claim_assessments": value.snapshot.ordered_candidate_assessments,
        "correction_packet": value.packet,
        "verified_answer": value.answer,
        "step25_result": value.step25_result,
        "evidence_context": value.evidence_context,
        "bound_at": value.base.binding.bound_at,
    }
    arguments.update(overrides)
    return build_personal_memory_patch_evidence_binding(state, **arguments)


def _with_target_verification(
    value: SimpleNamespace,
    verification,
) -> SimpleNamespace:
    summary = replace(
        value.step25_result.verification_summary,
        ordered_claim_verification_hashes=(verification.verification_hash,),
    )
    result = DraftV2PipelineResult(
        draft_v2=value.step25_result.draft_v2,
        generation_result=value.step25_result.generation_result,
        ordered_claims=value.step25_result.ordered_claims,
        ordered_claim_verifications=(verification,),
        verification_summary=summary,
        replayed=value.step25_result.replayed,
        persisted=value.step25_result.persisted,
    )
    answer_reference = replace(
        value.answer_reference,
        verification_hash=verification.verification_hash,
    )
    answer = replace(
        value.answer,
        verification_summary_hash=summary.summary_hash,
        claim_verification_references=(answer_reference,),
    )
    proposal = replace(
        value.proposal,
        verification_summary_hash=summary.summary_hash,
        verified_answer_hash=answer.answer_hash,
        source_result_hash=answer.answer_hash,
    )
    return _changed(
        value,
        target_verification=verification,
        step25_result=result,
        answer_reference=answer_reference,
        answer=answer,
        proposal=proposal,
        state=replace(value.state, proposal=proposal),
    )


def _remove_corrected_evidence_proof(value: SimpleNamespace) -> SimpleNamespace:
    verification = replace(
        value.target_verification,
        corrected_evidence_signal_hash=None,
        corrected_evidence_proof=None,
        corrected_evidence_proof_hash=None,
        reason_codes=tuple(
            code
            for code in value.target_verification.reason_codes
            if code is not Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS
        ),
    )
    return _with_target_verification(value, verification)


class CorrectedClaimBridgeTests(unittest.TestCase):
    def test_genuine_refuted_draft_v1_to_verified_supported_draft_v2_roundtrip(self):
        value = _corrected_fixture()
        self.assertIs(
            value.source_assessment.candidate_status,
            ClaimEvidenceCandidateStatus.REFUTED,
        )
        self.assertIs(
            value.target_verification.evidence_binding_result,
            EvidenceBindingResult.SUPPORTED,
        )
        self.assertIs(
            value.target_verification.final_step25_verdict,
            FinalStep25ClaimVerdict.VERIFIED_SUPPORTED,
        )
        self.assertIsNotNone(
            value.target_verification.corrected_evidence_signal_hash
        )
        self.assertIsNotNone(
            value.target_verification.corrected_evidence_proof_hash
        )
        self.assertIsNotNone(value.target_verification.corrected_evidence_proof)
        self.assertIn(
            Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS,
            value.target_verification.reason_codes,
        )
        self.assertEqual(
            value.proposal.candidate_claim_ids,
            (value.source_citation.claim_id,),
        )
        self.assertNotIn(
            value.target_claim.claim_id,
            value.proposal.candidate_claim_ids,
        )

        binding = _build(value)
        reference = binding.ordered_evidence_references[0]
        self.assertIsInstance(reference, PersonalMemoryCorrectedClaimEvidenceReference)
        self.assertEqual(reference.bridge_kind, CORRECTED_CLAIM_EVIDENCE_BRIDGE_KIND)
        self.assertEqual(reference.source_claim_id, value.source_citation.claim_id)
        self.assertIs(reference.source_relation, ClaimEvidenceRelation.REFUTES)
        self.assertEqual(
            reference.source_evidence_link_hash,
            value.source_citation.evidence_link_hash,
        )
        self.assertEqual(
            reference.target_draft_v2_claim_id,
            value.target_claim.claim_id,
        )
        self.assertEqual(
            reference.source_claim_id,
            value.target_link.claim_id,
        )
        self.assertEqual(
            reference.source_evidence_link_hash,
            reference.evidence_link_hash,
        )
        self.assertEqual(reference.source_citation_id, reference.target_citation_id)
        self.assertEqual(reference.source_citation_hash, reference.target_citation_hash)
        self.assertEqual(
            reference.corrected_evidence_signal_hash,
            value.target_verification.corrected_evidence_signal_hash,
        )
        self.assertEqual(
            reference.corrected_evidence_proof_hash,
            value.target_verification.corrected_evidence_proof_hash,
        )
        proof = value.target_verification.corrected_evidence_proof
        assert proof is not None
        self.assertEqual(reference.evidence_context_hash, proof.evidence_context_hash)
        self.assertEqual(
            reference.evidence_context_hash,
            value.evidence_context.context_hash,
        )
        self.assertEqual(
            reference.evidence_span_text_sha256,
            proof.evidence_span_text_sha256,
        )
        self.assertEqual(proof.original_evidence_link, value.target_link)

        bound = bind_personal_memory_patch_evidence(
            value.state,
            binding,
            transitioned_at=value.base.binding.bound_at,
        )
        encoded = personal_memory_patch_state_to_jsonb(bound)
        self.assertEqual(parse_personal_memory_patch_state(encoded), bound)
        self.assertEqual(
            encoded["evidence_binding"]["ordered_evidence_references"][0][
                "contract_type"
            ],
            CORRECTED_CLAIM_EVIDENCE_REFERENCE_CONTRACT_TYPE,
        )

    def test_active_provider_hash_json_and_service_defaults_are_stable(self):
        value = fixture()
        self.assertEqual(value.binding.binding_hash, ACTIVE_BINDING_HASH)
        self.assertEqual(
            value.binding.ordered_evidence_references[0].reference_hash,
            ACTIVE_REFERENCE_HASH,
        )
        encoded = personal_memory_patch_state_to_jsonb(value.awaiting)
        legacy_reference = encoded["evidence_binding"][
            "ordered_evidence_references"
        ][0]
        self.assertNotIn("contract_type", legacy_reference)
        self.assertEqual(parse_personal_memory_patch_state(encoded), value.awaiting)
        with self.assertRaises(ContractValidationError):
            replace(
                value.binding.ordered_evidence_references[0],
                relation=ClaimEvidenceRelation.REFUTES,
            )
        for method in (
            PersonalMemoryPatchProposalService.bind_evidence,
            PersonalMemoryPatchProposalService.validate_proposal,
        ):
            for parameter_name in ("step25_result", "evidence_context"):
                parameter = inspect.signature(method).parameters[parameter_name]
                self.assertIsNone(parameter.default)

    def test_direct_constructor_and_parser_reject_false_semantics(self):
        value = _corrected_fixture()
        binding = _build(value)
        reference = binding.ordered_evidence_references[0]
        for changes in (
            {"source_relation": ClaimEvidenceRelation.SUPPORTS},
            {"target_verdict": FinalStep25ClaimVerdict.VERIFIED_REFUTED},
            {"corrected_evidence_signal_hash": None},
            {"corrected_evidence_proof_hash": None},
            {"evidence_context_hash": None},
            {"evidence_span_text_sha256": None},
            {"source_evidence_link_hash": "f" * 64},
            {"target_citation_id": "citation-other"},
            {"contract_type": "UnknownCorrectedClaimReference"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ContractValidationError):
                    replace(reference, **changes)

        bound = bind_personal_memory_patch_evidence(
            value.state,
            binding,
            transitioned_at=value.base.binding.bound_at,
        )
        encoded = personal_memory_patch_state_to_jsonb(bound)
        location = encoded["evidence_binding"]["ordered_evidence_references"][0]

        unknown = copy.deepcopy(encoded)
        unknown["evidence_binding"]["ordered_evidence_references"][0][
            "contract_type"
        ] = "UnknownCorrectedClaimReference"
        with self.assertRaises(ContractValidationError):
            parse_personal_memory_patch_state(unknown)

        tampered = copy.deepcopy(encoded)
        tampered["evidence_binding"]["ordered_evidence_references"][0][
            "source_claim_id"
        ] = "claim-tampered"
        with self.assertRaises((IntegrityError, ContractValidationError)):
            parse_personal_memory_patch_state(tampered)

        wrong_verdict = copy.deepcopy(encoded)
        wrong_verdict["evidence_binding"]["ordered_evidence_references"][0][
            "target_verdict"
        ] = FinalStep25ClaimVerdict.VERIFIED_REFUTED.value
        with self.assertRaises(ContractValidationError):
            parse_personal_memory_patch_state(wrong_verdict)
        self.assertEqual(location["reference_hash"], reference.reference_hash)

    def test_pipeline_tamper_cross_owner_and_false_target_support_are_denied(self):
        value = _corrected_fixture()
        tampered_result = replace(value.step25_result)
        object.__setattr__(tampered_result, "result_hash", "0" * 64)
        with self.assertRaises(PersonalMemoryPatchValidationError):
            _build(value, step25_result=tampered_result)

        cross_owner_answer = replace(value.answer, user_id="other-owner")
        cross_owner_proposal = replace(
            value.proposal,
            verified_answer_hash=cross_owner_answer.answer_hash,
            source_result_hash=cross_owner_answer.answer_hash,
        )
        cross_owner = _changed(
            value,
            answer=cross_owner_answer,
            proposal=cross_owner_proposal,
            state=replace(value.state, proposal=cross_owner_proposal),
        )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            _build(cross_owner)

        false_support = _remove_corrected_evidence_proof(value)
        with self.assertRaises(IntegrityError):
            verify_corrected_evidence_proofs_against_packet(
                false_support.step25_result,
                false_support.packet,
            )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            _build(false_support)

        wrong_candidate = replace(
            value.proposal,
            candidate_claim_ids=(value.target_claim.claim_id,),
        )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            _build(value, state=replace(value.state, proposal=wrong_candidate))

    def test_rehashed_proof_with_altered_original_link_is_denied(self):
        value = _corrected_fixture()
        proof = value.target_verification.corrected_evidence_proof
        assert proof is not None
        altered_link = replace(
            proof.original_evidence_link,
            temporal_assessment_hash="f" * 64,
        )
        altered_proof = replace(proof, original_evidence_link=altered_link)
        altered_signal = CorrectedEvidenceVerifierSignal(
            request_hash=altered_proof.request_hash,
            verdict=CorrectedEvidenceVerdict.SUPPORTS,
            evidence_reference_ids=(altered_proof.packet_citation_id,),
            proof=altered_proof,
        )
        altered_verification = replace(
            value.target_verification,
            corrected_evidence_signal_hash=altered_signal.signal_hash,
            corrected_evidence_proof=altered_proof,
            corrected_evidence_proof_hash=altered_proof.proof_hash,
        )
        forged = _with_target_verification(value, altered_verification)
        with self.assertRaises(PersonalMemoryPatchValidationError):
            _build(forged)

    def test_rehashed_forged_context_hash_is_denied_before_step29_persistence(self):
        value = _corrected_fixture()
        proof = value.target_verification.corrected_evidence_proof
        assert proof is not None
        forged_proof = replace(proof, evidence_context_hash="f" * 64)
        forged_signal = CorrectedEvidenceVerifierSignal(
            request_hash=forged_proof.request_hash,
            verdict=CorrectedEvidenceVerdict.SUPPORTS,
            evidence_reference_ids=(forged_proof.packet_citation_id,),
            proof=forged_proof,
        )
        forged_verification = replace(
            value.target_verification,
            corrected_evidence_signal_hash=forged_signal.signal_hash,
            corrected_evidence_proof=forged_proof,
            corrected_evidence_proof_hash=forged_proof.proof_hash,
        )
        forged = _with_target_verification(value, forged_verification)

        with self.assertRaises(PersonalMemoryPatchValidationError):
            _build(forged)


if __name__ == "__main__":
    unittest.main()
