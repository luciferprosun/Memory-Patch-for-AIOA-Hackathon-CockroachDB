# Step 32 Personal Memory Lifecycle Closure 1A

## Starting point and scope

- Exact Step 31 base: `bf6cde9de87ab727f1bd5e48e2abfc7e8e3b85b5`.
- Step 31 was complete and pushed; Step 32 and Step 33 were not started.
- The change is limited to owner-private supersession, revocation, export,
  logical deletion and a review-only shared-promotion proposal boundary.
- Step 33 remains not started. This record does not invent the final Step 32
  Git closure SHA before that commit exists.

## Contracts and lifecycle behavior

Step 32 adds immutable, hash-bound requests and results for supersession,
revocation, deterministic owner export, logical deletion, separate shared
consent, deterministic de-identification assessment and shared-promotion
proposal. Every object binds the exact tenant, owner, Personal Memory slot,
Step 30 patch/state hashes, state version, replay identity and effective time.

Supersession preserves both patch contents and all approval, commit and
activation lineage. Current Step 31 retrieval suppresses the superseded patch,
while an exact historical `knowledge_as_of` before the supersession effective
time may retain it. Revocation makes a patch retrieval-ineligible without
rewriting or deleting it. Logical deletion reuses the Step 27
`DELETED_PENDING -> DELETED` slot path, records a tombstone and never claims
physical erasure.

## Persistence, replay and isolation

Migration `0015_step32_personal_memory_lifecycle` adds five append-only,
owner-private lifecycle tables and a guarded terminal overlay on the existing
Step 30 `memory_items` carrier. Its SHA-256 is
`eb1a442a98d5822d11b316822a40258b6b9d780cdb55be75af2988ac4677bf4a`.
There is no parallel patch store and no destructive database DELETE grant.

All protected tables use RLS and FORCE RLS. Composite keys, foreign keys,
parameterized owner/tenant/slot queries and service revalidation deny
same-tenant cross-user and cross-tenant operations. Step 6 serializable
transactions, receipt-first conditional terminal updates, uniqueness and
per-operation replay identities make exact replay idempotent and changed
replay fail closed.

CockroachDB requires the ordinary runtime to evaluate the existing Step 30
Commit Helper membership predicate while planning a Step 32 owner terminal
update. Migration 0015 grants only EXECUTE on that SECURITY INVOKER predicate;
it grants no Commit Helper membership, BYPASSRLS or commit authority, and the
predicate returns false for the application role.

## Export, deletion and shared-promotion boundary

Owner export is canonical, deterministic, bounded to 1,024 records and 8 MiB,
and recursively rejects secret-like fields, machine-local paths and foreign
owner data. It is an owner-private data export, not a promotion.

Private Step 30 approval is not consent to share. Promotion requires a new,
exact owner consent plus the versioned deterministic de-identification policy.
The result is only `SHARED_PROMOTION_PROPOSED` with review required. Ambiguous,
redacted or canonical-conflicting content stays review-only. No shared active
artifact, source-registry publication or canonical-evidence authority is
created.

## Validation and authority proof

The closure validation covers exact and conflicting replay, current and
historical supersession applicability, revocation retrieval suppression,
deterministic owner export, logical deletion and deletion suppression,
de-identification, review-only promotion, real RLS/FORCE RLS catalog state,
same-tenant cross-user denial, cross-tenant denial and complete disposable
runtime cleanup.

Personal Memory remains private non-canonical context. Neither an ACTIVE
private patch nor a shared-promotion proposal becomes canonical evidence,
publishes a source, upgrades source authority, authorizes a model or enables
external execution.

The canonical sanitized evidence is recorded at
`docs/evidence/personal-memory/step32-personal-memory-lifecycle-validation.json`.
Its clean disposable CockroachDB validation digest is
`5a122a6ee0af54e9818bee4a3c45515fc0d6e13deb272005a1111d68a9ccd1db`.

The final pre-commit validation passed:

- Python compilation for `src`, `scripts` and `tests`;
- all 22 focused Step 32 tests;
- all 637 focused Step 17 and Step 20-31, RLS, persistence, authority,
  tenant-boundary and contract-serialization regressions;
- all 1,772 tests in full repository discovery;
- the five-schema contract validator;
- offline validation of all 15 CockroachDB migrations; and
- the controlled disposable CockroachDB lifecycle validation, including
  replay, RLS/FORCE RLS, retrieval suppression and complete cleanup.

## Known limitations and later steps

- Deletion is logical, not physical.
- De-identification is deterministic and conservative; human review is still
  mandatory and no publication transition exists.
- Step 32 adds bounded domain receipts, not the Step 33 global audit ledger,
  hash chain or audit export.
- Step 34 human review workspace and Step 35 Personal Memory UI are absent.

`Step 33: NOT STARTED`.
