# Step 30 User Approval, Commit Helper and Activation Closure 1A

## Starting point and scope

- Exact Step 29 base: `0805fcbb04822d48198aa95ead42abd281784001`.
- Step 29 was complete and pushed; Step 30 and Step 31 were not started.
- Scope is limited to explicit owner approval, separate technical commit and
  receipt-gated activation.
- Step 31 remains not started. No final Step 30 Git commit SHA is asserted in
  this record before the closure commit exists.

## Human approval

Step 30 adds an immutable approval request and receipt. The request binds the
exact Step 29 proposal/evidence/validation state, owner-private slot and scope,
presentation digest, expected state hash/version, nonce and request hash. Only
the authenticated exact owner with actor type `HUMAN_USER` can create the
receipt. Model, Critic, Kernel and HAT outputs have no approval path.

Approval replay is separately tenant/owner scoped. Exact replay returns the
same receipt; changed semantics under the consumed nonce conflict. Approval
creates neither a committed nor active patch.

## Commit Helper and credential separation

`PersonalMemoryCommitHelper` uses the dedicated
`mp_personal_memory_commit_helper` role. The role is non-login,
non-BYPASSRLS, inherits no broad role, cannot approve, delete, publish a
source, mutate a HAT registry, or access provider/external-action authority.
A validation-only login receives the role without placing any credential in
the repository. Slot quota serialization is restricted to the single
`candidate_quota_epoch` effect. CockroachDB v26.2 lacks column-level UPDATE
grants, so a dedicated role-aware trigger rejects every other slot-table
mutation and requires an exact one-step epoch increment.
The only identity-table capability is `SELECT` on `tenants` and `users`,
required by CockroachDB foreign-key checks and constrained by exact
tenant/owner RLS policies; no identity mutation is granted.
The required `kernel_runs` lineage read is likewise `SELECT`-only and exact
tenant/owner scoped, with no run mutation authority.
The parent-slot audit FK read is separately restricted to audit rows carrying
the exact current owner and personal-space identity; no audit mutation is
granted.

Immediately before commit the service revalidates proposal, approval,
evidence, validation receipt, trusted time, owner/tenant/slot, quota and exact
model binding. The commit transaction atomically persists the exact approved
statement as an inactive owner-private patch, commit receipt, proposal state,
transition event and idempotency completion. Changed content cannot be
substituted.

## Activation and TOCTOU

Activation requires the exact committed state, patch, commit and approval
receipts, owner/slot, state version and an independent replay identity. It
repeats current slot, evidence, quota and binding checks. The database permits
only immutable content plus the `COMMITTED -> ACTIVE` state/receipt fields.

Tests and controlled validation cover post-approval slot archival and quota
change, changed proposal/receipt hashes, stale validation, wrong owner/tenant,
cross-owner technical role use, direct state skips and all three changed
replay cases. Proposal, committed and active content SHA-256 identities must
be equal.

## Persistence and isolation

Migration `0014_step30_user_approval_commit_activation.sql` reuses the
existing proposal, approval, commit, transition and memory-item tables. It
adds replay and receipt projections, exact payload checks, transition receipt
hashes, deterministic unique indexes, no-skip triggers, serialized quota
checks, scalar RLS lineage helpers, FORCE RLS verification and exact grants.
No parallel memory store or Step 31 retrieval table is introduced.

## Validation evidence

The final pre-commit gates passed:

- Step 30 focused module: 16 tests, zero failures/errors;
- explicit Step 17–30, RLS, persistence, authority, isolation and contract
  regressions: 640 tests, zero failures/errors;
- full repository discovery: 1,724 tests, zero failures/errors;
- Python compile-all, contract validation and `git diff --check`: pass;
- offline migration validation: 14 migrations, pass;
- migration 0014 SHA-256:
  `8c8fce3bb6263698a43adfd844e67dd4d55c78f7b584468a8e10cd1edc0ca95f`;
- disposable CockroachDB v26.2.4 controlled validation: pass, with exact
  migration replay, RLS/FORCE RLS catalog checks, approval/commit/activation
  replay and TOCTOU negatives, cross-owner/cross-tenant isolation and full
  cleanup;
- controlled-validation digest:
  `e8e9db4edd28630f16f70ec548d27c68c0d45aac1f79d3d50a2e7a5ee1b60e9e`.

The canonical controlled-validation result is recorded in
`docs/evidence/personal-memory/step30-user-approval-commit-activation-validation.json`.
The evidence contains only sanitized IDs, hashes, status matrices, authority
zeroes, isolation results and cleanup status. It contains no secret or raw
private patch text.

## Authority and later-step proof

- Human owner approval is required; validation is not approval.
- Commit Helper cannot approve or change approved content.
- Active Personal Memory remains private and non-canonical.
- External execution authority: none.
- Active-patch retrieval: zero.
- Cross-model reuse: zero.
- Step 31: NOT STARTED.

## Known limitations

Step 30 1A activates an exact factual-correction patch but intentionally does
not make it discoverable. Retrieval, temporal/scope ranking, model-binding
reuse, supersession, revocation, export/deletion workflow integration and
shared promotion remain later roadmap work, beginning with Step 31.
