"""Deterministic, non-executing Axis A Knowledge HAT router."""

from __future__ import annotations

from collections.abc import Mapping

from aioa_memory_kernel.contracts.enums import (
    HatAuthorityDeclaration,
    KnowledgeRoute,
    MissingDimensionBehavior,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.contracts.scope import validate_scope_dimensions
from aioa_memory_kernel.hats.models import (
    CompatibilityDecision,
    RegistryEntry,
    RegistryState,
)

from .models import (
    HatCandidateAvailability,
    HatPolicyRequirement,
    HatRoutingCandidate,
    KnowledgeRouteResult,
    RoutingInput,
    Step17ReasonCode,
    verify_routing_input_hash,
)


def _registry_index(
    routing_input: RoutingInput,
) -> Mapping[tuple[str, str], RegistryEntry]:
    return {
        (
            entry.identity.manifest.hat_id,
            entry.identity.manifest.hat_version,
        ): entry
        for entry in routing_input.trusted_hat_registry_snapshot.entries
    }


def _find_entry(
    candidate: HatRoutingCandidate,
    entries: Mapping[tuple[str, str], RegistryEntry],
) -> tuple[RegistryEntry | None, Step17ReasonCode | None]:
    entry = entries.get((candidate.hat_id, candidate.hat_version))
    if entry is not None:
        return entry, None
    if any(hat_id == candidate.hat_id for hat_id, _ in entries):
        return None, Step17ReasonCode.HAT_VERSION_MISMATCH
    return None, Step17ReasonCode.HAT_UNKNOWN


def _entry_trust_failure(
    candidate: HatRoutingCandidate,
    entry: RegistryEntry,
) -> Step17ReasonCode | None:
    if entry.state is not RegistryState.ENABLED:
        return Step17ReasonCode.HAT_DISABLED
    if entry.compatibility is not CompatibilityDecision.COMPATIBLE:
        return Step17ReasonCode.HAT_UNTRUSTED
    if candidate.manifest_digest != entry.identity.typed_manifest_digest:
        return Step17ReasonCode.HAT_UNTRUSTED
    binding = entry.runtime_binding
    receipt = entry.review_receipt
    if binding is None or receipt is None:
        return Step17ReasonCode.HAT_UNTRUSTED
    manifest = entry.identity.manifest
    if (
        binding.installation_class != "SYSTEM_INSTALLED"
        or binding.hat_id != manifest.hat_id
        or binding.hat_version != manifest.hat_version
        or receipt.decision != "ENABLE"
        or receipt.hat_id != manifest.hat_id
        or receipt.hat_version != manifest.hat_version
        or receipt.canonical_manifest_digest
        != entry.identity.typed_manifest_digest
        or receipt.raw_manifest_digest != entry.identity.raw_manifest_sha256
        or receipt.schema_digest != entry.identity.schema_file_sha256
        or receipt.runtime_binding_id != binding.runtime_binding_id
        or receipt.implementation_digest != binding.implementation_digest
        or receipt.compatibility is not CompatibilityDecision.COMPATIBLE
        or receipt.capabilities_digest
        != canonical_sha256(manifest.capabilities)
    ):
        return Step17ReasonCode.HAT_UNTRUSTED
    security = manifest.security_policy
    if (
        security.external_action_authority is not HatAuthorityDeclaration.NONE
        or security.canonical_write_authority is not HatAuthorityDeclaration.NONE
        or security.patch_approval_authority is not HatAuthorityDeclaration.NONE
        or security.patch_commit_authority is not HatAuthorityDeclaration.NONE
        or security.executable_user_code is not False
        or security.private_memory_access is not False
    ):
        return Step17ReasonCode.HAT_UNTRUSTED
    return None


def _scope_failure(
    routing_input: RoutingInput,
    entry: RegistryEntry,
) -> Step17ReasonCode | None:
    manifest = entry.identity.manifest
    if routing_input.requested_domain_id not in manifest.domain_ids:
        return Step17ReasonCode.HAT_SCOPE_MISMATCH
    try:
        ambiguous = validate_scope_dimensions(
            manifest.scope_dimension_definitions,
            routing_input.requested_scope,
        )
    except ContractValidationError:
        return Step17ReasonCode.HAT_SCOPE_MISMATCH
    if ambiguous:
        return Step17ReasonCode.SCOPE_AMBIGUOUS
    supplied = {dimension.name for dimension in routing_input.requested_scope}
    for definition in manifest.scope_dimension_definitions:
        if definition.name in supplied or not definition.required:
            continue
        if (
            definition.default_behavior
            is MissingDimensionBehavior.AMBIGUOUS
            or definition.missing_creates_ambiguous
        ):
            return Step17ReasonCode.SCOPE_AMBIGUOUS
        if definition.default_behavior is MissingDimensionBehavior.USE_DEFAULT:
            continue
        return Step17ReasonCode.HAT_SCOPE_MISMATCH
    return None


def _candidate_failure(
    routing_input: RoutingInput,
    candidate: HatRoutingCandidate,
    entries: Mapping[tuple[str, str], RegistryEntry],
) -> tuple[RegistryEntry | None, Step17ReasonCode | None]:
    entry, failure = _find_entry(candidate, entries)
    if failure is not None:
        return None, failure
    assert entry is not None
    if candidate.availability is HatCandidateAvailability.QUARANTINED:
        return None, Step17ReasonCode.HAT_QUARANTINED
    if candidate.availability is HatCandidateAvailability.REVOKED:
        return None, Step17ReasonCode.HAT_REVOKED
    if candidate.tenant_id is not None and candidate.tenant_id != routing_input.tenant_id:
        return None, Step17ReasonCode.TENANT_SCOPE_MISMATCH
    if (
        candidate.owner_user_id is not None
        and candidate.owner_user_id != routing_input.user_id
    ):
        return None, Step17ReasonCode.USER_SCOPE_MISMATCH
    failure = _entry_trust_failure(candidate, entry)
    if failure is not None:
        return None, failure
    failure = _scope_failure(routing_input, entry)
    if failure is not None:
        return None, failure
    return entry, None


def route_knowledge_request(routing_input: RoutingInput) -> KnowledgeRouteResult:
    """Return the deterministic Axis A route without invoking any HAT.

    Request-local candidates can only narrow the path. Exact enabled registry
    state and trusted review evidence are required before a HAT can be
    selected. A failed mandatory candidate becomes ``AMBIGUOUS`` rather than
    silently falling back to a more permissive route.
    """

    if not isinstance(routing_input, RoutingInput):
        raise ContractValidationError("routing_input must be a RoutingInput")
    verify_routing_input_hash(routing_input)
    entries = _registry_index(routing_input)
    eligible: list[tuple[HatRoutingCandidate, RegistryEntry]] = []
    failures: set[Step17ReasonCode] = set()
    mandatory_failure = False
    for candidate in routing_input.candidate_hat_descriptors:
        entry, failure = _candidate_failure(routing_input, candidate, entries)
        if failure is None:
            assert entry is not None
            eligible.append((candidate, entry))
            continue
        failures.add(failure)
        if candidate.policy_requirement is HatPolicyRequirement.MANDATORY:
            mandatory_failure = True

    eligible_hashes = tuple(candidate.candidate_hash for candidate, _ in eligible)
    if mandatory_failure:
        failures.add(Step17ReasonCode.SCOPE_AMBIGUOUS)
        return KnowledgeRouteResult(
            request_id=routing_input.request_id,
            tenant_id=routing_input.tenant_id,
            user_id=routing_input.user_id,
            routing_input_hash=routing_input.input_hash,
            registry_snapshot_hash=(
                routing_input.trusted_hat_registry_snapshot.snapshot_hash
            ),
            knowledge_route=KnowledgeRoute.AMBIGUOUS,
            selected_hat_id=None,
            selected_hat_version=None,
            selected_manifest_digest=None,
            effective_scope=routing_input.requested_scope,
            eligible_candidate_hashes=eligible_hashes,
            reason_codes=tuple(failures),
        )

    mandatory = [item for item in eligible if item[0].policy_requirement is HatPolicyRequirement.MANDATORY]
    advisory = [item for item in eligible if item[0].policy_requirement is HatPolicyRequirement.ADVISORY]
    if len(mandatory) > 1 or (not mandatory and len(advisory) > 1):
        return KnowledgeRouteResult(
            request_id=routing_input.request_id,
            tenant_id=routing_input.tenant_id,
            user_id=routing_input.user_id,
            routing_input_hash=routing_input.input_hash,
            registry_snapshot_hash=(
                routing_input.trusted_hat_registry_snapshot.snapshot_hash
            ),
            knowledge_route=KnowledgeRoute.AMBIGUOUS,
            selected_hat_id=None,
            selected_hat_version=None,
            selected_manifest_digest=None,
            effective_scope=routing_input.requested_scope,
            eligible_candidate_hashes=eligible_hashes,
            reason_codes=(Step17ReasonCode.MULTIPLE_HAT_CONFLICT,),
        )

    selected: tuple[HatRoutingCandidate, RegistryEntry] | None = None
    route = KnowledgeRoute.PASS_THROUGH
    reasons = set(failures)
    if mandatory:
        selected = mandatory[0]
        route = KnowledgeRoute.HAT_ENFORCE
        reasons.add(Step17ReasonCode.MANDATORY_HAT_POLICY)
    elif advisory:
        selected = advisory[0]
        route = KnowledgeRoute.HAT_ASSIST
        reasons.add(Step17ReasonCode.SINGLE_ASSISTING_HAT)
    else:
        reasons.add(Step17ReasonCode.NO_ELIGIBLE_HAT)

    selected_candidate = selected[0] if selected is not None else None
    selected_entry = selected[1] if selected is not None else None
    return KnowledgeRouteResult(
        request_id=routing_input.request_id,
        tenant_id=routing_input.tenant_id,
        user_id=routing_input.user_id,
        routing_input_hash=routing_input.input_hash,
        registry_snapshot_hash=(
            routing_input.trusted_hat_registry_snapshot.snapshot_hash
        ),
        knowledge_route=route,
        selected_hat_id=(
            None if selected_candidate is None else selected_candidate.hat_id
        ),
        selected_hat_version=(
            None if selected_candidate is None else selected_candidate.hat_version
        ),
        selected_manifest_digest=(
            None
            if selected_entry is None
            else selected_entry.identity.typed_manifest_digest
        ),
        effective_scope=routing_input.requested_scope,
        eligible_candidate_hashes=eligible_hashes,
        reason_codes=tuple(reasons),
    )
