# User Approval, Commit Helper and Activation 1A

## Boundary

Step 30 consumes one exact Step 29 state whose last edge is
`VALIDATED -> AWAITING_APPROVAL`. It owns only these three receipt-gated
edges:

```text
AWAITING_APPROVAL -> APPROVED -> COMMITTED -> ACTIVE
```

No edge may be skipped. `APPROVED` records owner consent but is neither a
durable technical commit nor activation. `COMMITTED` is an inactive durable
private patch. `ACTIVE` makes the patch eligible for future Step 31 lookup;
Step 30 contains no lookup, ranking, prompt injection, or cross-model reuse.

## Exact human approval

`PersonalMemoryApprovalRequest` binds the Step 29 proposal, evidence binding,
validation receipt, exact owner-private slot and scope, approval presentation
digest, expected state hash/version, request time, and a single-use nonce.
The presentation digest covers the exact proposal statement hash, limitations,
scope, target, evidence, validation policy, and owner identity.

Only an authenticated actor whose user identity equals the proposal owner can
produce `PersonalMemoryApprovalReceipt`. The actor type is closed to
`HUMAN_USER`; model, Critic, Kernel, and HAT output cannot instantiate an
approval actor. The receipt binds the complete request and versioned approval
policy. It contains no database credential or execution authority.

Approval replay identity is tenant- and owner-scoped. An exact replay returns
the existing receipt. Reusing the nonce with any changed semantic request is a
conflict, and it cannot authorize another proposal.

## Separate technical commit

`PersonalMemoryCommitRequest` contains no replacement text. It binds the
exact approval receipt, proposal and validation hashes, owner/tenant/slot,
expected `APPROVED` state hash/version, and a distinct commit idempotency
identity.

`PersonalMemoryCommitHelper` is a separate service using the non-login,
non-BYPASSRLS role `mp_personal_memory_commit_helper`. Normal application and
model-provider credentials do not include this role. The role cannot insert
approval records, update HAT/source registries, delete Personal Memory, or
perform external actions. CockroachDB v26.2 does not implement column-level
UPDATE grants, so the role has table UPDATE only behind
`personal_memory_spaces_s30_commit_guard`. That trigger recognizes the
dedicated role and permits exactly a one-step `candidate_quota_epoch`
increment while rejecting every semantic, identity, state and configuration
column change. The effective slot capability is therefore only the same-slot
quota serialization point.

CockroachDB foreign-key validation requires read access to the referenced
tenant and user identity rows. The helper therefore has `SELECT` only on
`tenants` and `users`, constrained by dedicated tenant-context and exact
owner-user RLS policies; it has no identity-table mutation privilege.
The same FK/RLS revalidation requires `SELECT` on `kernel_runs`; a dedicated
policy limits it to the exact current tenant and owner user, with no run
mutation privilege.
The parent-slot FK check also reads `audit_events`; its separate RLS policy
exposes only rows with an explicit personal-space owner matching the current
tenant/user context, and grants no audit mutation.

Immediately before commit, in the same short serializable transaction, the
helper reconstructs all hashes and reloads the current candidate, Step 29
proposal, evidence/validation receipt, Step 27 slot, quota policy, and exact
enabled provider-neutral model binding. Archived, suspended, delete-pending,
deleted, reconfigured, over-quota, stale, conflicting, or detached inputs fail
closed.

`CommittedPersonalMemoryPatch` preserves the exact approved statement and
scope. The proposal statement SHA-256, committed statement SHA-256, and later
active statement SHA-256 are identical. The patch and
`PersonalMemoryCommitReceipt` bind approval, validation, evidence, owner,
slot, model binding, commit sequence, role and technical actor. Commit row,
inactive memory item, proposal compare-and-set, event, quota accounting, and
idempotency completion share one transaction.

## Activation

`PersonalMemoryActivationRequest` binds the exact committed patch, commit and
approval receipts, proposal hash, owner/tenant/slot, expected `COMMITTED`
state hash/version, and an independent activation replay identity. The
activation service again reloads and verifies the current slot, evidence,
quota and model binding before changing the same immutable item from inactive
version 6 to active version 7.

The database trigger permits only the exact inactive-to-active mutation. All
content, lineage, owner, scope, evidence, commit and patch fields are
immutable. A same-slot epoch lock serializes concurrent active-patch quota
checks. `PersonalMemoryActivationReceipt` binds the unchanged content hash,
scope, model binding and technical activation actor.

## Trusted time and TOCTOU protection

The services require an injected application-owned trusted clock. Request or
model timestamps never decide freshness. Approval, precommit and
preactivation compare current trusted time to the Step 29 validation receipt,
while every request must not predate its source state. Changing a proposal,
slot, quota, binding, evidence status, state version, or receipt between
phases invalidates the later transition.

## Database enforcement and isolation

Migration `0014_step30_user_approval_commit_activation.sql` extends the
existing approval, commit, proposal, transition and memory-item carriers. It
adds immutable replay/receipt/payload projections, deterministic unique
indexes, exact state/content checks, transition receipt hashes, and two
guards. It does not create a parallel memory store.

RLS and FORCE RLS remain mandatory. Scalar SECURITY INVOKER helper functions
replace policy subqueries and bind each approval, commit, item, proposal edge,
and transition event to the current exact owner-scoped carrier. The commit
role receives only explicit table and function privileges. Cross-user and
cross-tenant visibility or mutation fails before service-level filtering.

Three independent Step 6 idempotency records protect approval, commit and
activation. Optimistic state hashes and database uniqueness make concurrent
exact replays converge and changed requests conflict. No partial commit or
activation survives transaction rollback.

## Authority and Step 31 boundary

An active Personal Memory patch remains private `PERSONAL_VERIFIED_PATCH`
data. It is not canonical evidence, shared knowledge, approval for any other
proposal, execution authorization, or permission to modify external systems.
Step 31 alone may implement active-patch retrieval and cross-model reuse under
new scope, temporal and model-binding checks.
