# Step 29 Personal Memory Patch Proposal Validation Closure 1A

## Starting point and scope

- Exact Step 28 base: `8ee125e3ab4b964c4ed85dcee95b08932fe0cab5`.
- Step 28 was complete and pushed; Step 29 and Step 30 were not started.
- Scope is limited to `DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED ->
  AWAITING_APPROVAL` and deterministic validation gates.
- Step 30 remains not started. No final Step 29 commit SHA is asserted here
  before the closure commit exists.

## Contracts and state machine

Step 29 adds immutable canonical contracts for proposal creation, exact
canonical-evidence binding, deterministic validation receipt, typed
transition commands, state wrapper, and transition receipts. Proposal ID,
dedup identity, content, evidence, validation, state, command, and receipt
hashes are deterministic.

All four edges are explicit and compare-and-set protected. No `PROPOSED ->
VALIDATED` or direct `AWAITING_APPROVAL` path exists. `AWAITING_APPROVAL` has
no approval actor/token and does not enter `APPROVED`, `COMMITTED`, or
`ACTIVE`.

## Evidence and validation

Evidence binding reuses exact Step 20 bundles, Step 21 temporal/freshness and
conflict status, Step 23 claim links/assessments, Step 24 packet identities,
and Step 26 Verified Answer lineage. It acquires no new evidence and grants no
authority to candidate, proposal, Critic, or model output.

Validation deterministically gates exact deduplication, peer/canonical/
temporal conflicts, freshness, owner scope, current slot state, Step 27
quota, and exact enabled model binding. Stronger canonical evidence remains a
constraint. A valid hash-bound receipt is required before
`AWAITING_APPROVAL`.

## Persistence and isolation

Migration `0013_step29_personal_memory_patch_validation.sql` reuses the
existing proposal and transition carriers, materializes exact Step 28
candidate lineage under a self-referential foreign key, embeds and verifies
the complete target-binding snapshot, and adds exact hash/state/dedup fields,
transactional dedup uniqueness, a serialized owner-slot proposal quota, and
an exact transition guard. Non-recursive target and transition helpers avoid
RLS dependency cycles while RLS/FORCE RLS plus the Step 28 candidate and
current Step 27 target tuple enforce tenant/user/slot isolation. The runtime
receives no DELETE, approval, commit, activation, or execution privilege.

## Validation evidence

Pre-commit validation completed with zero failures/errors: compileall passed;
the Step 29 focused module passed `21/21`; the explicit Step 17 and Step 20-28,
RLS, persistence, authority, tenant/user isolation, and serialization set
passed `595/595`; full unittest discovery passed `1708/1708`; contract
validation passed all five schemas and public authority invariants; and the
offline migration/catalog validation passed all thirteen migrations.

The controlled CockroachDB `v26.2.4` run applied and replay-verified all
thirteen migrations, exercised every Step 29 state edge and negative gate,
proved RLS/FORCE RLS owner isolation, and cleaned up its owned runtime. Its
canonical validation digest is
`d095ef26047b219bf316db6e3c81d59fc5048547ca31de900e1a32d7ff9c3af9`.

The committed evidence file is
`docs/evidence/personal-memory/step29-personal-memory-patch-validation.json`.
It records the exact base, proposal/evidence/receipt hashes, full state and
idempotency matrices, deterministic negative cases, RLS/FORCE RLS proof,
disposable CockroachDB replay, authority zeroes, cleanup, and a recomputable
canonical validation digest. It contains no secret or raw private proposal
text.

## Authority and later-step proof

- Personal Memory proposals are not canonical evidence.
- Validation is not approval.
- Model, Critic, Kernel, and HAT approval authority: none.
- Commit, activation, retrieval, external-action, and execution authority:
  none.
- Approved transitions: zero.
- Committed transitions: zero.
- Active transitions: zero.
- Step 30: NOT STARTED.

## Known limitations

Step 29 1A supports deterministic factual-correction proposals and exact
normalization/conflict checks. It does not use model-based semantic merge or
resolve ambiguous near-duplicates. Proposal quota validation does not reserve
future commit capacity; Step 30 must revalidate the exact current quota before
any authorized commit. Existing active-patch duplicate checks remain dormant
until Step 30 creates such records.
