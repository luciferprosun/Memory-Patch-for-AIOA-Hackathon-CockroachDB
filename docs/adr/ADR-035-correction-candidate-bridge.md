# ADR-035: Correction producers remain candidate-only

## Status

Proposed. It becomes accepted only when the Step 28 closure commit is
reachable on `origin/main`.

## Context

Step 27 provides owner-private Personal Memory slots but no correction
content workflow. Kernel and Critic observations now need a durable bridge to
later proposal validation without accidentally becoming patches, evidence,
approval, or activation authority. The current repository has no canonical
Knowledge Hub runtime adapter, and Step 29 owns the proposal and evidence
validation state machine.

## Decision

1. Kernel and Critic Prompt Loop producers may emit only bounded, immutable,
   hash-bound `CorrectionCandidateEnvelope` data through typed adapters.
2. Every candidate targets one exact `CONFIGURED` or `ACTIVE` Step 27 slot and
   binds its tenant, owner, scope, route/run lineage, and slot configuration.
3. Critic output is untrusted provenance, not canonical evidence. Model or
   producer agreement does not create authority.
4. Candidate intake reuses `memory_patch.memory_patch_proposals` as its
   durable carrier through migration 0012, but writes only the `DETECTED`
   state and exposes no patch-transition API.
5. Candidate intake is Step 6 idempotent. Semantic identity provides exact
   deterministic deduplication without model-assisted similarity merging or
   provenance overwrite.
6. Candidate count and byte quotas are hard policy. A short serializable
   service transaction performs the early check and a same-slot, `BEFORE
   INSERT` database trigger independently serializes and enforces the actual
   cumulative 128-row / 8-MiB ledger.
7. Composite tenant/owner/slot lineage plus RLS and FORCE RLS enforce owner
   privacy. Cross-user or cross-tenant reads and submissions fail closed.
   The database derives the canonical owner HAT scope and exact-matches the
   Step 27 repository-materialized slot hash and current configuration,
   quota, and model-binding snapshot. Legacy unsealed slots fail closed.
8. No canonical Knowledge Hub runtime exists, so a producer-specific Hub
   adapter is not required in 1A. Any future Hub or external-agent adapter
   must use the same candidate-only contract.
9. A candidate is not a patch, canonical evidence, approval, commit receipt,
   active memory, or execution authorization.
10. Step 29 exclusively owns `PROPOSED`, `EVIDENCE_BOUND`, `VALIDATED`, and
    `AWAITING_APPROVAL` behavior and evidence/conflict/staleness validation.
    Steps 30-32 own approval, commit, activation, retrieval, cross-model reuse,
    and shared promotion.

Step 29: NOT STARTED.

## Consequences

The Kernel and Critic can preserve bounded correction opportunities without
granting their outputs truth or memory authority. Exact replay and duplicate
submissions do not create candidate spam, while independent owner, slot, or
scope identities remain distinct. Database policy, not caller filtering,
prevents an application actor from submitting into or reading another user's
candidate stream.

Step 28 cannot determine whether a candidate is correct or ready for user
review. That deliberate limitation keeps proposal validation and approval
auditable as later, separately gated roadmap steps.

## Rejected alternatives

### Create a parallel candidate table

Rejected because the existing owner-bound proposal carrier already provides
the required durable lineage and security boundary.

### Treat Critic output as evidence or approval

Rejected because a model-originated observation is candidate data and cannot
establish truth, consent, or memory authority.

### Start the patch state machine during intake

Rejected because proposal construction and evidence validation belong to
Step 29 and approval/activation belong to Step 30.

### Add a speculative Knowledge Hub or external-agent runtime

Rejected because no canonical runtime exists in the current repository and
Step 28 must not create NOOA, OpenShell, NVIDIA, or other operational bridges.
