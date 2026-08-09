# Step 28 - Knowledge Hub and Critic Correction Candidate Bridge 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 29: NOT STARTED.

## Starting identity

- Exact Step 27 baseline:
  `a4317d4c5689d35f649f19b45646e7205876581f`
- Baseline subject: `feat(memory): add personal memory hat persistence 1a`
- Branch: `main`
- Baseline full repository suite: 1,652 passed
- Baseline focused Step 17/20-27, RLS, persistence, authority, tenant, and
  serialization regressions: 560 passed
- Baseline contract validator and compileall: PASS

The final closure identity is the commit containing this record. No future
commit SHA is embedded before that commit exists.

## Candidate contract and persistence decision

Step 28 adds immutable, hash-bound submission, source, lineage, envelope,
quota-policy, and receipt contracts. Exact Kernel and Critic Prompt Loop
adapters feed one candidate-only bridge. There is no canonical Knowledge Hub
runtime implementation, so its producer adapter is `NOT_REQUIRED` in 1A.

Migration `0012_step28_correction_candidate_bridge.sql` reuses the existing
`memory_patch.memory_patch_proposals` carrier instead of creating a parallel
candidate table. Candidate rows are restricted to `DETECTED`, exact owner and
eligible Step 27 slot lineage and bounded model-experience trust. Bounded
upstream evidence hashes are retained only as unvalidated lineage references;
they are neither validated nor promoted by Step 28. The application role
receives only the narrowly scoped insert capability; Step 28 exposes no
update/delete or later-state operation.

## Slot, quota, idempotency, and isolation

Every intake binds one exact tenant/owner/slot, slot configuration digest and
version, request/run and route lineage, producer identity, typed trigger and
reason codes, exact text digest, and bounded scope/metadata. Only configured
or active targets are accepted. Archived, suspended, delete-pending, deleted,
unknown, cross-owner, and cross-tenant targets fail closed.

Candidate count and byte limits are digest-bound hard policy. The service
checks them inside its short serializable transaction, and the database
independently serializes each slot through a quota epoch and rejects the
incoming JSONB value above 8 MiB before applying an overflow-safe cumulative
carrier-ledger comparison against 128 rows and 8 MiB. Exact conflict
replay does not consume quota. Controlled validation races two independent
serializable transactions for the final position and requires exactly one
commit and one quota rejection. Step 6 idempotency
reuses exact command replay and rejects changed content. Semantic candidate
identity deduplicates exact owner/slot/source/run/scope/text duplicates while
preserving the original candidate provenance. Completed exact replay is not
invalidated by later mutable slot state, and the duplicate intake receipt
binds its triggering submission separately from the returned stored
candidate. No model-assisted semantic merge occurs.

RLS and FORCE RLS remain authoritative on the durable carrier and exact owner
context. Controlled database validation separates Tenant A / User A, Tenant A
/ User B, and Tenant B / User C; negative contexts cannot insert or observe
User A's candidates.

Migration 0012 materializes `hat_scope_id`, `slot_hash`, and the non-semantic
quota epoch on Step 27 slots. The database derives the owner scope itself and
requires exact current slot/configuration/quota/model-binding equality. The
typed Step 27 repository constructs the full canonical slot hash and verifies
it on rehydrate. A legacy unsealed slot is intentionally ineligible until a
normal Step 27 configuration update writes the canonical pair; there is no
unsafe bulk backfill.

## Validation

The closure gate requires all of the following to pass with zero failures:

- compileall and the focused Step 28 suite;
- the full repository test suite;
- focused Step 1/2, 5/6, 17, and 20-27 authority, persistence, RLS, tenant/user
  isolation, and serialization regressions;
- contract validation; and
- deterministic disposable CockroachDB Step 28 validation, including
  migration replay, producer intake, idempotency/dedup, quota, slot-state and
  owner negatives, and complete cleanup.

Sanitized evidence is committed at
`docs/evidence/personal-memory/step28-correction-candidate-bridge-validation.json`.
It records the exact candidate/receipt hashes and matrices, real/synthetic
Critic fixture status, RLS/FORCE RLS proof, and zero approval, commit,
activation, evidence-promotion, model/provider, or external-agent effects.

## Authority and known limitations

A candidate remains untrusted owner-private suggestion data. Kernel or Critic
submission grants no evidence, approval, commit, activation, retrieval,
source-publication, or execution authority. Candidate intake cannot change
the slot, quota, bindings, route, HAT, or source registry. NOOA, OpenShell, and
NVIDIA are not implemented.

Known limitations are explicit: only Kernel and Critic producer adapters are
active; no canonical Knowledge Hub runtime fixture exists; deduplication is
exact rather than semantic; and Step 28 performs no proposal construction,
evidence/conflict/staleness validation, approval, commit, activation, active
retrieval, cross-model reuse, or shared promotion.

## Step 29 handoff

Step 29 may consume the exact immutable `DETECTED`
`CorrectionCandidateEnvelope`, target Step 27 slot/configuration identity,
owner scope, candidate hash, and source/run lineage. It owns the expected
state machine:

```text
DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL
```

Step 29 may build and evidence-validate a Personal Memory Patch Proposal. It
must not approve, commit, or activate the proposal. Step 29 remains NOT
STARTED.
