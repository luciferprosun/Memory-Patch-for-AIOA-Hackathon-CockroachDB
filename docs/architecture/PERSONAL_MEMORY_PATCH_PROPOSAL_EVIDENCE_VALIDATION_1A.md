# Personal Memory Patch Proposal and Evidence Validation 1A

## Boundary

Step 29 consumes one exact Step 28 `CorrectionCandidateEnvelope` and may
advance only through:

```text
DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL
```

Each edge is explicit, versioned, optimistic-concurrency protected, and
recorded as an immutable transition. `AWAITING_APPROVAL` is review readiness,
not approval. Step 29 has no approval actor or token, commit helper,
`APPROVED`, `COMMITTED`, or `ACTIVE` transition, retrieval, provider call,
new evidence acquisition, or execution capability.

## Candidate and proposal contracts

`CreatePersonalMemoryPatchProposal` binds the exact Step 28 candidate ID,
envelope hash, target binding hash, tenant, owner, slot, idempotency key, and
request time. Construction re-verifies the entire candidate contract and
preserves the candidate's exact correction text. No model may rewrite the
statement.

The immutable `PersonalMemoryPatchProposal` binds the candidate content and
envelope hashes, claim and evidence-reference hashes, exact owner-private HAT
scope, current model-binding identity, route/result/query and Draft/packet/
answer lineage, proposal scope, typed factual-correction kind, exact text
SHA-256, deterministic normalization, conflict subject, dedup key, proposal
ID, and proposal hash. The proposal is explicitly non-canonical evidence.

Step 29 1A accepts factual corrections from the Step 28 Kernel and Critic
candidate origins. It does not introduce provider-specific or Gemma-specific
semantics.

## Canonical evidence binding

`PersonalMemoryPatchEvidenceBinding` is built only from already verified
Kernel artifacts:

- Step 20 frozen evidence bundles and immutable candidate identities;
- Step 21 temporal resolution, conflict, evidence status, and trusted time;
- Step 23 claim-evidence links and candidate assessments;
- Step 24 Correction Packet and packet-permitted references;
- Step 26 exact Verified Answer lineage.

The builder checks hashes, request/route/HAT/scope lineage, tenant identity,
publication eligibility, source/version/chunk/content identity, authority,
temporal applicability, freshness, claim relationship, correction packet,
and verified-answer identity. References are deterministically ordered and
hash-bound. Candidate text, proposal text, Critic output, and model output
cannot satisfy the evidence gate. Step 29 performs no retrieval or web call.

`EVIDENCE_BOUND` means only that exact evidence has been attached. It is not a
truth, approval, or activation decision.

## Deterministic validation gates

`PersonalMemoryPatchValidationReceipt` binds the evidence binding and the
versioned Step 29 policy to separate closed results for deduplication,
conflict, freshness/temporal status, owner scope, slot state, quota, and model
binding. The receipt is valid only when every required gate passes.

Exact deduplication uses tenant, owner, slot, deterministically normalized
text, exact scope, and proposal kind. Database uniqueness closes the
concurrent-create race. A duplicate existing `COMMITTED` or `ACTIVE` carrier,
if later present, is detected but is not modified, superseded, or activated.
Step 29 does not use a model for near-duplicate decisions.

Conflict checking deterministically detects direct polarity contradiction,
overlapping owner-slot proposals, canonical-evidence prohibitions, Step 21
temporal conflicts, and existing-patch conflicts. Canonical evidence is a
hard constraint; Personal Memory cannot upgrade or override its authority.
An ambiguity that cannot be proven safely remains non-validatable.

Freshness is re-evaluated from the exact Step 21 trusted-time result. Stale,
conflicting, insufficient, unavailable, or invalid required evidence does not
advance. The validation receipt is time-bounded and becomes unusable for the
final edge after its policy maximum age.

## Owner, slot, quota, and model binding

Every service edge reloads the Step 28 envelope and current Step 27 slot in a
short serializable transaction. Tenant, owner, slot, HAT scope, candidate,
route, slot hash/configuration, quota policy, and enabled exact model binding
must still match. An archived, suspended, delete-pending, deleted, unknown,
or reconfigured target fails closed. No transition reactivates a slot.

Step 27 quota is rechecked before validation and approval readiness. The
future active-patch count must remain below the configured hard ceiling. Step
29 additionally limits durable proposals to 128 rows or 8 MiB per owner slot;
a serialized database trigger independently enforces the same cumulative
ceiling. This is validation, not quota reservation for Step 30.

Model bindings remain provider-neutral configuration. A matching binding
does not grant a model write, read, approval, commit, or activation authority.

## Persistence and isolation

Migration `0013_step29_personal_memory_patch_validation.sql` reuses
`memory_patch.memory_patch_proposals` and
`memory_patch.patch_transition_records`; no parallel Personal Memory store is
created. It adds exact candidate lineage columns, a self-referential candidate
foreign key, dedup/state/evidence/receipt columns, an owner-slot dedup index,
a target-lineage function, a proposal quota trigger, and an exact
state-transition guard. Every proposal embeds the exact Step 28 target binding;
the database compares its complete current slot, configuration, quota, and
model-binding snapshot without recursively querying the protected proposal
relation. A separate non-recursive helper binds transition inserts to their
current proposal and avoids an RLS dependency cycle.

RLS and FORCE RLS bind all proposal reads, inserts, updates, and transition
events to Step 5 tenant and owner context. Composite tenant/owner/slot keys,
the persisted Step 28 candidate, and the current Step 27 slot/model tuple are
checked at the database boundary. The runtime receives only proposal UPDATE
and transition-event INSERT privileges needed for Step 29, never DELETE.

Step 6 idempotency makes every exact command replay return the existing
state/receipt. Reusing an idempotency identity with different content fails
closed. State version, state hash, proposal hash, evidence binding hash, and
validation receipt hash protect every compare-and-set transition.

## Step 30 boundary

`VALIDATED -> AWAITING_APPROVAL` requires the exact valid receipt and a final
current-state recheck. The result contains no approval actor, token, receipt,
dedicated credential, commit record, or activation record. Step 30 alone may
implement explicit owner approval and the separately authorized
`AWAITING_APPROVAL -> APPROVED -> COMMITTED -> ACTIVE` sequence.
