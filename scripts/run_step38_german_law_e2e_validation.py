#!/usr/bin/env python3
"""Controlled Step 38 German Law end-to-end validation orchestrator.

The default ``live`` mode owns one disposable CockroachDB runtime and database
for real Step 18-20 retrieval plus the Step 27-35 Personal Memory, audit,
review, and UI path.  Approved hosted-model calls occur outside every database
transaction.  ``--offline`` runs deterministic contract and authority proofs
only and is always classified ``PASS_OFFLINE_NOT_CLOSURE``.

A provider outage (including HTTP 429 exhausted by the approved retry policy)
is reported as
``STEP38_REAL_MODEL_VALIDATION_REQUIRED`` and is never replaced with a fake
provider result.

The live runtime accepts the one exact ``OPENROUTER_API_KEY`` credential
source.  It is consumed into a purpose-bound in-memory value before subprocess
work and is passed only through the minimal pinned-runtime re-exec environment.
The secret is never included in stdout, stderr, argv, evidence, or a database
child environment.

Only one canonical, sanitized JSON document is written to stdout.  Progress
records are written to stderr.  No raw model text, source text, credential,
machine path, child stderr, or database connection information is emitted.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

# Some existing Step 35 test utilities import Starlette's compatibility
# TestClient.  Its version warning is unrelated to this evidence and would
# otherwise add non-progress text to stderr.
warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated.*",
)

import run_step18_retrieval_validation as step18  # noqa: E402
from aioa_memory_kernel.answers import (  # noqa: E402
    FinalAnswerRequest,
    FinalOutputStatus,
    VerifiedAnswerService,
)
from aioa_memory_kernel.audit_ledger import verify_audit_chain  # noqa: E402
from aioa_memory_kernel.claims import (  # noqa: E402
    ClaimEvidenceBindingService,
    prepare_claim_binding_request,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.contracts.exceptions import (  # noqa: E402
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.corrections import (  # noqa: E402
    HmacSha256PacketAuthenticator,
    build_correction_packet,
)
from aioa_memory_kernel.german_law.e2e import (  # noqa: E402
    CanonicalEvidenceExactVerifier,
    EvidenceBoundDraftV2Provider,
    GermanLawGoldenCase,
    REAL_HAT_SCOPE_ID,
    REAL_OFFICIAL_IDENTIFIER,
    REAL_PROVISION_HASHES,
    REAL_SOURCE_ID,
    REAL_VERSION_IDENTITY,
    build_before_after_trace,
    build_draft_v2_target_projection,
    build_evidence_bound_correction_context,
    load_german_law_golden_cases,
    project_bmjernano_temporal_facts,
    prove_draft_v1_evidence_blind,
    step38_component_inventory,
)
from aioa_memory_kernel.german_law.e2e_runtime import (  # noqa: E402
    STEP38_PERSONAL_MEMORY_SCENARIO_VERSION,
    STEP38_UPSTREAM_LINEAGE_VERSION,
    STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION,
    Step38PersonalMemoryScenario,
    Step38UpstreamRuntimeAttestation,
    Step38VerifiedUpstreamLineage,
    build_step38_post_retrieval_policy_receipt,
)
from aioa_memory_kernel.modeling import (  # noqa: E402
    DraftV1Service,
    ModelAdapterError,
    PromptTemplate,
    load_approved_provider_spec,
    prepare_model_generation_request,
)
from aioa_memory_kernel.modeling.providers import OpenRouterDraftV1Adapter  # noqa: E402
from aioa_memory_kernel.security import assert_secret_free  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    CredentialBoundaryError,
    CredentialPurpose,
    SecretValue,
    build_minimal_subprocess_environment,
    load_required_credential,
)
from aioa_memory_kernel.temporal import (  # noqa: E402
    EvidenceAvailability,
    FreshnessPolicy,
    TemporalQueryMode,
    TemporalResolutionService,
)
from aioa_memory_kernel.verification import (  # noqa: E402
    DraftV2LayeredVerifier,
    DraftV2Service,
    VerificationSummaryStatus,
    prepare_draft_v2_generation_request,
    verify_corrected_evidence_proof_hash,
)
START_SHA = "9888070ab171fd057b17ab3057b3cf868cf704d2"
SCHEMA_VERSION = "step38-german-law-full-e2e-validation-1a"
OPENROUTER_PROVIDER_ID = "openrouter"
OPENROUTER_MODEL_ID = "moonshotai/kimi-k2"
OPENROUTER_API_ORIGIN = "https://openrouter.ai"
OPENROUTER_CHAT_COMPLETIONS_PATH = "/api/v1/chat/completions"
OPENROUTER_KEY_ENVIRONMENT_NAME = "OPENROUTER_API_KEY"
FIXTURE_PATH = ROOT / "tests/fixtures/step38_german_law_cases.json"
PROVISION_III_SHA256 = (
    "fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1"
)
PROVISION_II_SHA256 = (
    "6a12a5f19d7a4b61d71be5c5583d0a3a41b3111fcf00803892200fc42260d99e"
)
PROVISION_III_EXACT_TEXT = (
    "Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen "
    "zum selben Gegenstand sind nicht mehr anzuwenden."
)
PROVISION_II_EXACT_TEXT = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der "
    "unter I. genannten Beamtinnen und Beamten vor."
)
PROVISION_II_WRONG_POLARITY_TEXT = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der "
    "unter I. genannten Beamtinnen und Beamten nicht vor."
)
PROVISION_I_EXACT_TEXT = (
    "Auf Grund des Artikels 1 Absatz 1 der Anordnung des Bundespräsidenten über "
    "die Ernennung und Entlassung der Beamtinnen, Beamten, Richterinnen und "
    "Richter des Bundes vom 23. Juni 2004 (BGBl. I S. 1286) übertrage ich "
    "widerruflich 1.der Präsidentin oder dem Präsidenten des "
    "Bundesgerichtshofs,2.der Präsidentin oder dem Präsidenten des "
    "Bundesverwaltungsgerichts,3.der Präsidentin oder dem Präsidenten des "
    "Bundesfinanzhofs,4.der Präsidentin oder dem Präsidenten des "
    "Bundespatentgerichts,5.der Präsidentin oder dem Präsidenten des "
    "Bundesamtes für Justiz,6.der Präsidentin oder dem Präsidenten des "
    "Deutschen Patent- und Markenamtes,7.der Generalbundesanwältin oder dem "
    "Generalbundesanwalt beim Bundesgerichtshofjeweils für ihren oder seinen "
    "Geschäftsbereich die Ausübung des Rechtes zur Ernennung und Entlassung "
    "der Bundesbeamtinnen und Bundesbeamten der Besoldungsgruppen bis "
    "einschließlich A 15 der Bundesbesoldungsordnung A (Anlage I des "
    "Bundesbesoldungsgesetzes)."
)
BACKUP_SPECIAL_CASE_ID = "backup-special-case-reservation"
BACKUP_DRAFT_CORRECT_EXACT = "CORRECT_EXACT_NO_MATERIAL_DEFECT"
BACKUP_DRAFT_WRONG_EXACT = "WRONG_EXACT_MATERIAL_DEFECT"
BACKUP_DRAFT_INVALID = "INVALID_FAIL_CLOSED"
SYNTHETIC_PACKET_KEY_MATERIAL = bytes(range(32))
GOLDEN_CASE_EXECUTION_PROOF_VERSION = "step38-golden-case-execution-proof-1a"
STEP39_BOUNDARY_SCAN_PROOF_VERSION = "step38-step39-boundary-scan-proof-1a"
_STEP39_SCAN_MAX_FILES = 512
_STEP39_SCAN_MAX_BYTES = 16 * 1024 * 1024
_STEP39_ALLOWED_HIT_COUNTS: Mapping[tuple[str, str], int] = {
    ("scripts/run_cockroachdb_migrations.py", "15b7e7e84aa708e30f33892a6cf0821e9ebc5e6a33d06b6585721718987f48f7"): 2,
    ("scripts/run_cockroachdb_migrations.py", "bc943cd5190ab95ccedc7ad9965be989cd16346695d45d621da3db799bb8632c"): 1,
    ("scripts/run_cockroachdb_migrations.py", "ea1edbdd3e514b04da062383519ba3211ba338c64da89ae622821ea0950fc575"): 1,
    ("scripts/run_step27_personal_memory_validation.py", "893b3ba2266571f9c7aeb72a655dce35292becda66e6c28604a44f006d16fe35"): 1,
    ("scripts/run_step28_correction_candidate_bridge_validation.py", "033e623679162947ff73680980bb840a9a3cc7d2607c7a22f3e600f0d8fd4117"): 1,
    ("scripts/run_step28_correction_candidate_bridge_validation.py", "7c70c8c50222d7f475b6727893ac14c6a8731f7f305798849acaaa16e7c3f844"): 1,
    ("scripts/run_step28_correction_candidate_bridge_validation.py", "8c4da928107147734d74479911bab192985f25adfd5b802c57bace96636260aa"): 1,
    ("scripts/run_step28_correction_candidate_bridge_validation.py", "8f12e511ba8c3529938bbee6efc6b5d4e7ae3c1d6538c277db3e021206f55d89"): 1,
    ("scripts/run_step28_correction_candidate_bridge_validation.py", "3788744b1c515f75282b7fd9c99dac01fdd8ed4f3048d61bdaaf0ade6c98da98"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "0448b8b3fa151b2a02428a3762cfe0ca09b09aa455ae905a9597922a0383a3a1"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "0c5de6206a82ac3485548091d357faab3c175baff3f54552b63863fe3490db06"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "2d0616fe464457e9392662d6675c96e86943ffc08e1ec92f32cbcfc52fe231d6"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "46b28bcf6a801d29ddcbe705266d34f216eb434cf088ae872ed870de28d5518e"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "4f9f5b2fe4e6e8081fada3ae20255fd2203d4b2efbf841b93919ffa16819de22"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "54799f89c40781d5f7f1dd5e6478cc0dbaa38327fa0f6e7ef206329ecabf2d8c"): 2,
    ("scripts/run_step38_german_law_e2e_validation.py", "6679ed739dff662658f444b8d9c6778fe0633a72a5f8b51f5d20cd6db3152335"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "881f8058df4aa1f8fde60031da430b030cd3d14d0a71244e01ea43772f5df8a1"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "acdb27d316477d76a5a14addac43e51bea7bd2288085a27e1be1e617451312b3"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "b933326165debc9f6e80d9bab7a7d72d850bce13fedfaa03e31f18060c8333a1"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "d8355844530cdf5a9bad617081a7b1ad91eca439cfd98420cc322c915f2a1756"): 1,
    ("scripts/run_step38_german_law_e2e_validation.py", "ffb3c3d52a69d25e958f35447fa527e2cc10c925ab341cef9a9115f4c7e31803"): 1,
    ("scripts/step38_coherent_runtime.py", "2a797fff472bfeb98aaeee7159ff2c6917f6b634bcef96b69ce526c9a6fc90a5"): 1,
    ("src/aioa_memory_kernel/contracts/correction.py", "cb5223921990b6f25a218ef49946cdaa4de468e36ea8e19c6b5e1503c9432ce4"): 1,
    ("src/aioa_memory_kernel/contracts/enums.py", "9ad7dbb8a08621619893a1c8a920642362aeb81082b72f893e8b838ff918143c"): 2,
    ("src/aioa_memory_kernel/contracts/patches.py", "cb5223921990b6f25a218ef49946cdaa4de468e36ea8e19c6b5e1503c9432ce4"): 1,
    ("src/aioa_memory_kernel/german_law/e2e_runtime.py", "01b08deb54a7614f88138bcad63e1bfd2975896d21b833ebea4124f291595c6b"): 1,
    ("src/aioa_memory_kernel/german_law/e2e_runtime.py", "6be9f5f2eae36428ade8d4ae30d7fe9937a55bc774198a4d06f4274a91d87d39"): 1,
    ("src/aioa_memory_kernel/german_law/e2e_runtime.py", "6c6f8dee2ee2b60aeb70999ebd1eb9960b2ca0dbe028cef65bea92d9d9475c57"): 1,
    ("src/aioa_memory_kernel/german_law/e2e_runtime.py", "6fe5f5be9da228fc22b27e2be4125f0e6b33f691e2968be2d64cec426d91b404"): 1,
    ("src/aioa_memory_kernel/german_law/e2e_runtime.py", "c4313a931039942c07c61ee62b651053a6b0c514f032d8dc5759331eb373b326"): 1,
    ("src/aioa_memory_kernel/personal_memory/candidate_repository.py", "58805110fd3c559f55f72078d6b1390a223bc64122a6f5da96df9c87899b1772"): 1,
    ("src/aioa_memory_kernel/personal_memory/candidate_repository.py", "df4c588f873e337ce8baf37df3b752b81151056f39dd9dd715cbd22249f9e79c"): 3,
    ("src/aioa_memory_kernel/personal_memory/candidate_service.py", "4fd3c8ffb673669df4e50c1b6bbaabb4221c135e36c866dc87b6836ddb35deaa"): 1,
    ("src/aioa_memory_kernel/personal_memory/candidates.py", "7d1f1b5a04d1941c5f74bb6d848e8c2297c85c2e0563329b446e7848c967994c"): 1,
    ("src/aioa_memory_kernel/personal_memory/candidates.py", "cb5223921990b6f25a218ef49946cdaa4de468e36ea8e19c6b5e1503c9432ce4"): 2,
    ("src/aioa_memory_kernel/personal_memory/candidates.py", "e33b66fb4f49146c2ae6b8bcfb7aae9fbadad16e678a70b9f9eaada8cf208f6c"): 1,
    ("src/aioa_memory_kernel/personal_memory/candidates.py", "9069423cb6b540815da3c509d5a39f4a739443b4fc319c138758a8c413993a57"): 1,
    ("src/aioa_memory_kernel/personal_memory/candidates.py", "e5bba602f5588386c55b247d0d8fbb04cff369cc24c782bdfe488651ba74c146"): 1,
    ("src/aioa_memory_kernel/personal_memory/proposal_repository.py", "6b74c63e1ed8c3c209b305d09f897521650d607dab8d4286d1a179504e6d1f1d"): 1,
    ("src/aioa_memory_kernel/personal_memory/proposals.py", "1ef03241ae1f6129a7b32a155c1588e2e3ed2e1a7a16ff600ce41a3425860de8"): 1,
    ("src/aioa_memory_kernel/personal_memory/proposals.py", "4b5632936e8b20ad3f7ba250e72273cb6b1ff2f139dfbc3cfe290e753c2429c6"): 1,
    ("src/aioa_memory_kernel/personal_memory/proposals.py", "be4766e952c5de266dc2f97631f4430d842e3470e43a862d4a72a0ebb94f273b"): 1,
    ("src/aioa_memory_kernel/state_machines/memory_patch.py", "cb5223921990b6f25a218ef49946cdaa4de468e36ea8e19c6b5e1503c9432ce4"): 1,
}


def _step39_marker_pattern() -> re.Pattern[str]:
    return re.compile(
        r"(?i)(?:\bstep[ _-]?" +
        r"39\b|\bstep39_[a-z0-9_]*(?:started|bridge|critic|production)"
        r"[a-z0-9_]*\b|critic[ _-]?(?:prompt[ _-]?loop[ _-]?)?"
        r"(?:production[ _-]?)?bridge\b|critic[ _-]?prompt[ _-]?loop\b)"
    )


class ValidationFailure(RuntimeError):
    """A failure carrying only an allowlisted, non-sensitive reason code."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 38 controlled validation failed")
        self.sanitized_code = code


@dataclass(frozen=True, slots=True)
class _Step39BoundaryScanProof:
    proof_version: str
    scanned_roots: tuple[str, ...]
    scanned_file_count: int
    scanned_source_bytes: int
    reviewed_allowlisted_hit_count: int
    reviewed_allowlisted_hits_digest: str
    unexpected_production_bridge_hits: int
    unexpected_production_bridge_hits_digest: str
    status: str
    proof_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.proof_version != STEP39_BOUNDARY_SCAN_PROOF_VERSION:
            raise ContractValidationError("unsupported Step 39 boundary scan proof")
        if not self.scanned_roots or len(self.scanned_roots) > 4:
            raise ContractValidationError("Step 39 scan roots are invalid")
        if not 0 <= self.scanned_file_count <= _STEP39_SCAN_MAX_FILES:
            raise ContractValidationError("Step 39 scan file bound exceeded")
        if not 0 <= self.scanned_source_bytes <= _STEP39_SCAN_MAX_BYTES:
            raise ContractValidationError("Step 39 scan byte bound exceeded")
        for value, name in (
            (self.reviewed_allowlisted_hits_digest, "reviewed_allowlisted_hits_digest"),
            (
                self.unexpected_production_bridge_hits_digest,
                "unexpected_production_bridge_hits_digest",
            ),
        ):
            require_sha256_hex(value, name)
        expected_status = (
            "PASS" if self.unexpected_production_bridge_hits == 0 else "FAIL"
        )
        if self.status != expected_status:
            raise IntegrityError("Step 39 scan status is detached from observations")
        object.__setattr__(
            self,
            "proof_hash",
            canonical_sha256(self, exclude_fields=("proof_hash",)),
        )

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def public_mapping(self) -> Mapping[str, Any]:
        return {
            "proof_version": self.proof_version,
            "status": self.status,
            "scanned_roots": self.scanned_roots,
            "scanned_file_count": self.scanned_file_count,
            "scanned_source_bytes": self.scanned_source_bytes,
            "reviewed_allowlisted_hits_count": (
                self.reviewed_allowlisted_hit_count
            ),
            "reviewed_allowlisted_hits_digest": (
                self.reviewed_allowlisted_hits_digest
            ),
            "unexpected_production_bridge_hits": (
                self.unexpected_production_bridge_hits
            ),
            "unexpected_production_bridge_hits_digest": (
                self.unexpected_production_bridge_hits_digest
            ),
            "proof_hash": self.proof_hash,
        }


def _step39_boundary_scan(
    *,
    root_specs: Sequence[tuple[str, Path]] | None = None,
    allowlisted_hit_counts: Mapping[tuple[str, str], int] | None = None,
) -> _Step39BoundaryScanProof:
    """Scan bounded production roots without emitting matched source text."""

    specs = tuple(root_specs or (
        ("src/aioa_memory_kernel", ROOT / "src/aioa_memory_kernel"),
        ("scripts", ROOT / "scripts"),
    ))
    allowlist = dict(
        _STEP39_ALLOWED_HIT_COUNTS
        if allowlisted_hit_counts is None
        else allowlisted_hit_counts
    )
    pattern = _step39_marker_pattern()
    reviewed: list[Mapping[str, Any]] = []
    unexpected: list[Mapping[str, Any]] = []
    observed_counts: dict[tuple[str, str], int] = {}
    scanned_files = 0
    scanned_bytes = 0
    for label, root in specs:
        if not label or root.is_symlink() or not root.is_dir():
            raise ContractValidationError("Step 39 scan root is invalid")
        for path in sorted(root.rglob("*.py")):
            if path.is_symlink() or not path.is_file():
                raise ContractValidationError("Step 39 scan rejects symlinked sources")
            data = path.read_bytes()
            scanned_files += 1
            scanned_bytes += len(data)
            if (
                scanned_files > _STEP39_SCAN_MAX_FILES
                or scanned_bytes > _STEP39_SCAN_MAX_BYTES
            ):
                raise ContractValidationError("Step 39 scan bound exceeded")
            relative = f"{label}/{path.relative_to(root).as_posix()}"
            for line_number, line in enumerate(data.decode("utf-8").splitlines(), 1):
                if pattern.search(line) is None:
                    continue
                line_digest = hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
                key = (relative, line_digest)
                observed_counts[key] = observed_counts.get(key, 0) + 1
                record = {
                    "path": relative,
                    "line_number": line_number,
                    "line_sha256": line_digest,
                }
                if observed_counts[key] <= allowlist.get(key, 0):
                    reviewed.append(record)
                else:
                    unexpected.append(record)
    return _Step39BoundaryScanProof(
        proof_version=STEP39_BOUNDARY_SCAN_PROOF_VERSION,
        scanned_roots=tuple(label for label, _root in specs),
        scanned_file_count=scanned_files,
        scanned_source_bytes=scanned_bytes,
        reviewed_allowlisted_hit_count=len(reviewed),
        reviewed_allowlisted_hits_digest=canonical_sha256(tuple(reviewed)),
        unexpected_production_bridge_hits=len(unexpected),
        unexpected_production_bridge_hits_digest=canonical_sha256(tuple(unexpected)),
        status="PASS" if not unexpected else "FAIL",
    )


def _closure_eligible_with_boundary_scan(
    coherent_proof: Any,
    boundary_proof: _Step39BoundaryScanProof,
) -> bool:
    if not isinstance(boundary_proof, _Step39BoundaryScanProof):
        raise TypeError("boundary_proof must be a typed scan proof")
    return bool(coherent_proof.closure_eligible and boundary_proof.passed)


@dataclass(frozen=True, slots=True)
class _GoldenCaseExecutionProof:
    """Hash-bound proof that one declared case, not merely its label, ran."""

    proof_version: str
    case: GermanLawGoldenCase
    execution_class: str
    executed_question_digest: str
    executed_knowledge_as_of: str
    executed_source_ids: tuple[str, ...]
    executed_provision_ids: tuple[str, ...]
    executed_version_ids: tuple[str, ...]
    routing_input_hash: str
    route_hash: str
    policy_result_hash: str
    step20_outcome_hash: str | None
    temporal_result_hash: str | None
    draft_v1_request_hash: str | None
    final_outcome_hash: str | None
    execution_result_hash: str
    executed_route: str
    executed_evidence_status: str
    executed_final_output: str
    actual_outcome_status: str
    step26_invoked: bool
    required_correction_count: int | None
    review_result_hash: str | None
    verified_answer_hash: str | None
    proof_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.proof_version != GOLDEN_CASE_EXECUTION_PROOF_VERSION:
            raise ContractValidationError(
                "unsupported golden-case execution proof"
            )
        if not isinstance(self.case, GermanLawGoldenCase):
            raise ContractValidationError("case must be a GermanLawGoldenCase")
        expected_question_digest = hashlib.sha256(
            self.case.question.encode("utf-8")
        ).hexdigest()
        expected_case_hash = canonical_sha256(
            self.case,
            exclude_fields=("case_hash", "question"),
        )
        if (
            self.case.question_digest != expected_question_digest
            or self.case.case_hash != expected_case_hash
            or self.executed_question_digest != self.case.question_digest
            or self.executed_knowledge_as_of
            != self.case.knowledge_as_of.isoformat()
            or self.executed_source_ids
            != (() if self.case.expected_source_id is None else (self.case.expected_source_id,))
            or self.executed_provision_ids != self.case.expected_provision_ids
            or self.executed_route != self.case.expected_route.value
            or self.executed_evidence_status
            != self.case.expected_evidence_status.value
            or self.executed_final_output
            != self.case.expected_final_output.value
        ):
            raise IntegrityError("golden case execution detached from fixture")
        if any(not value for value in self.executed_version_ids):
            raise ContractValidationError("executed version identities must be non-empty")
        if self.case.expected_source_id is None and self.executed_version_ids:
            raise IntegrityError("route-only case has downstream version identities")
        if self.execution_class not in {
            "EXACT_REAL_SOURCE_BYTES_DETERMINISTIC_PROVIDER_FIXTURE",
            "SYNTHETIC_FAIL_CLOSED_CONTRACT_FIXTURE",
            "DETERMINISTIC_ROUTE_ONLY",
        }:
            raise ContractValidationError("unknown golden-case execution class")
        for value, name in (
            (self.executed_question_digest, "executed_question_digest"),
            (self.routing_input_hash, "routing_input_hash"),
            (self.route_hash, "route_hash"),
            (self.policy_result_hash, "policy_result_hash"),
            (self.execution_result_hash, "execution_result_hash"),
        ):
            require_sha256_hex(value, name)
        for value, name in (
            (self.step20_outcome_hash, "step20_outcome_hash"),
            (self.temporal_result_hash, "temporal_result_hash"),
            (self.draft_v1_request_hash, "draft_v1_request_hash"),
            (self.final_outcome_hash, "final_outcome_hash"),
            (self.review_result_hash, "review_result_hash"),
            (self.verified_answer_hash, "verified_answer_hash"),
        ):
            if value is not None:
                require_sha256_hex(value, name)
        if not isinstance(self.step26_invoked, bool):
            raise ContractValidationError("step26_invoked must be boolean")
        if self.required_correction_count is not None and (
            not isinstance(self.required_correction_count, int)
            or isinstance(self.required_correction_count, bool)
            or self.required_correction_count < 0
        ):
            raise ContractValidationError(
                "required_correction_count must be a non-negative integer"
            )
        if self.case.expected_final_output.value == "PASS_THROUGH":
            if (
                self.step26_invoked
                or self.actual_outcome_status != "PASS_THROUGH_RESULT"
                or self.required_correction_count is not None
                or self.step20_outcome_hash is not None
                or self.temporal_result_hash is not None
                or self.draft_v1_request_hash is not None
                or self.final_outcome_hash is not None
                or self.review_result_hash is not None
                or self.verified_answer_hash is not None
                or self.execution_result_hash != self.policy_result_hash
            ):
                raise IntegrityError("route-only proof invoked a downstream authority")
        elif self.case.expected_final_output.value == "VERIFIED_ANSWER":
            if (
                not self.step26_invoked
                or self.actual_outcome_status != "VERIFIED_ANSWER"
                or self.required_correction_count != 0
                or self.step20_outcome_hash is None
                or self.temporal_result_hash is None
                or self.draft_v1_request_hash is None
                or self.final_outcome_hash is None
                or self.verified_answer_hash is None
                or self.review_result_hash is not None
                or self.execution_result_hash != self.final_outcome_hash
            ):
                raise IntegrityError("verified-answer execution proof is incomplete")
        else:
            if (
                not self.step26_invoked
                or self.actual_outcome_status not in {
                    "CONFIRMATION_REQUIRED",
                    "HUMAN_REVIEW_REQUIRED",
                }
                or self.required_correction_count is None
                or self.step20_outcome_hash is None
                or self.temporal_result_hash is None
                or self.draft_v1_request_hash is None
                or self.final_outcome_hash is None
                or self.review_result_hash is None
                or self.verified_answer_hash is not None
                or self.execution_result_hash != self.final_outcome_hash
            ):
                raise IntegrityError("review execution proof is incomplete")
        object.__setattr__(
            self,
            "proof_hash",
            canonical_sha256(
                {
                    "proof_version": self.proof_version,
                    "case_hash": self.case.case_hash,
                    "question_digest": self.case.question_digest,
                    "execution_class": self.execution_class,
                    "executed_question_digest": self.executed_question_digest,
                    "executed_knowledge_as_of": self.executed_knowledge_as_of,
                    "executed_source_ids": self.executed_source_ids,
                    "executed_provision_ids": self.executed_provision_ids,
                    "executed_version_ids": self.executed_version_ids,
                    "routing_input_hash": self.routing_input_hash,
                    "route_hash": self.route_hash,
                    "policy_result_hash": self.policy_result_hash,
                    "step20_outcome_hash": self.step20_outcome_hash,
                    "temporal_result_hash": self.temporal_result_hash,
                    "draft_v1_request_hash": self.draft_v1_request_hash,
                    "final_outcome_hash": self.final_outcome_hash,
                    "execution_result_hash": self.execution_result_hash,
                    "executed_route": self.executed_route,
                    "executed_evidence_status": self.executed_evidence_status,
                    "executed_final_output": self.executed_final_output,
                    "actual_outcome_status": self.actual_outcome_status,
                    "step26_invoked": self.step26_invoked,
                    "required_correction_count": self.required_correction_count,
                    "review_result_hash": self.review_result_hash,
                    "verified_answer_hash": self.verified_answer_hash,
                }
            ),
        )

    def public_mapping(self) -> Mapping[str, Any]:
        return {
            "proof_version": self.proof_version,
            "proof_hash": self.proof_hash,
            "case_hash": self.case.case_hash,
            "fixture_class": self.case.fixture_class.value,
            "execution_class": self.execution_class,
            "question_digest": self.case.question_digest,
            "executed_question_digest": self.executed_question_digest,
            "question_exactly_executed": True,
            "knowledge_as_of": self.executed_knowledge_as_of,
            "executed_source_ids": self.executed_source_ids,
            "executed_provision_ids": self.executed_provision_ids,
            "executed_version_ids": self.executed_version_ids,
            "routing_input_hash": self.routing_input_hash,
            "route_hash": self.route_hash,
            "policy_result_hash": self.policy_result_hash,
            "step20_outcome_hash": self.step20_outcome_hash,
            "temporal_result_hash": self.temporal_result_hash,
            "draft_v1_request_hash": self.draft_v1_request_hash,
            "final_outcome_hash": self.final_outcome_hash,
            "execution_result_hash": self.execution_result_hash,
            "expected_route": self.case.expected_route.value,
            "executed_route": self.executed_route,
            "expected_evidence_status": (
                self.case.expected_evidence_status.value
            ),
            "executed_evidence_status": self.executed_evidence_status,
            "expected_final_output": self.case.expected_final_output.value,
            "executed_final_output": self.executed_final_output,
            "actual_outcome_status": self.actual_outcome_status,
            "step26_invoked": self.step26_invoked,
            "required_correction_count": self.required_correction_count,
            "review_result_hash": self.review_result_hash,
            "verified_answer_hash": self.verified_answer_hash,
        }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run deterministic development proofs only; never closure eligible",
    )
    parser.add_argument(
        "--external-env",
        type=Path,
        default=step18.ROOT / ".local/external-data.env",
    )
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument(
        "--step14-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP14,
    )
    parser.add_argument(
        "--step15-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP15,
    )
    parser.add_argument(
        "--step16-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP16,
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=step18.DEFAULT_SOURCE_ROOT,
    )
    return parser.parse_args()


def _step38_openrouter_spec() -> Any:
    """Load and independently pin the user-approved Step 38 provider."""

    spec = load_approved_provider_spec()
    if (
        spec.provider_id != OPENROUTER_PROVIDER_ID
        or spec.model_id != OPENROUTER_MODEL_ID
        or spec.model_declared_version != OPENROUTER_MODEL_ID
        or spec.api_origin != OPENROUTER_API_ORIGIN
        or spec.chat_completions_path != OPENROUTER_CHAT_COMPLETIONS_PATH
        or spec.credential_environment_variable
        != OPENROUTER_KEY_ENVIRONMENT_NAME
        or not spec.tooling_disabled
        or not spec.function_calling_disabled
        or not spec.web_browsing_disabled
        or not spec.code_execution_disabled
    ):
        raise ValidationFailure("STEP38_OPENROUTER_IDENTITY_MISMATCH")
    return spec


def _consume_openrouter_environment_credential() -> SecretValue | None:
    """Remove the exact provider secret from ambient child inheritance."""

    spec = _step38_openrouter_spec()
    name = spec.credential_environment_variable
    if name not in os.environ:
        return None
    try:
        return load_required_credential(
            CredentialPurpose.MODEL_PROVIDER,
            os.environ,
        )
    finally:
        os.environ.pop(name, None)


def _minimal_openrouter_reexec_environment(
    provider_credential: SecretValue,
) -> dict[str, str]:
    """Build the sole child environment allowed to carry the provider key."""

    if (
        not isinstance(provider_credential, SecretValue)
        or provider_credential.purpose is not CredentialPurpose.MODEL_PROVIDER
    ):
        raise ValidationFailure("STEP38_REAL_MODEL_VALIDATION_REQUIRED")
    spec = _step38_openrouter_spec()
    environment = build_minimal_subprocess_environment(os.environ)
    environment[spec.credential_environment_variable] = (
        provider_credential.reveal_for(CredentialPurpose.MODEL_PROVIDER)
    )
    if "MOONSHOT_API_KEY" in environment:
        raise ValidationFailure("STEP38_LEGACY_PROVIDER_SECRET_IN_CHILD_ENV")
    return environment


def _provider_public_spec(spec: Any) -> Mapping[str, Any]:
    """Project only non-secret, exact provider configuration evidence."""

    checked = _step38_openrouter_spec()
    if spec != checked:
        raise ValidationFailure("STEP38_OPENROUTER_IDENTITY_MISMATCH")
    return {
        "provider_id": spec.provider_id,
        "adapter_version": spec.adapter_version,
        "model_id": spec.model_id,
        "model_declared_version": spec.model_declared_version,
        "endpoint_class": spec.endpoint_class,
        "api_origin": spec.api_origin,
        "chat_completions_path": spec.chat_completions_path,
        "context_window_tokens": spec.context_window_tokens,
        "model_registry_owner": spec.model_registry_owner,
        "immutable_model_revision": spec.immutable_model_revision,
        "config_digest": spec.config_digest,
        "provider_identity_digest": spec.provider_identity().identity_digest,
        "tools_disabled": spec.tooling_disabled,
        "function_calling_disabled": spec.function_calling_disabled,
        "web_disabled": spec.web_browsing_disabled,
        "code_execution_disabled": spec.code_execution_disabled,
        "provider_material_recorded": False,
    }


def _progress(stage: str, status: str = "RUNNING") -> None:
    print(
        canonical_json({"stage": stage, "status": status, "step": 38}),
        file=sys.stderr,
        flush=True,
    )


def _repository_identity() -> Mapping[str, Any]:
    def command(*arguments: str) -> str:
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            raise ValidationFailure("STEP38_GIT_IDENTITY_UNAVAILABLE")
        return completed.stdout.strip()

    head = command("git", "rev-parse", "HEAD")
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise ValidationFailure("STEP38_GIT_HEAD_INVALID")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", START_SHA, head],
        cwd=ROOT,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ValidationFailure("STEP37_BASE_NOT_REACHABLE")
    return {
        "step37_base_sha": START_SHA,
        "validation_head_sha": head,
        "step37_base_reachable": True,
    }


def _run_unit_suite() -> Mapping[str, Any]:
    names = (
        "tests.test_step38_german_law_e2e",
        "tests.test_step38_corrected_claim_bridge",
    )
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if not result.wasSuccessful() or result.testsRun < 10:
        raise ValidationFailure("STEP38_FOCUSED_CONTRACT_TESTS_FAILED")
    return {
        "status": "PASS",
        "test_count": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "modules": list(names),
    }


def _authority_contract_proofs() -> Mapping[str, Any]:
    """Run the Step 36 service-purpose negatives used by live evidence."""

    names = (
        "tests.test_step36_credential_separation",
        "tests.test_step36_commit_authority",
    )
    excluded = {
        "tests.test_step36_credential_separation.BrowserAndCapabilityMatrixTests."
        "test_controlled_offline_browser_proof_reports_rendered_output"
    }

    def cases(value: unittest.TestSuite):
        for item in value:
            if isinstance(item, unittest.TestSuite):
                yield from cases(item)
            else:
                yield item

    loaded = unittest.defaultTestLoader.loadTestsFromNames(names)
    selected = tuple(item for item in cases(loaded) if item.id() not in excluded)
    suite = unittest.TestSuite(selected)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if not result.wasSuccessful() or result.testsRun < 20:
        raise ValidationFailure("STEP38_AUTHORITY_CONTRACT_PROOFS_FAILED")
    invariants = (
        "PROVIDER_HAS_NO_DATABASE_COMMIT_AUTHORITY",
        "COMMIT_HELPER_HAS_NO_APPROVAL_AUTHORITY",
        "REVIEWER_HAS_NO_COMMIT_AUTHORITY",
        "MISSING_CREDENTIAL_HAS_NO_ADMIN_FALLBACK",
        "BROWSER_HAS_NO_PRIVILEGED_SECRET",
    )
    return {
        "status": "PASS",
        "test_count": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "modules": names,
        "excluded_runtime_dependency_test_ids": tuple(sorted(excluded)),
        "excluded_test_replacement": (
            "PINNED_ASSET_CHECK_PLUS_LIVE_REDACTION_SCAN"
        ),
        "validated_invariants": invariants,
        "proof_digest": canonical_sha256(
            {
                "modules": names,
                "excluded_runtime_dependency_test_ids": tuple(
                    sorted(excluded)
                ),
                "excluded_test_replacement": (
                    "PINNED_ASSET_CHECK_PLUS_LIVE_REDACTION_SCAN"
                ),
                "test_count": result.testsRun,
                "validated_invariants": invariants,
            }
        ),
    }


def _corrected_proof_evidence(pipeline: Any) -> Mapping[str, Any]:
    """Project reconstructible proof bindings without raw claim/source text."""

    signals = tuple(
        item
        for item in pipeline.ordered_claim_verifications
        if item.corrected_evidence_signal_hash is not None
    )
    proofs: list[Mapping[str, Any]] = []
    for verification in signals:
        proof = verification.corrected_evidence_proof
        proof_hash = verification.corrected_evidence_proof_hash
        if proof is None or proof_hash is None:
            continue
        verify_corrected_evidence_proof_hash(proof)
        if proof.proof_hash != proof_hash:
            raise ValidationFailure("STEP38_CORRECTED_EVIDENCE_PROOF_DETACHED")
        link = proof.original_evidence_link
        proofs.append(
            {
                "claim_id": verification.claim_id,
                "claim_hash": verification.claim_hash,
                "signal_hash": verification.corrected_evidence_signal_hash,
                "proof_hash": proof_hash,
                "proof_version": proof.proof_version,
                "target_claim_hash": proof.target_claim_hash,
                "packet_citation_hash": proof.packet_citation_hash,
                "evidence_context_hash": proof.evidence_context_hash,
                "source_evidence_link_hash": link.link_hash,
                "step20_bundle_hash": link.step20_bundle_hash,
                "step20_item_hash": link.step20_item_hash,
                "evidence_span_convention": link.evidence_span_convention,
                "evidence_start_offset": link.evidence_start_offset,
                "evidence_end_offset": link.evidence_end_offset,
                "evidence_span_text_sha256": proof.evidence_span_text_sha256,
                "full_typed_proof_verified": True,
            }
        )
    if not proofs:
        raise ValidationFailure("STEP38_CORRECTED_EVIDENCE_PROOF_MISSING")
    return {
        "signal_count": len(signals),
        "proof_count": len(proofs),
        "proofs": proofs,
        "raw_claim_or_source_text_recorded": False,
    }


def _offline_contract_proofs(suite: Any) -> Mapping[str, Any]:
    """Run deterministic fixtures without pretending they are a real E2E."""

    from aioa_memory_kernel.reliability import FailurePoint
    from tests.test_step21_temporal_resolution import metadata
    from tests.test_step26_verified_answer_output import hat_lineage
    from tests.test_step31_active_patch_retrieval import (
        active_fixture,
        candidate as active_candidate,
        request_for as active_request_for,
        service as active_retrieval_service,
    )
    from tests.test_step33_audit_ledger import chain as synthetic_audit_chain
    from tests.test_step35_personal_memory_ui import (
        FakeBackend as SyntheticUiBackend,
        OWNER_A as SYNTHETIC_UI_OWNER,
    )
    from tests.test_step37_recovery_idempotency import (
        run_personal_memory_recovery_campaigns,
    )
    from tests.test_step38_german_law_e2e import primary_lineage

    unit_tests = _run_unit_suite()
    lineage = primary_lineage()
    trace = build_before_after_trace(
        lineage["case"],
        lineage["draft"],
        lineage["packet"],
        lineage["pipeline"],
        lineage["final"],
        lineage["context"],
        lineage["evidence_provider"].input_receipts[0],
    )
    blindness = prove_draft_v1_evidence_blind(
        lineage["draft_request"],
        lineage["case"].question,
    )
    corrected_proofs = _corrected_proof_evidence(lineage["pipeline"])

    _fixture, slot, _committed, active, first_model, second_model = active_fixture()
    patch_hashes: list[str] = []
    context_hashes: list[str] = []
    for model_binding in (first_model, second_model):
        request, selected_route, temporal = active_request_for(model_binding)
        retrieval, _repository, patcher = active_retrieval_service(
            slot,
            (active_candidate(),),
        )
        try:
            result, context = retrieval.retrieve(
                request,
                route=selected_route,
                temporal_result=temporal,
            )
        finally:
            patcher.stop()
        if len(result.eligible_patches) != 1 or context.canonical_evidence_authority:
            raise ValidationFailure("STEP38_OFFLINE_CROSS_MODEL_PROOF_FAILED")
        patch_hashes.append(result.eligible_patches[0].patch_hash)
        context_hashes.append(context.context_hash)
    if patch_hashes != [active.committed_patch.patch_hash] * 2:
        raise ValidationFailure("STEP38_OFFLINE_PATCH_HASH_REUSE_FAILED")

    other_request, other_route, other_temporal = active_request_for(
        first_model,
        user_id="step38-other-owner",
    )
    retrieval, _repository, patcher = active_retrieval_service(
        slot,
        (active_candidate(),),
    )
    try:
        other_result, _other_context = retrieval.retrieve(
            other_request,
            route=other_route,
            temporal_result=other_temporal,
        )
    finally:
        patcher.stop()
    if other_result.eligible_patches:
        raise ValidationFailure("STEP38_OFFLINE_OWNER_ISOLATION_FAILED")

    entries = synthetic_audit_chain(4)
    audit = verify_audit_chain(entries[0].envelope.chain_id, entries)
    if not audit.verified:
        raise ValidationFailure("STEP38_OFFLINE_AUDIT_CHAIN_FAILED")

    review_request, review_authenticator = hat_lineage(
        values=(
            metadata(version="step38-conflict-a"),
            metadata(version="step38-conflict-b"),
        ),
        contents=("Widerspruch A", "Widerspruch B"),
    )
    review = VerifiedAnswerService(review_authenticator).finalize(review_request)
    if (
        review.output_status is not FinalOutputStatus.HUMAN_REVIEW_REQUIRED
        or review.human_review is None
    ):
        raise ValidationFailure("STEP38_OFFLINE_REVIEW_FALLBACK_FAILED")

    dashboard = SyntheticUiBackend().dashboard(SYNTHETIC_UI_OWNER)
    if not (
        dashboard.pending_approval_count == 1
        and dashboard.active_patch_count == 1
        and len(dashboard.recent_audit_events) == 1
    ):
        raise ValidationFailure("STEP38_OFFLINE_UI_PROJECTION_FAILED")

    recovery = next(
        (
            item
            for item in run_personal_memory_recovery_campaigns()
            if item.failure_point is FailurePoint.PM_AFTER_COMMIT_ACK_LOST
        ),
        None,
    )
    if (
        recovery is None
        or recovery.duplicate_side_effect_count
        or recovery.authority_violation_count
        or recovery.integrity_violation_count
    ):
        raise ValidationFailure("STEP38_RECOVERY_SPOT_CHECK_FAILED")

    validation_receipt = active.step29_state.validation_receipt
    if (
        active.approval_receipt is None
        or active.commit_receipt is None
        or active.activation_receipt is None
        or active.committed_patch is None
        or validation_receipt is None
    ):
        raise ValidationFailure("STEP38_OFFLINE_MEMORY_LINEAGE_INCOMPLETE")

    return {
        "classification": (
            "EXACT_SOURCE_BYTES_SYNTHETIC_TYPED_FIXTURE_COMPONENT_PROOF_ONLY"
        ),
        "closure_authority": False,
        "unit_tests": unit_tests,
        "suite_hash": suite.suite_hash,
        "before_after": {
            "trace_hash": trace.trace_hash,
            "draft_v1_hash": trace.draft_v1_hash,
            "correction_packet_hash": trace.correction_packet_hash,
            "draft_v2_hash": trace.draft_v2_hash,
            "verification_summary_hash": trace.verification_summary_hash,
            "verified_answer_hash": trace.verified_answer_hash,
            "evidence_context_hash": trace.evidence_context_hash,
            "augmented_provider_input_receipt_hash": (
                trace.augmented_provider_input_receipt_hash
            ),
            "draft_v1_evidence_blind": not blindness.evidence_fields_projected,
            "original_query_exactly_matched": blindness.expected_query_matched,
            "tools_enabled": blindness.tools_enabled,
            "corrected_evidence": corrected_proofs,
        },
        "personal_memory": {
            "proposal_hash": active.step29_state.proposal.proposal_hash,
            "validation_receipt_hash": validation_receipt.receipt_hash,
            "approval_receipt_hash": active.approval_receipt.receipt_hash,
            "commit_receipt_hash": active.commit_receipt.receipt_hash,
            "activation_receipt_hash": active.activation_receipt.receipt_hash,
            "active_patch_hash": active.committed_patch.patch_hash,
            "two_model_same_patch_hash": len(set(patch_hashes)) == 1,
            "context_envelope_hashes": context_hashes,
            "other_owner_eligible_patch_count": len(other_result.eligible_patches),
            "canonical_evidence_authority": False,
        },
        "audit": {
            "verified": audit.verified,
            "event_count": audit.event_count,
            "first_hash": audit.first_hash,
            "last_hash": audit.last_hash,
            "verification_result_hash": audit.result_hash,
        },
        "review": {
            "output_status": review.output_status.value,
            "review_result_hash": review.human_review.result_hash,
        },
        "ui": {
            "pending_approval_count": dashboard.pending_approval_count,
            "active_patch_count": dashboard.active_patch_count,
            "recent_audit_event_count": len(dashboard.recent_audit_events),
        },
        "failure_recovery_spot_check": {
            "failure_point": recovery.failure_point.value,
            "recovery_status": recovery.recovery_status.value,
            "result_hash": recovery.result_hash,
            "duplicate_side_effects": recovery.duplicate_side_effect_count,
            "authority_violations": recovery.authority_violation_count,
        },
    }


def _execute_named_golden_case_proofs(
    suite: Any,
) -> tuple[_GoldenCaseExecutionProof, ...]:
    """Execute the four provider-free cases using their exact fixture inputs."""

    if (
        hashlib.sha256(PROVISION_I_EXACT_TEXT.encode("utf-8")).hexdigest()
        != REAL_PROVISION_HASHES["I."]
        or hashlib.sha256(PROVISION_II_EXACT_TEXT.encode("utf-8")).hexdigest()
        != REAL_PROVISION_HASHES["II."]
        or hashlib.sha256(PROVISION_III_EXACT_TEXT.encode("utf-8")).hexdigest()
        != REAL_PROVISION_HASHES["III."]
    ):
        raise ValidationFailure("STEP38_EXACT_SOURCE_BYTES_HASH_MISMATCH")

    import step38_real_retrieval as real_retrieval
    from aioa_memory_kernel.contracts.enums import (
        EvidenceStatus,
        KnowledgeRoute,
        ScopeComparisonMode,
        ScopeValueType,
    )
    from aioa_memory_kernel.contracts.scope import ScopeDimension
    from aioa_memory_kernel.hats import decode_manifest
    from aioa_memory_kernel.routing import (
        AuthorityPolicyContext,
        EvidenceCoverageStatus,
        ExecutionAuthorizationDecision,
        HatPolicyRequirement,
        HatRoutingCandidate,
        KnowledgePolicyDecision,
        RoutingInput,
        TrustedHatRegistrySnapshot,
        evaluate_policy_gate,
        route_knowledge_request,
    )
    from tests.test_step21_temporal_resolution import (
        bundle_outcome,
        metadata,
        resolve,
    )
    from tests.test_step23_claim_evidence_binding import (
        FakeProvider as DraftV1FixtureProvider,
        FixedClock as DraftV1FixtureClock,
    )
    from tests.test_step25_draft_v2_layered_verifier import run_v2

    identity = decode_manifest(
        (ROOT / "config/hats/german-law-1.0.0.json").read_bytes(),
        schema_path=ROOT / "schemas/hat-manifest.schema.json",
    )

    def route_and_policy(case: GermanLawGoldenCase):
        entry = real_retrieval._canonical_manifest_entry(
            identity,
            case.knowledge_as_of,
        )
        requirement = (
            HatPolicyRequirement.MANDATORY
            if case.expected_route is KnowledgeRoute.HAT_ENFORCE
            else HatPolicyRequirement.ADVISORY
        )
        candidates = (
            ()
            if case.expected_route is KnowledgeRoute.PASS_THROUGH
            else (
                HatRoutingCandidate(
                    identity.manifest.hat_id,
                    identity.manifest.hat_version,
                    identity.typed_manifest_digest,
                    requirement,
                ),
            )
        )
        scope = (
            ScopeDimension(
                "legal_jurisdiction",
                "DE_FEDERAL",
                ScopeValueType.STRING,
                ScopeComparisonMode.EXACT,
                "step38-golden-case",
                True,
            ),
            ScopeDimension(
                "knowledge_as_of",
                case.knowledge_as_of,
                ScopeValueType.TIMESTAMP,
                ScopeComparisonMode.TIMESTAMP,
                "step38-golden-case",
                True,
            ),
            ScopeDimension(
                "source_language",
                "de",
                ScopeValueType.STRING,
                ScopeComparisonMode.EXACT,
                "step38-golden-case",
                True,
            ),
            ScopeDimension(
                "legal_source_class",
                ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
                ScopeValueType.STRING_SET,
                ScopeComparisonMode.IN_SET,
                "step38-golden-case",
                True,
            ),
        )
        coverage = {
            EvidenceStatus.SUFFICIENT: EvidenceCoverageStatus.COMPLETE,
            EvidenceStatus.INSUFFICIENT: EvidenceCoverageStatus.PARTIAL,
            EvidenceStatus.CONFLICTING: EvidenceCoverageStatus.CONFLICTING,
            EvidenceStatus.NOT_REQUIRED: EvidenceCoverageStatus.EMPTY,
        }[case.expected_evidence_status]
        routing_input = RoutingInput(
            tenant_id="tenant-step20",
            user_id="user-step20",
            request_id=f"request-step38-{case.case_id}",
            request_kind="knowledge-question",
            normalized_query_or_subject=case.question,
            requested_domain_id=(
                "weather.general"
                if case.expected_route is KnowledgeRoute.PASS_THROUGH
                else "law.de.federal"
            ),
            requested_scope=scope,
            candidate_hat_descriptors=candidates,
            trusted_hat_registry_snapshot=TrustedHatRegistrySnapshot(
                "trusted-registry:step38:golden-cases",
                (entry,),
            ),
            evidence_status=case.expected_evidence_status,
            evidence_coverage_status=coverage,
            context_metadata={
                "classification_source": "deterministic-step38-golden-case",
                "golden_case_hash": case.case_hash,
                "question_digest": case.question_digest,
            },
        )
        selected_route = route_knowledge_request(routing_input)
        if (
            routing_input.normalized_query_or_subject != case.question
            or selected_route.knowledge_route is not case.expected_route
        ):
            raise ValidationFailure("STEP38_GOLDEN_CASE_ROUTE_MISMATCH")
        ceiling = (
            KnowledgePolicyDecision.REQUIRE_CONFIRMATION
            if case.expected_final_output.value == "HUMAN_REVIEW_REQUIRED"
            else KnowledgePolicyDecision.ALLOW_ANSWER
        )
        context = AuthorityPolicyContext(
            request_id=routing_input.request_id,
            tenant_id=routing_input.tenant_id,
            user_id=routing_input.user_id,
            policy_reference=f"policy:step38:{case.case_id}",
            policy_digest=canonical_sha256(
                {"case_hash": case.case_hash, "policy_ceiling": ceiling.value}
            ),
            knowledge_policy_ceiling=ceiling,
            execution_authorization_ceiling=(
                ExecutionAuthorizationDecision.DENY
            ),
            scope_allowed=True,
            selected_hat_id=selected_route.selected_hat_id,
            selected_hat_version=selected_route.selected_hat_version,
            selected_manifest_digest=selected_route.selected_manifest_digest,
        )
        policy = evaluate_policy_gate(routing_input, selected_route, context)
        if policy.evidence_status is not case.expected_evidence_status:
            raise ValidationFailure("STEP38_GOLDEN_CASE_POLICY_MISMATCH")
        return routing_input, selected_route, policy

    def finalize_hat_case(
        case: GermanLawGoldenCase,
        *,
        metadatas: tuple[dict[str, object], ...],
        contents: tuple[str, ...],
        source_ids: tuple[str, ...],
        draft_text: str,
        execution_class: str,
    ) -> _GoldenCaseExecutionProof:
        routing_input, selected_route, policy = route_and_policy(case)
        step20 = bundle_outcome(
            *metadatas,
            contents=contents,
            source_ids=source_ids,
            route_value=selected_route,
            effective_scope=selected_route.effective_scope,
        )
        temporal = resolve(
            step20,
            route_value=selected_route,
            mode=TemporalQueryMode.AS_OF,
            as_of=case.knowledge_as_of,
        )
        if temporal.evidence_status is not case.expected_evidence_status:
            raise ValidationFailure("STEP38_GOLDEN_CASE_TEMPORAL_MISMATCH")
        draft_request = prepare_model_generation_request(temporal, case.question)
        if draft_request.original_query_digest != case.question_digest:
            raise ValidationFailure("STEP38_GOLDEN_CASE_QUERY_DETACHED")
        draft_receipt = DraftV1Service(
            DraftV1FixtureProvider(draft_request, draft_text),
            clock=DraftV1FixtureClock(),
            sleep=lambda _: None,
        ).generate(draft_request)
        claim_request = prepare_claim_binding_request(
            draft_receipt.draft,
            (step20.bundle,),
            temporal,
        )
        snapshot = ClaimEvidenceBindingService().freeze_packet_input(
            claim_request
        )
        packet = build_correction_packet(snapshot)
        pipeline, _provider, _request, authenticator = run_v2(
            draft_receipt.draft,
            packet,
            draft_text,
        )
        final_request = FinalAnswerRequest(
            route=selected_route,
            policy_result=policy,
            step20_outcomes=(step20,),
            temporal_result=temporal,
            draft_v1=draft_receipt.draft,
            correction_packet=packet,
            integrity_receipt=authenticator.authenticate(packet),
            step25_result=pipeline,
        )
        outcome = VerifiedAnswerService(authenticator).finalize(final_request)
        if case.expected_final_output.value == "VERIFIED_ANSWER":
            if (
                outcome.output_status.value != "VERIFIED_ANSWER"
                or outcome.verified_answer is None
                or packet.ordered_required_corrections
            ):
                raise ValidationFailure("STEP38_SUPPORTED_CASE_FORCED_CORRECTION")
        elif (
            case.expected_final_output.value == "HUMAN_REVIEW_REQUIRED"
            and (
                outcome.human_review is None
                or outcome.verified_answer is not None
                or outcome.output_status.value
                not in {"CONFIRMATION_REQUIRED", "HUMAN_REVIEW_REQUIRED"}
            )
        ):
            raise ValidationFailure("STEP38_GOLDEN_CASE_REVIEW_MISSING")
        return _GoldenCaseExecutionProof(
            proof_version=GOLDEN_CASE_EXECUTION_PROOF_VERSION,
            case=case,
            execution_class=execution_class,
            executed_question_digest=draft_request.original_query_digest,
            executed_knowledge_as_of=case.knowledge_as_of.isoformat(),
            executed_source_ids=tuple(dict.fromkeys(source_ids)),
            executed_provision_ids=tuple(
                dict.fromkeys(str(item["provision_identifier"]) for item in metadatas)
            ),
            executed_version_ids=tuple(
                dict.fromkeys(str(item["version_identity"]) for item in metadatas)
            ),
            routing_input_hash=routing_input.input_hash,
            route_hash=selected_route.route_hash,
            policy_result_hash=policy.policy_result_hash,
            step20_outcome_hash=step20.outcome_hash,
            temporal_result_hash=temporal.result_hash,
            draft_v1_request_hash=draft_request.request_hash,
            final_outcome_hash=outcome.outcome_hash,
            execution_result_hash=outcome.outcome_hash,
            executed_route=selected_route.knowledge_route.value,
            executed_evidence_status=temporal.evidence_status.value,
            executed_final_output=(
                "HUMAN_REVIEW_REQUIRED"
                if outcome.human_review is not None
                else outcome.output_status.value
            ),
            actual_outcome_status=outcome.output_status.value,
            step26_invoked=True,
            required_correction_count=len(
                packet.ordered_required_corrections
            ),
            review_result_hash=(
                None if outcome.human_review is None else outcome.human_review.result_hash
            ),
            verified_answer_hash=(
                None
                if outcome.verified_answer is None
                else outcome.verified_answer.answer_hash
            ),
        )

    supported_case = suite.case("supported-entry-into-force-clean")
    temporal_case = suite.case("temporal-unavailable-edge")
    conflict_case = suite.case("conflicting-ceiling-edge")
    route_case = suite.case("non-german-law-route")
    supported = finalize_hat_case(
        supported_case,
        metadatas=(
            metadata(
                document=REAL_OFFICIAL_IDENTIFIER,
                version=REAL_VERSION_IDENTITY,
                provision="III.",
                official_identifier=REAL_OFFICIAL_IDENTIFIER,
            ),
        ),
        contents=(PROVISION_III_EXACT_TEXT,),
        source_ids=(REAL_SOURCE_ID,),
        draft_text="Diese Anordnung tritt am 1. Januar 2024 in Kraft.",
        execution_class=(
            "EXACT_REAL_SOURCE_BYTES_DETERMINISTIC_PROVIDER_FIXTURE"
        ),
    )
    temporal = finalize_hat_case(
        temporal_case,
        metadatas=(
            metadata(
                document=REAL_OFFICIAL_IDENTIFIER,
                version=REAL_VERSION_IDENTITY,
                provision="I.",
                official_identifier=REAL_OFFICIAL_IDENTIFIER,
            ),
        ),
        contents=(PROVISION_I_EXACT_TEXT,),
        source_ids=(REAL_SOURCE_ID,),
        draft_text="Für den historischen Stichtag ist die Beleglage unzureichend.",
        execution_class="SYNTHETIC_FAIL_CLOSED_CONTRACT_FIXTURE",
    )
    conflict = finalize_hat_case(
        conflict_case,
        metadatas=(
            metadata(
                document="synthetic-step38-conflict",
                version="step38-conflict-a",
                provision="synthetic-I",
                official_identifier="synthetic-step38-conflict",
            ),
            metadata(
                document="synthetic-step38-conflict",
                version="step38-conflict-b",
                provision="synthetic-I",
                official_identifier="synthetic-step38-conflict",
            ),
        ),
        contents=("Die Obergrenze ist A 14.", "Die Obergrenze ist A 15."),
        source_ids=(
            "synthetic-step38-conflict",
            "synthetic-step38-conflict",
        ),
        draft_text="Die Obergrenze ist A 15.",
        execution_class="SYNTHETIC_FAIL_CLOSED_CONTRACT_FIXTURE",
    )
    routing_input, selected_route, policy = route_and_policy(route_case)
    if policy.answer_status.value != "PASS_THROUGH_RESULT":
        raise ValidationFailure("STEP38_ROUTE_NEGATIVE_POLICY_MISMATCH")
    route_only = _GoldenCaseExecutionProof(
        proof_version=GOLDEN_CASE_EXECUTION_PROOF_VERSION,
        case=route_case,
        execution_class="DETERMINISTIC_ROUTE_ONLY",
        executed_question_digest=hashlib.sha256(
            routing_input.normalized_query_or_subject.encode("utf-8")
        ).hexdigest(),
        executed_knowledge_as_of=route_case.knowledge_as_of.isoformat(),
        executed_source_ids=(),
        executed_provision_ids=(),
        executed_version_ids=(),
        routing_input_hash=routing_input.input_hash,
        route_hash=selected_route.route_hash,
        policy_result_hash=policy.policy_result_hash,
        step20_outcome_hash=None,
        temporal_result_hash=None,
        draft_v1_request_hash=None,
        final_outcome_hash=None,
        execution_result_hash=policy.policy_result_hash,
        executed_route=selected_route.knowledge_route.value,
        executed_evidence_status=policy.evidence_status.value,
        executed_final_output="PASS_THROUGH",
        actual_outcome_status=policy.answer_status.value,
        step26_invoked=False,
        required_correction_count=None,
        review_result_hash=None,
        verified_answer_hash=None,
    )
    return supported, temporal, conflict, route_only


def _golden_case_outcome_proofs(suite: Any) -> Mapping[str, Any]:
    """Return hash-only projections of exact named-case executions."""

    executed = _execute_named_golden_case_proofs(suite)
    primary_case = suite.case("primary-entry-into-force")
    backup_case = suite.case(BACKUP_SPECIAL_CASE_ID)
    result: dict[str, Any] = {
        primary_case.case_id: {
            "fixture_class": primary_case.fixture_class.value,
            "case_hash": primary_case.case_hash,
            "question_digest": primary_case.question_digest,
            "execution": "LIVE_REAL_PROVIDER_PRIMARY_ATTEMPT",
        },
        backup_case.case_id: {
            "fixture_class": backup_case.fixture_class.value,
            "case_hash": backup_case.case_hash,
            "question_digest": backup_case.question_digest,
            "execution": "LIVE_REAL_PROVIDER_FALLBACK_IF_PRIMARY_HAS_NO_DEFECT",
        },
    }
    result.update({proof.case.case_id: proof.public_mapping() for proof in executed})
    result["proof_digest"] = canonical_sha256(
        {
            "suite_hash": suite.suite_hash,
            "executed_case_proof_hashes": tuple(
                proof.proof_hash for proof in executed
            ),
            "provider_case_hashes": (
                primary_case.case_hash,
                backup_case.case_hash,
            ),
        }
    )
    return result


def _real_corpus_preflight(
    args: argparse.Namespace,
) -> tuple[Mapping[str, Any], Mapping[str, str], Any]:
    fixture_args = SimpleNamespace(
        step14_bundle_root=args.step14_bundle_root,
        step15_bundle_root=args.step15_bundle_root,
        step16_bundle_root=args.step16_bundle_root,
        source_root=args.source_root,
    )
    item, first, _candidate = step18._real_fixture(fixture_args)
    provision_path = (
        args.source_root.resolve()
        / item["alias_provisions_relative_paths"][0]
    )
    provisions = {
        "I.": first,
        "II.": step18._jsonl_first(
            provision_path,
            lambda value: value.get("provision_identifier") == "II.",
        ),
        "III.": step18._jsonl_first(
            provision_path,
            lambda value: value.get("provision_identifier") == "III.",
        ),
    }
    exact_text: dict[str, str] = {}
    provision_hashes: dict[str, str] = {}
    for identifier, provision in provisions.items():
        text = provision.get("official_text_de")
        digest = provision.get("content_sha256")
        if (
            not isinstance(text, str)
            or not text
            or not isinstance(digest, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest
        ):
            raise ValidationFailure("STEP38_REAL_SOURCE_BYTE_HASH_MISMATCH")
        exact_text[identifier] = text
        provision_hashes[identifier] = digest
    expected_provision_hashes = {
        identifier: REAL_PROVISION_HASHES[identifier]
        for identifier in ("I.", "II.", "III.")
    }
    if (
        provision_hashes != expected_provision_hashes
        or provision_hashes["II."] != PROVISION_II_SHA256
        or exact_text["II."] != PROVISION_II_EXACT_TEXT
        or provision_hashes["III."] != PROVISION_III_SHA256
        or exact_text["III."] != PROVISION_III_EXACT_TEXT
    ):
        raise ValidationFailure("STEP38_REAL_PROVISION_III_IDENTITY_MISMATCH")
    temporal_projection = project_bmjernano_temporal_facts(exact_text["III."])
    if not (
        item.get("source_id") == REAL_SOURCE_ID
        and item.get("official_identifier") == REAL_OFFICIAL_IDENTIFIER
        and item.get("version_identity") == REAL_VERSION_IDENTITY
        and item.get("state") == "PUBLISHED"
    ):
        raise ValidationFailure("STEP38_REAL_CORPUS_IDENTITY_MISMATCH")
    return (
        {
            "classification": "REAL_GERMAN_LAW_CORPUS_FIXTURE",
            "source_id": REAL_SOURCE_ID,
            "official_identifier": REAL_OFFICIAL_IDENTIFIER,
            "version_identity": REAL_VERSION_IDENTITY,
            "publication_state": item.get("state"),
            "publication_item_digest": item.get("publication_item_digest"),
            "step14_manifest_digest": step18.EXPECTED_STEP14_DIGEST,
            "step15_manifest_digest": step18.EXPECTED_STEP15_DIGEST,
            "step16_manifest_digest": step18.EXPECTED_STEP16_DIGEST,
            "provision_content_sha256": provision_hashes,
            "exact_source_byte_hashes_verified": True,
            "temporal_projection": {
                "receipt_hash": temporal_projection.receipt_hash,
                "effective_from": temporal_projection.effective_from_date,
                "exact_source_span_sha256": (
                    temporal_projection.exact_source_span_sha256
                ),
                "projection_method": temporal_projection.projection_method,
                "fixture_bound": temporal_projection.fixture_bound,
                "preexisting_temporal_metadata_used": (
                    temporal_projection.preexisting_temporal_metadata_used
                ),
                "model_inference_used": temporal_projection.model_inference_used,
                "canonical_evidence_authority": (
                    temporal_projection.canonical_evidence_authority
                ),
            },
            "source_writes": 0,
        },
        exact_text,
        temporal_projection,
    )


def _prepare_live_embedding_runtime(
    args: argparse.Namespace,
    provider_credential: SecretValue,
) -> tuple[Any, ...]:
    """Enter an already-pinned E5 runtime without installing or downloading."""

    import run_step19_embedding_vector_validation as step19
    from aioa_memory_kernel.embeddings import PassageEmbeddingCache
    from aioa_memory_kernel.embeddings.local_e5 import LocalE5Backend

    if (
        not isinstance(provider_credential, SecretValue)
        or provider_credential.purpose is not CredentialPurpose.MODEL_PROVIDER
    ):
        raise ValidationFailure("STEP38_REAL_MODEL_VALIDATION_REQUIRED")
    adapter, config, external_facts = step19._external_runtime(args.external_env)
    runtime_root = step19._safe_external_directory(
        config.data_root,
        step19.RUNTIME_RELATIVE,
    )
    step19._safe_external_directory(config.data_root, step19.HF_RELATIVE)
    step19._safe_external_directory(config.data_root, step19.PIP_RELATIVE)
    runtime_python = runtime_root / "bin/python"
    if not runtime_python.is_file():
        raise ValidationFailure("STEP38_PINNED_EMBEDDING_RUNTIME_REQUIRED")
    if Path(sys.prefix).resolve() != runtime_root.resolve(strict=True):
        environment = _minimal_openrouter_reexec_environment(
            provider_credential
        )
        environment.update(
            {
                "STEP38_ISOLATED_RUNTIME": "1",
                "HF_HOME": str(config.data_root / step19.HF_RELATIVE),
                "HF_HUB_CACHE": str(config.data_root / step19.HF_RELATIVE / "hub"),
                "PIP_CACHE_DIR": str(config.data_root / step19.PIP_RELATIVE),
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_DISABLE_XET": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        os.execve(
            str(runtime_python),
            [str(runtime_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            environment,
        )
    os.environ.update(
        {
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
        }
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        versions = step19._runtime_versions()
        model_root = step19._safe_external_directory(
            config.data_root,
            step19.MODEL_RELATIVE,
        )
        required_model_files = {
            "config.json",
            "model.safetensors",
            "sentencepiece.bpe.model",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        present_model_files = {
            path.name
            for path in model_root.iterdir()
            if path.is_file() and not path.is_symlink()
        }
        if not required_model_files.issubset(present_model_files):
            raise ValidationFailure("STEP38_PINNED_LOCAL_E5_SNAPSHOT_REQUIRED")
        step19.verify_model_snapshot(model_root)
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        backend = LocalE5Backend(model_root)
    cache = PassageEmbeddingCache(adapter)
    return (
        config,
        backend,
        cache,
        {
            "runtime_packages_digest": canonical_sha256(versions),
            "model_digest": backend.identity().model_digest,
            "model_file_count": len(backend.verified_files),
            "external_volume_verified": all(bool(value) for value in external_facts.values()),
            "embedding_network_access": False,
            "runtime_or_model_bootstrap_performed": False,
        },
    )


def _resolve_real_temporal_lineage(
    *,
    route: Any,
    outcome: Any,
    receipt: Any,
    knowledge_as_of: Any,
    expected_evidence_status: Any,
) -> Any:
    """Run Step 21 over the exact Step 20 result returned by the DB adapter."""

    bundle = outcome.bundle
    if bundle is None or not bundle.ordered_items:
        raise ValidationFailure("STEP38_REAL_STEP20_BUNDLE_MISSING")
    source_kinds = tuple(sorted({item.source_kind for item in bundle.ordered_items}))
    freshness = FreshnessPolicy(
        policy_id="step38-german-law-real-corpus-freshness-1a",
        policy_version="1",
        maximum_age_seconds_by_source_kind={
            value: 30 * 24 * 60 * 60 for value in source_kinds
        },
    )
    service = TemporalResolutionService()
    request = service.prepare_request(
        route=route,
        step20_outcome=outcome,
        temporal_mode=TemporalQueryMode.AS_OF,
        knowledge_as_of=knowledge_as_of,
        clock=SimpleNamespace(now=lambda: knowledge_as_of),
        availability=EvidenceAvailability.AVAILABLE,
        freshness_policy=freshness,
    )
    temporal = service.resolve(request)
    if (
        temporal.evidence_status is not expected_evidence_status
        or temporal.step20_outcome_hash != outcome.outcome_hash
        or temporal.step20_bundle_hash != bundle.bundle_hash
        or not temporal.resolved_item_hashes
        or any(
            item.structured_metadata.get(
                "step38_temporal_projection_receipt_hash"
            )
            != receipt.receipt_hash
            for item in bundle.ordered_items
        )
    ):
        raise ValidationFailure("STEP38_REAL_TEMPORAL_LINEAGE_INVALID")
    return temporal


def _provider_input_evidence(
    evidence_provider: EvidenceBoundDraftV2Provider,
    *,
    required: bool,
) -> tuple[Mapping[str, Any], Any | None]:
    """Return hash-only evidence for the exact augmented Draft V2 input."""

    receipts = evidence_provider.input_receipts
    if len(receipts) > 1:
        raise ValidationFailure("STEP38_AUGMENTED_PROVIDER_RECEIPT_COUNT_INVALID")
    if not receipts:
        if required:
            raise ValidationFailure("STEP38_AUGMENTED_PROVIDER_RECEIPT_MISSING")
        return {"augmented_provider_input_recorded": False}, None
    receipt = receipts[0]
    return (
        {
            "augmented_provider_input_recorded": True,
            "base_provider_request_hash": receipt.base_provider_request_hash,
            "augmented_provider_request_hash": (
                receipt.augmented_provider_request_hash
            ),
            "evidence_context_hash": receipt.evidence_context_hash,
            "correction_packet_hash": receipt.correction_packet_hash,
            "draft_v2_target_projection_hash": (
                receipt.draft_v2_target_projection_hash
            ),
            "provider_identity_digest": receipt.provider_identity_digest,
            "augmented_user_content_sha256": (
                receipt.augmented_user_content_sha256
            ),
            "provider_response_hash": receipt.provider_response_hash,
            "provider_purpose": receipt.provider_purpose,
            "augmented_provider_input_receipt_hash": receipt.receipt_hash,
            "raw_augmented_input_recorded": False,
        },
        receipt,
    )


def _required_correction_claim_ids(packet: object) -> tuple[str, ...]:
    """Project the exact Step 23 claim identities corrected by Step 24."""

    return tuple(
        value.claim_id
        for value in packet.ordered_required_corrections
    )


def _backup_special_case_draft_classification(
    case: GermanLawGoldenCase,
    draft_text: str,
) -> str | None:
    """Classify only the two complete polarity answers allowed by the fixture.

    The provider prompt does not disclose which completion is canonical.  This
    post-generation gate merely prevents a placeholder, a one-word answer, or
    surrounding prose from being mistaken for a material, evidence-correctable
    Draft V1 defect.
    """

    if case.case_id != BACKUP_SPECIAL_CASE_ID:
        return None
    if draft_text == PROVISION_II_EXACT_TEXT:
        return BACKUP_DRAFT_CORRECT_EXACT
    if draft_text == PROVISION_II_WRONG_POLARITY_TEXT:
        return BACKUP_DRAFT_WRONG_EXACT
    return BACKUP_DRAFT_INVALID


def _prepare_real_draft_v1_request(temporal: Any, case: GermanLawGoldenCase) -> Any:
    """Build a bounded evidence-blind response shape for the selected real case."""

    request = prepare_model_generation_request(temporal, case.question)
    if case.case_id == "primary-entry-into-force":
        prompt = PromptTemplate(
            template_id="step38-draft-v1-evidence-blind-german-date-1a",
            template_version="1",
            system_instruction=(
                "Beantworte die Frage ausschließlich aus deinem eigenen "
                "Modellwissen und ohne Quellen oder Werkzeuge. Antworte auf "
                "Deutsch in genau einem kurzen, eigenständigen Satz, der nur "
                "das Datum des Inkrafttretens nennt. Verwende keine Überschrift, "
                "kein Markdown, keine Zitate, keine Paragraphen, keine "
                "Erläuterung, keine Abkürzungsauflösung und keine Einschränkung."
            ),
        )
    elif case.case_id == BACKUP_SPECIAL_CASE_ID:
        prompt = PromptTemplate(
            template_id="step38-draft-v1-evidence-blind-german-reservation-1a",
            template_version="1",
            system_instruction=(
                "Beantworte die Frage ausschließlich aus deinem eigenen "
                "Modellwissen und ohne Quellen oder Werkzeuge. Gib ausschließlich "
                "den gesamten, vollständig ausgefüllten deutschen Satz aus, "
                "einschließlich aller Wörter vor und nach der Lücke. Der "
                "ausgegebene Satz darf weder einen Platzhalter noch eine "
                "Einzelwortantwort enthalten. Verwende keine Überschrift, kein "
                "Markdown, keine Anführungszeichen, keine Quellenangabe, keine "
                "Erläuterung und keine zusätzliche Aussage."
            ),
        )
    else:
        raise ValidationFailure("STEP38_REAL_PROVIDER_CASE_UNSUPPORTED")
    return replace(request, prompt_template=prompt)


def _real_provider_flow(
    retrieval: Any,
    temporal: Any,
    provider_credential: SecretValue,
) -> tuple[Mapping[str, Any], Step38VerifiedUpstreamLineage | None]:
    """Run exact Steps 22-26 while retaining a typed private lineage."""

    spec = _step38_openrouter_spec()
    input_value = retrieval.retrieval_input
    post_policy = build_step38_post_retrieval_policy_receipt(
        input_value.routing_input,
        input_value.route,
        input_value.policy_context,
        input_value.policy_result,
        temporal,
    )
    policy = post_policy.final_policy_result
    if (
        policy.evidence_status is not temporal.evidence_status
        or policy.knowledge_policy_decision.value != "ALLOW_ANSWER"
    ):
        return (
            {
                "status": "BLOCKED",
                "reason": "STEP38_POST_RETRIEVAL_POLICY_REEVALUATION_REQUIRED",
                "provider_id": spec.provider_id,
                "model_id": spec.model_id,
                "provider_material_recorded": False,
                "provider_calls": 0,
                "policy_result_hash": policy.policy_result_hash,
            },
            None,
        )
    if (
        not isinstance(provider_credential, SecretValue)
        or provider_credential.purpose is not CredentialPurpose.MODEL_PROVIDER
    ):
        return (
            {
                "status": "BLOCKED",
                "reason": "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
                "provider_id": spec.provider_id,
                "model_id": spec.model_id,
                "provider_material_recorded": False,
                "provider_calls": 0,
            },
            None,
        )
    provider = OpenRouterDraftV1Adapter(provider_credential, spec=spec)
    case = retrieval.retrieval_input.golden_case
    outcome = retrieval.hybrid_outcome
    bundle = outcome.bundle
    if bundle is None:
        raise ValidationFailure("STEP38_REAL_STEP20_BUNDLE_MISSING")
    request = _prepare_real_draft_v1_request(temporal, case)
    blindness = prove_draft_v1_evidence_blind(request, case.question)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            draft_receipt = DraftV1Service(provider).generate(request)
    except ModelAdapterError as error:
        return (
            {
                "status": "BLOCKED",
                "reason": "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
                "provider_reason_code": error.reason_code.value,
                "provider_id": spec.provider_id,
                "model_id": spec.model_id,
                "provider_material_recorded": False,
                "provider_calls": "BOUNDED_BY_APPROVED_ATTEMPT_POLICY",
            },
            None,
        )
    draft = draft_receipt.draft
    draft_shape_classification = _backup_special_case_draft_classification(
        case,
        draft.draft_text,
    )
    if draft_shape_classification == BACKUP_DRAFT_INVALID:
        return (
            {
                "status": "BLOCKED",
                "reason": "STEP38_BACKUP_RESPONSE_SHAPE_INVALID",
                "provider_id": spec.provider_id,
                "model_id": spec.model_id,
                "provider_material_recorded": False,
                "case_attempts": [
                    {
                        "case_id": case.case_id,
                        "question_digest": case.question_digest,
                        "draft_v1_hash": draft.draft_hash,
                        "draft_v1_text_sha256": draft.draft_text_sha256,
                        "draft_v1_byte_length": draft.draft_byte_length,
                        "draft_shape_classification": (
                            draft_shape_classification
                        ),
                        "required_correction_count": None,
                        "defect_claim_ids": (),
                        "citation_count": None,
                        "packet_hash": None,
                    }
                ],
            },
            None,
        )
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(
        prepare_claim_binding_request(draft, (bundle,), temporal)
    )
    packet = build_correction_packet(snapshot)
    attempt: dict[str, Any] = {
        "case_id": case.case_id,
        "question_digest": case.question_digest,
        "draft_v1_hash": draft.draft_hash,
        "draft_v1_text_sha256": draft.draft_text_sha256,
        "draft_v1_byte_length": draft.draft_byte_length,
        "required_correction_count": len(packet.ordered_required_corrections),
        "defect_claim_ids": _required_correction_claim_ids(packet),
        "citation_count": len(packet.ordered_citations),
        "packet_hash": packet.packet_hash,
    }
    if draft_shape_classification is not None:
        attempt["draft_shape_classification"] = draft_shape_classification
    if not packet.ordered_required_corrections or not packet.ordered_citations:
        no_defect_reason = (
            "STEP38_PRIMARY_DEFECT_NOT_OBSERVED_BACKUP_REQUIRED"
            if case.case_kind.value == "PRIMARY"
            else "STEP38_BACKUP_DEFECT_NOT_OBSERVED"
        )
        return (
            {
                "status": "BLOCKED",
                "reason": no_defect_reason,
                "provider_id": spec.provider_id,
                "model_id": spec.model_id,
                "provider_material_recorded": False,
                "case_attempts": [attempt],
            },
            None,
        )
    context = build_evidence_bound_correction_context(
        snapshot,
        (bundle,),
        packet,
    )
    try:
        target_projection = build_draft_v2_target_projection(packet, context)
    except ContractValidationError:
        no_exact_correction_reason = (
            "STEP38_PRIMARY_EXACT_CORRECTION_NOT_OBSERVED_BACKUP_REQUIRED"
            if case.case_kind.value == "PRIMARY"
            else "STEP38_BACKUP_EXACT_CORRECTION_NOT_OBSERVED"
        )
        return (
            {
                "status": "BLOCKED",
                "reason": no_exact_correction_reason,
                "provider_id": spec.provider_id,
                "model_id": spec.model_id,
                "provider_material_recorded": False,
                "case_attempts": [attempt],
            },
            None,
        )
    authenticator = HmacSha256PacketAuthenticator(
        key_id="step38-synthetic-validation-key",
        key_material=SYNTHETIC_PACKET_KEY_MATERIAL,
    )
    integrity_receipt = authenticator.authenticate(packet)
    draft_v2_request = prepare_draft_v2_generation_request(
        draft,
        packet,
        integrity_receipt,
        authenticator,
    )
    evidence_provider = EvidenceBoundDraftV2Provider(
        provider,
        packet,
        context,
        target_projection,
    )
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            pipeline = DraftV2Service(
                evidence_provider,
                authenticator,
                verifier=DraftV2LayeredVerifier(
                    corrected_evidence_verifier=CanonicalEvidenceExactVerifier(
                        context
                    ),
                ),
            ).generate_and_verify(draft_v2_request)
    except ModelAdapterError as error:
        provider_input, _provider_input_receipt = _provider_input_evidence(
            evidence_provider,
            required=False,
        )
        return {
            "status": "BLOCKED",
            "reason": "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
            "provider_reason_code": error.reason_code.value,
            "provider_id": spec.provider_id,
            "model_id": spec.model_id,
            "provider_material_recorded": False,
            "case_attempts": [attempt],
            "selected_case_id": case.case_id,
            "draft_v1_hash": draft.draft_hash,
            "correction_packet_hash": packet.packet_hash,
            "evidence_context_hash": context.context_hash,
            "provider_input": provider_input,
        }, None
    provider_input, provider_input_receipt = _provider_input_evidence(
        evidence_provider,
        required=True,
    )
    if pipeline.verification_summary.summary_status is not VerificationSummaryStatus.VERIFIED:
        return {
            "status": "BLOCKED",
            "reason": "STEP38_REAL_DRAFT_V2_NOT_VERIFIED",
            "provider_id": spec.provider_id,
            "model_id": spec.model_id,
            "provider_material_recorded": False,
            "case_attempts": [attempt],
            "selected_case_id": case.case_id,
            "draft_v1_hash": draft.draft_hash,
            "correction_packet_hash": packet.packet_hash,
            "evidence_context_hash": context.context_hash,
            "draft_v2_hash": pipeline.draft_v2.draft_v2_hash,
            "verification_summary_hash": pipeline.verification_summary.summary_hash,
            "verification_status": pipeline.verification_summary.summary_status.value,
            "provider_input": provider_input,
        }, None
    if provider_input_receipt is None:
        raise ValidationFailure("STEP38_AUGMENTED_PROVIDER_RECEIPT_MISSING")
    corrected_proofs = _corrected_proof_evidence(pipeline)

    final_request = FinalAnswerRequest(
        route=retrieval.retrieval_input.route,
        policy_result=policy,
        step20_outcomes=(outcome,),
        temporal_result=temporal,
        draft_v1=draft,
        correction_packet=packet,
        integrity_receipt=integrity_receipt,
        step25_result=pipeline,
    )
    final = VerifiedAnswerService(authenticator).finalize(final_request)
    if final.output_status is not FinalOutputStatus.VERIFIED_ANSWER:
        return {
            "status": "BLOCKED",
            "reason": "STEP38_REAL_VERIFIED_ANSWER_NOT_RETURNED",
            "provider_id": spec.provider_id,
            "model_id": spec.model_id,
            "provider_material_recorded": False,
            "case_attempts": [attempt],
            "selected_case_id": case.case_id,
            "output_status": final.output_status.value,
            "outcome_hash": final.outcome_hash,
        }, None
    trace = build_before_after_trace(
        case,
        draft,
        packet,
        pipeline,
        final,
        context,
        provider_input_receipt,
    )
    upstream = Step38VerifiedUpstreamLineage(
        lineage_version=STEP38_UPSTREAM_LINEAGE_VERSION,
        golden_case=case,
        route=retrieval.retrieval_input.route,
        post_retrieval_policy_receipt=post_policy,
        step20_outcome=outcome,
        temporal_result=temporal,
        draft_v1=draft,
        packet_input_snapshot=snapshot,
        correction_packet=packet,
        step25_result=pipeline,
        final_answer_request=final_request,
        final_outcome=final,
        evidence_context=context,
        provider_input_receipt=provider_input_receipt,
        before_after_trace=trace,
    )
    generation = draft_receipt.generation_result
    public = {
        "status": "PASS_REAL_VERIFIED_LINEAGE",
        "closure_authority": True,
        "classification": "REAL_MODEL_WITH_REAL_STEP18_21_EVIDENCE_LINEAGE",
        "provider_id": spec.provider_id,
        "model_id": spec.model_id,
        "model_declared_version": spec.model_declared_version,
        "provider_identity_digest": spec.provider_identity().identity_digest,
        "provider_material_recorded": False,
        "selected_case_id": case.case_id,
        "case_attempts": [attempt],
        "route_hash": retrieval.retrieval_input.route.route_hash,
        "evidence_bundle_hash": bundle.bundle_hash,
        "temporal_result_hash": temporal.result_hash,
        "post_retrieval_policy_receipt_hash": post_policy.receipt_hash,
        "temporal_projection_receipt_hash": (
            retrieval.temporal_projection_receipt.receipt_hash
        ),
        "claim_snapshot_hash": snapshot.snapshot_hash,
        "correction_packet_hash": packet.packet_hash,
        "evidence_context_hash": context.context_hash,
        "draft_v1_hash": draft.draft_hash,
        "draft_v1_text_sha256": draft.draft_text_sha256,
        "draft_v1_byte_length": draft.draft_byte_length,
        "draft_v1_prompt_template_digest": draft.prompt_template_digest,
        "draft_v1_generation_parameters_digest": (
            draft.generation_parameters_digest
        ),
        "draft_v1_model_version": draft.model_version,
        "draft_v1_evidence_blind": not blindness.evidence_fields_projected,
        "original_query_exactly_matched": blindness.expected_query_matched,
        "draft_v1_provider_attempt_count": (
            generation.attempt_count if generation is not None else None
        ),
        "exact_source_span_hashes": tuple(
            item.identity.content_sha256 for item in bundle.ordered_items
        ),
        "draft_v2_request_hash": draft_v2_request.request_hash,
        "provider_input": provider_input,
        "corrected_evidence": corrected_proofs,
        "draft_v2_hash": pipeline.draft_v2.draft_v2_hash,
        "draft_v2_prompt_template_digest": (
            pipeline.draft_v2.prompt_template_digest
        ),
        "draft_v2_generation_parameters_digest": (
            pipeline.draft_v2.generation_parameters_digest
        ),
        "draft_v2_model_version": pipeline.draft_v2.model_version,
        "verification_summary_hash": pipeline.verification_summary.summary_hash,
        "verified_answer_hash": final.verified_answer.answer_hash,
        "before_after_trace_hash": trace.trace_hash,
        "upstream_lineage_hash": upstream.lineage_hash,
        "raw_model_text_recorded": False,
    }
    return public, upstream


def _runtime_cleanup_is_complete(value: Mapping[str, Any] | None) -> bool:
    return isinstance(value, Mapping) and all(
        value.get(name) is expected
        for name, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    )


def _retrieval_evidence(artifacts: Any, temporal: Any) -> Mapping[str, Any]:
    """Project one real retrieval pass without source text or DB identity."""

    bundle = artifacts.hybrid_outcome.bundle
    if bundle is None:
        raise ValidationFailure("STEP38_REAL_STEP20_BUNDLE_MISSING")
    proof = artifacts.attestation
    return {
        "status": "PASS_REAL_SAME_DATABASE_RETRIEVAL",
        "retrieval_input_kind": proof.retrieval_input_kind,
        "artifacts_hash": artifacts.artifacts_hash,
        "input_hash": artifacts.retrieval_input.input_hash,
        "question_digest": hashlib.sha256(
            artifacts.retrieval_input.question_text.encode("utf-8")
        ).hexdigest(),
        "route_hash": artifacts.retrieval_input.route.route_hash,
        "hat_id": artifacts.retrieval_input.route.selected_hat_id,
        "hat_version": artifacts.retrieval_input.route.selected_hat_version,
        "hat_manifest_digest": (
            artifacts.retrieval_input.route.selected_manifest_digest
        ),
        "hat_scope_id": REAL_HAT_SCOPE_ID,
        "step18_result_hashes": tuple(
            result.result_hash for _request, result in artifacts.lexical_inputs
        ),
        "step18_candidate_count_by_mode": {
            request.retrieval_mode.value: len(result.candidates)
            for request, result in artifacts.lexical_inputs
        },
        "step19_embedding_result_hash": artifacts.embedding_result.result_hash,
        "step19_vector_result_hash": artifacts.vector_result.result_hash,
        "step20_outcome_hash": artifacts.hybrid_outcome.outcome_hash,
        "step20_bundle_hash": bundle.bundle_hash,
        "selected_candidate_identities": tuple(
            {
                "identity_hash": item.identity.identity_hash,
                "source_id": item.identity.source_id,
                "knowledge_version_id": item.identity.knowledge_version_id,
                "chunk_id": item.identity.chunk_id,
                "content_sha256": item.identity.content_sha256,
                "authority_level": item.authority_level.value,
                "publication_state": item.publication_state.value,
            }
            for item in bundle.ordered_items
        ),
        "step21_result_hash": temporal.result_hash,
        "temporal_projection_receipt_hash": (
            artifacts.temporal_projection_receipt.receipt_hash
        ),
        "adapter_attestation_hash": proof.attestation_hash,
        "data_plane_purpose": (
            proof.data_plane_credential_purpose.value
        ),
        "cross_tenant_rls_visible_count": proof.cross_tenant_rls_visible_count,
        "negative_source_leak_count": proof.negative_source_leak_count,
        "approved_local_e5_backend": proof.approved_local_e5_backend,
        "same_database": proof.same_database,
        "raw_source_text_recorded": False,
    }


def _coherent_runtime_evidence(proof: Any) -> Mapping[str, Any]:
    """Return the hash-only Step 27-35 projection used for closure."""

    return {
        "status": (
            "PASS_REAL_COHERENT_PERSONAL_MEMORY_LINEAGE"
            if proof.closure_eligible
            else "BLOCKED_SAFELY_NOT_CLOSURE"
        ),
        "proof_hash": proof.proof_hash,
        "scenario_hash": proof.scenario_hash,
        "upstream_runtime_attestation_hash": (
            proof.upstream_runtime_attestation_hash
        ),
        "primary_retrieval_proof_hash": proof.primary_retrieval_proof_hash,
        "later_retrieval_proof_hash": proof.later_retrieval_proof_hash,
        "runtime_instance_digest": proof.runtime_instance_digest,
        "database_instance_digest": proof.database_instance_digest,
        "slot_hash": proof.slot_hash,
        "candidate_hash": proof.candidate_hash,
        "candidate_envelope_hash": proof.candidate_envelope_hash,
        "proposal_hash": proof.proposal_hash,
        "validation_receipt_hash": proof.validation_receipt_hash,
        "approval_receipt_hash": proof.approval_receipt_hash,
        "commit_receipt_hash": proof.commit_receipt_hash,
        "activation_receipt_hash": proof.activation_receipt_hash,
        "active_patch_hash": proof.active_patch_hash,
        "later_active_patch_retrieval_request_hash": (
            proof.later_active_patch_retrieval_request_hash
        ),
        "disallowed_model_retrieval_hash": (
            proof.disallowed_model_retrieval_hash
        ),
        "disallowed_model_access_denied": proof.disallowed_model_access_denied,
        "cross_user_approval_denial_hash": (
            proof.cross_user_approval_denial_hash
        ),
        "cross_user_approval_denied": proof.cross_user_approval_denied,
        "cross_user_export_denial_hash": proof.cross_user_export_denial_hash,
        "cross_user_export_denied": proof.cross_user_export_denied,
        "canonical_conflict_temporal_hash": (
            proof.canonical_conflict_temporal_hash
        ),
        "canonical_conflict_retrieval_hash": (
            proof.canonical_conflict_retrieval_hash
        ),
        "canonical_evidence_conflict_suppressed": (
            proof.canonical_evidence_conflict_suppressed
        ),
        "canonical_conflict_fixture_class": "SYNTHETIC_EDGE_CASE",
        "activation_ack_lost_recovery_hash": (
            proof.activation_ack_lost_recovery_hash
        ),
        "activation_ack_lost_recovery_status": (
            proof.activation_ack_lost_recovery_status
        ),
        "activation_ack_lost_recovered": proof.activation_ack_lost_recovered,
        "activation_recovery_observation_hash": (
            proof.activation_recovery_observation.observation_hash
        ),
        "activation_recovery_failure_point": (
            proof.activation_recovery_failure_point.value
        ),
        "duplicate_semantic_side_effect_count": (
            proof.duplicate_semantic_side_effect_count
        ),
        "authority_violation_count": proof.authority_violation_count,
        "integrity_violation_count": proof.integrity_violation_count,
        "model_a_identity_digest": proof.model_a_identity_digest,
        "model_a_fixture_class": "FAKE_SECOND_MODEL_IDENTITY",
        "model_a_retrieval_hash": proof.model_a_retrieval_hash,
        "model_a_context_hash": proof.model_a_context_hash,
        "model_b_identity_digest": proof.model_b_identity_digest,
        "model_b_fixture_class": "FAKE_SECOND_MODEL_IDENTITY",
        "model_b_retrieval_hash": proof.model_b_retrieval_hash,
        "model_b_context_hash": proof.model_b_context_hash,
        "cross_model_same_patch": proof.cross_model_same_patch,
        "real_second_model_inference_status": (
            proof.real_second_model_inference_status.value
        ),
        "audit_chain_id": proof.audit_chain_id,
        "audit_verification_hash": proof.audit_verification_hash,
        "audit_first_event_hash": proof.audit_first_event_hash,
        "audit_last_event_hash": proof.audit_last_event_hash,
        "audit_chain_verified": proof.audit_chain_verified,
        "approval_audit_event_hash": proof.approval_audit_event_hash,
        "commit_audit_event_hash": proof.commit_audit_event_hash,
        "activation_audit_event_hash": proof.activation_audit_event_hash,
        "lifecycle_audit_events_distinct": proof.lifecycle_audit_events_distinct,
        "audit_export_bundle_hash": proof.audit_export_bundle_hash,
        "audit_export_hash_only": proof.audit_export_hash_only,
        "shared_promotion_hash": proof.shared_promotion_hash,
        "review_case_type": proof.review_case_type.value,
        "review_case_hash": proof.review_case_hash,
        "review_detail_hash": proof.review_detail_hash,
        "review_context_verified": proof.review_context_verified,
        "ordinary_user_review_access_denied": (
            proof.ordinary_user_review_access_denied
        ),
        "ui_awaiting_dashboard_hash": proof.ui_awaiting_dashboard_hash,
        "ui_dashboard_hash": proof.ui_dashboard_hash,
        "ui_awaiting_state_verified": proof.ui_awaiting_state_verified,
        "ui_active_state_verified": proof.ui_active_state_verified,
        "ui_same_active_patch": proof.ui_same_active_patch,
        "ui_quota_verified": (
            proof.ui_awaiting_state_verified and proof.ui_active_state_verified
        ),
        "ui_model_bindings_verified": (
            proof.ui_awaiting_state_verified and proof.ui_active_state_verified
        ),
        "ui_audit_history_verified": (
            proof.ui_awaiting_state_verified and proof.ui_active_state_verified
        ),
        "cross_user_access_denied": proof.cross_user_access_denied,
        "cross_tenant_access_denied": proof.cross_tenant_access_denied,
        "canonical_evidence_authority": proof.canonical_evidence_authority,
        "source_publication_authority": proof.source_publication_authority,
        "external_execution_authority": proof.external_execution_authority,
        "real_retrieval_lineage": proof.real_retrieval_lineage,
        "later_real_retrieval_lineage": proof.later_real_retrieval_lineage,
        "upstream_and_downstream_same_database": (
            proof.upstream_and_downstream_same_database
        ),
        "step39_started": proof.step39_started,
        "closure_eligible": proof.closure_eligible,
        "raw_personal_memory_text_recorded": False,
    }


def _run_live_same_database(
    args: argparse.Namespace,
    suite: Any,
    step39_boundary: _Step39BoundaryScanProof,
    provider_credential: SecretValue,
) -> Mapping[str, Any]:
    """Own one migrated database for the entire real Step 18-35 lineage."""

    import run_cockroachdb_migrations as migrations
    import run_step19_embedding_vector_validation as step19
    import run_step27_personal_memory_validation as step27
    import run_step30_user_approval_commit_activation_validation as step30
    import step38_coherent_runtime as coherent_runtime
    import step38_real_retrieval as real_retrieval

    config, backend, cache, embedding_runtime = _prepare_live_embedding_runtime(
        args,
        provider_credential,
    )
    if OPENROUTER_KEY_ENVIRONMENT_NAME in os.environ:
        raise ValidationFailure("STEP38_PROVIDER_SECRET_AMBIENT_AFTER_REEXEC")
    source_binary = (
        args.cockroach_binary
        if args.cockroach_binary is not None
        else config.data_root
        / "cache/xdg/cockroachdb/v26.2.4/linux-amd64/server/"
        "cockroach-v26.2.4.linux-amd64/cockroach"
    ).expanduser().resolve(strict=True)
    source_identity = migrations.verify_binary_identity(source_binary)
    if source_identity["binary_sha256"] != step19.EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP38_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime = None
    root = None
    database = None
    cleanup: Mapping[str, Any] | None = None
    drop_succeeded = False
    data_plane_role: str | None = None
    data_plane_role_created = False
    runtime_started = False
    live_result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    with tempfile.TemporaryDirectory(
        prefix="mp-step38-one-lineage-binary-",
        dir="/tmp",
    ) as temporary:
        local_binary = Path(temporary) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if (
            migrations.verify_binary_identity(local_binary)["binary_sha256"]
            != step19.EXPECTED_COCKROACH_SHA256
        ):
            raise ValidationFailure("STEP38_COPIED_COCKROACH_DIGEST_MISMATCH")
        run_id = "mp_step38_e2e_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            _progress("OWNED_DISPOSABLE_COCKROACHDB_START")
            root = step18._start_disposable_runtime(runtime)
            runtime_started = True
            _progress("OWNED_DISPOSABLE_COCKROACHDB_REAL_SQL_READY", "PASS")
            _progress("VECTOR_INDEX_CAPABILITY_CHECK")
            current = migrations.one_value(
                root.execute(
                    "defaultdb",
                    "SHOW CLUSTER SETTING feature.vector_index.enabled",
                    timeout=60,
                )
            )
            if current != "t":
                root.execute(
                    "defaultdb",
                    "SET CLUSTER SETTING feature.vector_index.enabled = true",
                    timeout=60,
                )
            if migrations.one_value(
                root.execute(
                    "defaultdb",
                    "SHOW CLUSTER SETTING feature.vector_index.enabled",
                    timeout=60,
                )
            ) != "t":
                raise ValidationFailure("STEP38_VECTOR_INDEX_CAPABILITY_DISABLED")
            _progress("VECTOR_INDEX_CAPABILITY_CHECK", "PASS")
            database = run_id + "_db"
            _progress("OWNED_DISPOSABLE_DATABASE_CREATE")
            migrations.create_database(root, database)
            _progress("OWNED_DISPOSABLE_DATABASE_CREATE", "PASS")
            _progress("MIGRATE_AND_REPLAY_ONCE")
            applied = migrations.apply_migrations(root, database, timeout=300)
            replay = migrations.apply_migrations(root, database, timeout=300)
            expected = len(migrations.load_migrations())
            if (
                len(applied["applied"]) != expected
                or replay["applied"]
                or len(replay["skipped"]) != expected
            ):
                raise ValidationFailure("STEP38_MIGRATION_REPLAY_MISMATCH")
            security_catalog = migrations.assert_step36_security_catalog(
                root,
                database,
            )
            data_plane_role = "mp_s38_retrieval_" + uuid.uuid4().hex[:12]
            step27._create_validation_role(root, data_plane_role)
            data_plane_role_created = True
            data_plane_runner = step30._runner(
                port=root.sql_port,
                database=database,
                role=data_plane_role,
                credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
                diagnostic=True,
            )
            runtime_digest = canonical_sha256(
                {
                    "run_id": run_id,
                    "binary_sha256": source_identity["binary_sha256"],
                    "sql_port": runtime.sql_port,
                }
            )
            database_digest = canonical_sha256(
                {
                    "runtime_instance_digest": runtime_digest,
                    "database": database,
                }
            )
            runtime_public = {
                "cockroachdb_build_tag": source_identity["build_tag"],
                "cockroachdb_binary_sha256": source_identity["binary_sha256"],
                "migration_applied_count": len(applied["applied"]),
                "migration_replay_skipped_count": len(replay["skipped"]),
                "runtime_instance_digest": runtime_digest,
                "database_instance_digest": database_digest,
                "database_is_disposable": True,
            }
            corpus_roots = real_retrieval.Step38CorpusRoots(
                step14_bundle_root=args.step14_bundle_root,
                step15_bundle_root=args.step15_bundle_root,
                step16_bundle_root=args.step16_bundle_root,
                source_root=args.source_root,
            )
            retrieval_input = real_retrieval.build_canonical_primary_retrieval_input(
                suite,
                tenant_id="tenant-step38-golden",
                user_id="user-step38-owner-a",
                request_id="request-step38-primary-golden",
            )
            _progress("REAL_STEP17_20_RETRIEVAL_ON_OWNED_DATABASE")
            retrieval = real_retrieval.run_step38_real_retrieval_on_owned_database(
                retrieval_input,
                root=root,
                database=database,
                database_runner=data_plane_runner,
                data_plane_session_user=data_plane_role,
                runtime_instance_digest=runtime_digest,
                database_instance_digest=database_digest,
                corpus_roots=corpus_roots,
                embedding_backend=backend,
                embedding_cache=cache,
            )
            _progress("REAL_STEP21_TEMPORAL_RESOLUTION")
            case = retrieval.retrieval_input.golden_case
            temporal = _resolve_real_temporal_lineage(
                route=retrieval.retrieval_input.route,
                outcome=retrieval.hybrid_outcome,
                receipt=retrieval.temporal_projection_receipt,
                knowledge_as_of=case.knowledge_as_of,
                expected_evidence_status=case.expected_evidence_status,
            )
            _progress("APPROVED_PROVIDER_STEP22_25_OUTSIDE_TRANSACTION")
            provider_public, upstream = _real_provider_flow(
                retrieval,
                temporal,
                provider_credential,
            )
            retrieval_public = _retrieval_evidence(retrieval, temporal)
            initial_primary_retrieval_public = retrieval_public
            backup_retrieval_public: Mapping[str, Any] | None = None
            primary_attempts = tuple(provider_public.get("case_attempts", ()))
            if (
                upstream is None
                and provider_public.get("reason")
                in {
                    "STEP38_PRIMARY_DEFECT_NOT_OBSERVED_BACKUP_REQUIRED",
                    "STEP38_PRIMARY_EXACT_CORRECTION_NOT_OBSERVED_BACKUP_REQUIRED",
                }
            ):
                _progress("REAL_BACKUP_STEP17_20_RETRIEVAL_ON_OWNED_DATABASE")
                backup_input = (
                    real_retrieval.build_canonical_backup_retrieval_input(
                        retrieval.retrieval_input,
                        suite,
                        request_id="request-step38-backup-golden",
                    )
                )
                backup_retrieval = (
                    real_retrieval.run_step38_backup_retrieval_on_owned_database(
                        backup_input,
                        root=root,
                        database=database,
                        database_runner=data_plane_runner,
                        data_plane_session_user=data_plane_role,
                        runtime_instance_digest=runtime_digest,
                        database_instance_digest=database_digest,
                        corpus_roots=corpus_roots,
                        embedding_backend=backend,
                        embedding_cache=cache,
                    )
                )
                _progress("REAL_BACKUP_STEP21_TEMPORAL_RESOLUTION")
                backup_case = backup_input.golden_case
                backup_temporal = _resolve_real_temporal_lineage(
                    route=backup_input.route,
                    outcome=backup_retrieval.hybrid_outcome,
                    receipt=backup_retrieval.temporal_projection_receipt,
                    knowledge_as_of=backup_case.knowledge_as_of,
                    expected_evidence_status=(
                        backup_case.expected_evidence_status
                    ),
                )
                _progress("APPROVED_PROVIDER_BACKUP_STEP22_25_OUTSIDE_TRANSACTION")
                backup_provider_public, upstream = _real_provider_flow(
                    backup_retrieval,
                    backup_temporal,
                    provider_credential,
                )
                combined_attempts = primary_attempts + tuple(
                    backup_provider_public.get("case_attempts", ())
                )
                provider_public = dict(backup_provider_public)
                provider_public["case_attempts"] = combined_attempts
                provider_public["primary_case_attempted"] = True
                provider_public["backup_case_attempted"] = True
                retrieval = backup_retrieval
                temporal = backup_temporal
                backup_retrieval_public = _retrieval_evidence(
                    backup_retrieval,
                    backup_temporal,
                )
                retrieval_public = backup_retrieval_public
            else:
                provider_public = dict(provider_public)
                provider_public["primary_case_attempted"] = True
                provider_public["backup_case_attempted"] = False
            if upstream is None:
                live_result = {
                    "status": "BLOCKED_SAFELY_NOT_CLOSURE",
                    "closure_eligible": False,
                    "closure_block_reason": provider_public.get(
                        "reason",
                        "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
                    ),
                    "embedding_runtime": embedding_runtime,
                    "runtime": runtime_public,
                    "authority_catalog": security_catalog,
                    "retrieval": {
                        "primary_attempt": initial_primary_retrieval_public,
                        "backup_attempt": backup_retrieval_public,
                    },
                    "real_model_flow": provider_public,
                    "coherent_runtime": {"status": "NOT_RUN_AFTER_MODEL_BLOCK"},
                }
            else:
                primary_proof = real_retrieval.build_database_retrieval_proof(
                    retrieval
                )
                runtime_attestation = Step38UpstreamRuntimeAttestation(
                    attestation_version=(
                        STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION
                    ),
                    upstream_lineage_hash=upstream.lineage_hash,
                    retrieval_proof=primary_proof,
                )
                selected_later_question = (
                    real_retrieval.canonical_later_question(
                        retrieval.retrieval_input.golden_case
                    )
                )
                later_input = (
                    real_retrieval.build_canonical_related_retrieval_input(
                        retrieval.retrieval_input,
                        question_text=selected_later_question,
                        request_id="request-step38-later-related",
                    )
                )
                _progress("REAL_LATER_STEP17_20_RETRIEVAL_ON_OWNED_DATABASE")
                later_retrieval = (
                    real_retrieval.run_step38_related_retrieval_on_owned_database(
                        later_input,
                        root=root,
                        database=database,
                        database_runner=data_plane_runner,
                        data_plane_session_user=data_plane_role,
                        runtime_instance_digest=runtime_digest,
                        database_instance_digest=database_digest,
                        corpus_roots=corpus_roots,
                        embedding_backend=backend,
                        embedding_cache=cache,
                    )
                )
                _progress("REAL_LATER_STEP21_TEMPORAL_RESOLUTION")
                selected_case = retrieval.retrieval_input.golden_case
                later_temporal = _resolve_real_temporal_lineage(
                    route=later_input.route,
                    outcome=later_retrieval.hybrid_outcome,
                    receipt=later_retrieval.temporal_projection_receipt,
                    knowledge_as_of=selected_case.knowledge_as_of,
                    expected_evidence_status=(
                        selected_case.expected_evidence_status
                    ),
                )
                later_proof = real_retrieval.build_database_retrieval_proof(
                    later_retrieval
                )
                scenario = Step38PersonalMemoryScenario(
                    scenario_version=STEP38_PERSONAL_MEMORY_SCENARIO_VERSION,
                    upstream=upstream,
                    upstream_runtime_attestation=runtime_attestation,
                    later_route=later_input.route,
                    later_step20_outcome=later_retrieval.hybrid_outcome,
                    later_temporal_result=later_temporal,
                    later_retrieval_proof=later_proof,
                    later_query_digest=hashlib.sha256(
                        selected_later_question.encode("utf-8")
                    ).hexdigest(),
                )
                _progress("REAL_STEP27_35_COHERENT_LINEAGE_ON_OWNED_DATABASE")
                coherent_proof = (
                    coherent_runtime.run_coherent_lineage_on_owned_database(
                        scenario,
                        root=root,
                        database=database,
                        runtime_instance_digest=runtime_digest,
                        database_instance_digest=database_digest,
                        later_query_text=selected_later_question,
                        progress=_progress,
                    )
                )
                coherent_public = _coherent_runtime_evidence(coherent_proof)
                if (
                    coherent_proof.primary_retrieval_proof_hash
                    != primary_proof.proof_hash
                    or coherent_proof.later_retrieval_proof_hash
                    != later_proof.proof_hash
                    or coherent_proof.upstream_runtime_attestation_hash
                    != runtime_attestation.attestation_hash
                ):
                    raise ValidationFailure(
                        "STEP38_COHERENT_RUNTIME_PROOF_DETACHED"
                    )
                closure_eligible = _closure_eligible_with_boundary_scan(
                    coherent_proof,
                    step39_boundary,
                )
                live_result = {
                    "status": (
                        "PASS_LIVE_COHERENT_LINEAGE"
                        if closure_eligible
                        else "BLOCKED_SAFELY_NOT_CLOSURE"
                    ),
                    "closure_eligible": closure_eligible,
                    "embedding_runtime": embedding_runtime,
                    "runtime": runtime_public,
                    "authority_catalog": security_catalog,
                    "retrieval": {
                        "selected_initial_case": retrieval_public,
                        "primary_attempt": initial_primary_retrieval_public,
                        "backup_attempt": backup_retrieval_public,
                        "later_related": _retrieval_evidence(
                            later_retrieval,
                            later_temporal,
                        ),
                        "same_runtime_instance": (
                            primary_proof.runtime_instance_digest
                            == later_proof.runtime_instance_digest
                        ),
                        "same_database_instance": (
                            primary_proof.database_instance_digest
                            == later_proof.database_instance_digest
                        ),
                    },
                    "real_model_flow": provider_public,
                    "verified_upstream_lineage_hash": upstream.lineage_hash,
                    "upstream_runtime_attestation_hash": (
                        runtime_attestation.attestation_hash
                    ),
                    "coherent_runtime": coherent_public,
                    "security": {
                        "cross_user_access": (
                            not coherent_proof.cross_user_access_denied
                        ),
                        "cross_tenant_access": (
                            not coherent_proof.cross_tenant_access_denied
                        ),
                        "personal_memory_canonical_evidence": (
                            coherent_proof.canonical_evidence_authority
                        ),
                        "canonical_conflict_overridden": (
                            not coherent_proof.canonical_evidence_conflict_suppressed
                        ),
                        "source_publication_authority": (
                            coherent_proof.source_publication_authority
                        ),
                        "external_execution_authority": (
                            coherent_proof.external_execution_authority
                        ),
                        "authority_catalog_digest": canonical_sha256(
                            security_catalog
                        ),
                    },
                }
                if not closure_eligible:
                    live_result["closure_block_reason"] = (
                        "STEP38_COHERENT_RUNTIME_PROOF_NOT_CLOSURE_ELIGIBLE"
                    )
        except BaseException as error:
            primary_error = error
        finally:
            if (
                root is not None
                and data_plane_role is not None
                and data_plane_role_created
            ):
                try:
                    step27._drop_validation_role(root, data_plane_role)
                    data_plane_role_created = False
                except BaseException:
                    if primary_error is None:
                        primary_error = ValidationFailure(
                            "STEP38_DATA_PLANE_ROLE_CLEANUP_FAILED"
                        )
            if root is not None and database is not None:
                try:
                    migrations.drop_database(root, database, timeout=180)
                    drop_succeeded = True
                except BaseException:
                    if primary_error is None:
                        primary_error = ValidationFailure(
                            "STEP38_OWNED_DATABASE_DROP_FAILED"
                        )
            if runtime is not None and runtime.process is not None:
                runtime_started = True
                try:
                    cleanup = step18._stop_owned_runtime(runtime)
                except BaseException:
                    if primary_error is None:
                        primary_error = ValidationFailure(
                            "STEP38_OWNED_RUNTIME_CLEANUP_FAILED"
                        )
    cleanup_complete = _runtime_cleanup_is_complete(cleanup)
    if primary_error is not None:
        if (
            data_plane_role_created
            or
            (database is not None and not drop_succeeded)
            or (runtime_started and not cleanup_complete)
        ):
            raise ValidationFailure(
                "STEP38_OWNED_RUNTIME_CLEANUP_INCOMPLETE"
            ) from primary_error
        raise primary_error
    if live_result is None:
        raise ValidationFailure("STEP38_LIVE_RESULT_MISSING")
    if not drop_succeeded or not cleanup_complete:
        raise ValidationFailure("STEP38_OWNED_RUNTIME_CLEANUP_INCOMPLETE")
    live_result["cleanup"] = {
        "owned_database_runtimes_started": 1,
        "database_dropped": True,
        "pid_exited": True,
        "ports_closed": True,
        "temporary_store_removed": True,
        "force_kill_used": False,
        "production_resources_touched": 0,
        "temporary_packet_key_retained": 0,
    }
    return live_result


def _base_payload(
    *,
    mode: str,
    repository: Mapping[str, Any],
    suite: Any,
    inventory: Sequence[Any],
    offline: Mapping[str, Any],
    step39_boundary: _Step39BoundaryScanProof,
) -> dict[str, Any]:
    spec = _step38_openrouter_spec()
    offline_mode = mode == "OFFLINE_DEVELOPMENT_ONLY"
    not_observed = "UNKNOWN_NOT_RUN" if offline_mode else "PENDING_LIVE_RUNTIME"
    return {
        "step": 38,
        "schema_version": SCHEMA_VERSION,
        "start_sha": START_SHA,
        "validation_mode": mode,
        "repository": repository,
        "component_inventory": {
            "status": "PASS",
            "count": len(inventory),
            "steps": [item.step for item in inventory],
            "inventory_digest": canonical_sha256(
                tuple(
                    {
                        "step": item.step,
                        "component_id": item.component_id,
                        "module_name": item.module_name,
                        "grants_new_authority": item.grants_new_authority,
                    }
                    for item in inventory
                )
            ),
        },
        "golden_cases": {
            "suite_id": suite.suite_id,
            "suite_hash": suite.suite_hash,
            "case_ids": [case.case_id for case in suite.cases],
            "fixture_classes": {
                case.case_id: case.fixture_class.value for case in suite.cases
            },
        },
        "provider": _provider_public_spec(spec),
        "deterministic_contract_proofs": offline,
        "security": {
            "runtime_security_status": (
                "NOT_RUN_OFFLINE" if offline_mode else "PENDING_LIVE_RUNTIME"
            ),
            "cross_user_access": not_observed,
            "cross_tenant_access": not_observed,
            "personal_memory_canonical_evidence": not_observed,
            "canonical_conflict_overridden": not_observed,
            "secret_leakage_count": not_observed,
            "static_authority_contracts": {
                "evidence_class": "STATIC_CONTRACT_ATTESTATION",
                "status": "PENDING_VALIDATION",
            },
        },
        "effect_bounds": {
            "production_db_mutations": 0,
            "production_aws_mutations": 0,
            "production_s3_mutations": 0,
            "production_secret_rotations": 0,
            "arbitrary_external_actions": 0,
        },
        "step39_boundary": step39_boundary.public_mapping(),
    }


def validate(args: argparse.Namespace) -> tuple[Mapping[str, Any], int]:
    # Consume the exact provider secret before the first subprocess so neither
    # Git preflight nor the owned CockroachDB runtime can inherit it.  The one
    # pinned Python-runtime re-exec receives it explicitly later.
    try:
        credential_preflight = _consume_openrouter_environment_credential()
    except CredentialBoundaryError:
        credential_preflight = None
    _progress("REPOSITORY_AND_COMPONENT_PREFLIGHT")
    repository = _repository_identity()
    suite = load_german_law_golden_cases(FIXTURE_PATH)
    inventory = step38_component_inventory(require_importable=True)
    golden_case_outcomes = _golden_case_outcome_proofs(suite)
    authority_contract_proofs = _authority_contract_proofs()
    step39_boundary = _step39_boundary_scan()

    if args.offline:
        _progress("DETERMINISTIC_CONTRACT_PROOFS")
        offline = _offline_contract_proofs(suite)
    else:
        offline = {
            "status": "NOT_RUN_IN_LIVE_RUNTIME",
            "reason": "LIVE_MODE_USES_ONE_REAL_TYPED_LINEAGE",
            "closure_authority": False,
        }
    mode = (
        "OFFLINE_DEVELOPMENT_ONLY"
        if args.offline
        else "LIVE_ONE_OWNED_SAME_DATABASE_LINEAGE"
    )
    output = _base_payload(
        mode=mode,
        repository=repository,
        suite=suite,
        inventory=inventory,
        offline=offline,
        step39_boundary=step39_boundary,
    )
    output["golden_case_outcomes"] = golden_case_outcomes
    output["authority_contract_proofs"] = authority_contract_proofs

    if not step39_boundary.passed:
        output.update(
            {
                "status": "FAILED_STEP39_BOUNDARY_SCAN",
                "closure_eligible": False,
                "closure_block_reason": "UNEXPECTED_STEP39_PRODUCTION_BRIDGE_HIT",
            }
        )
        output["validation_digest"] = canonical_sha256(output)
        return output, 1

    if args.offline:
        output.update(
            {
                "status": "PASS_OFFLINE_NOT_CLOSURE",
                "closure_eligible": False,
                "closure_block_reason": "OFFLINE_MODE_HAS_NO_REAL_PROVIDER_OR_DISPOSABLE_DB_E2E",
                "real_corpus": {"status": "NOT_RUN_OFFLINE"},
                "real_model_flow": {"status": "NOT_RUN_OFFLINE"},
                "live_same_database_lineage": {"status": "NOT_RUN_OFFLINE"},
                "closure_gaps": [
                    "REAL_MODEL_RUN_REQUIRED",
                    "REAL_RETRIEVAL_RUNTIME_REQUIRED",
                    "SINGLE_RUNTIME_COHERENT_LINEAGE_REQUIRED",
                    "REAL_PERSONAL_MEMORY_LIFECYCLE_REQUIRED",
                    "REAL_AUDIT_REVIEW_UI_FIXTURE_REQUIRED",
                ],
                "cleanup": {
                    "owned_database_runtimes_started": 0,
                    "provider_calls": 0,
                    "temporary_sensitive_material_retained": 0,
                },
            }
        )
        assert_secret_free(
            output,
            surface="STEP38_OFFLINE_CONTROLLED_VALIDATION",
            reject_machine_paths=True,
        )
        output["validation_digest"] = canonical_sha256(output)
        return output, 0

    _progress("REAL_STEP14_16_CORPUS_PREFLIGHT")
    real_corpus, _source_text, _temporal_projection = _real_corpus_preflight(args)
    output["real_corpus"] = real_corpus

    # Fail before embedding-runtime/DB work when the exact provider capability
    # is absent.  The purpose-bound value is never serialized.
    if credential_preflight is None:
        output.update(
            {
                "status": "BLOCKED_SAFELY_NOT_CLOSURE",
                "closure_eligible": False,
                "closure_block_reason": "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
                "real_model_flow": {
                    "status": "BLOCKED",
                    "reason": "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
                    "provider_calls": 0,
                    "provider_material_recorded": False,
                },
                "closure_gaps": [
                    "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
                    "SINGLE_RUNTIME_COHERENT_LINEAGE_REQUIRED",
                    "REAL_PERSONAL_MEMORY_LINEAGE_NOT_REACHED",
                ],
                "cleanup": {
                    "owned_database_runtimes_started": 0,
                    "provider_calls": "BOUNDED_BY_APPROVED_ATTEMPT_POLICY",
                    "temporary_packet_key_retained": 0,
                },
            }
        )
        assert_secret_free(
            output,
            surface="STEP38_BLOCKED_REAL_MODEL_VALIDATION",
            reject_machine_paths=True,
        )
        output["validation_digest"] = canonical_sha256(output)
        return output, 2
    _progress("ONE_OWNED_SAME_DATABASE_LINEAGE")
    live = _run_live_same_database(
        args,
        suite,
        step39_boundary,
        credential_preflight,
    )
    reason = live.get("closure_block_reason")
    output.update(live)
    if not live.get("closure_eligible"):
        output["closure_gaps"] = [
            str(reason or "STEP38_COHERENT_LIVE_LINEAGE_INCOMPLETE")
        ]
    assert_secret_free(
        output,
        surface="STEP38_LIVE_PRE_REDACTION_SCAN",
        reject_machine_paths=True,
    )
    live_security = dict(output.get("security", {}))
    live_security.update(
        {
            "secret_leakage_count": 0,
            "redaction_scan_status": "PASS",
            "static_authority_contracts": {
                "evidence_class": "STATIC_CONTRACT_ATTESTATION",
                "proof_digest": authority_contract_proofs["proof_digest"],
                "invariants": {
                    "model_authority": "NO",
                    "provider_commit_authority": "NO",
                    "commit_helper_approval_authority": "NO",
                    "reviewer_commit_authority": "NO",
                    "admin_fallback": "NO",
                },
            },
        }
    )
    output["security"] = live_security
    assert_secret_free(
        output,
        surface="STEP38_LIVE_SAME_DATABASE_VALIDATION",
        reject_machine_paths=True,
    )
    output["validation_digest"] = canonical_sha256(output)
    return output, 0 if live.get("closure_eligible") else 2


def _failure_payload(error: BaseException) -> Mapping[str, Any]:
    reason = getattr(
        error,
        "sanitized_code",
        getattr(error, "code", type(error).__name__.upper()),
    )
    payload: dict[str, Any] = {
        "step": 38,
        "schema_version": SCHEMA_VERSION,
        "start_sha": START_SHA,
        "status": "FAILED",
        "closure_eligible": False,
        "reason": reason,
        "secret_leakage_count": 0,
        "step39_boundary": {"status": "NOT_AVAILABLE_ON_EXCEPTION"},
    }
    assert_secret_free(
        payload,
        surface="STEP38_CONTROLLED_VALIDATION_FAILURE",
        reject_machine_paths=True,
    )
    payload["validation_digest"] = canonical_sha256(payload)
    return payload


def main() -> int:
    try:
        payload, exit_code = validate(_arguments())
    except Exception as error:
        payload = _failure_payload(error)
        exit_code = 1
    print(canonical_json(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
