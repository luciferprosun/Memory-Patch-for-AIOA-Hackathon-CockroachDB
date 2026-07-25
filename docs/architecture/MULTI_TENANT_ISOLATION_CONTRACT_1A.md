# Multi-Tenant Isolation Contract 1A

## Application guard

Every user-owned object carries `tenant_id`, `user_id`, and
`personal_memory_space_id`. `MemoryOwnership`, `verify_ownership()`, and
`verify_run_ownership()` require exact matches. Missing tenant or user context
fails closed.

The contract prevents:

- User A from reading or targeting User B's space;
- Tenant A from targeting Tenant B;
- a model binding from transferring ownership;
- a Kernel or Critic event for User A from targeting User B;
- shared Knowledge HAT retrieval from including private personal memory;
- retrieval from a revoked, suspended, archived, deletion-pending, or deleted
  space.

Ownership and trust are independent dimensions. Raising the trust of a personal
patch never makes it visible to another owner.

## Critic Prompt Loop boundary

`CorrectionCandidate` carries its tenant, user, personal space, run, and model
binding. Validation binds those values to the originating `KernelRunIdentity`.
Its state vocabulary is only `DETECTED` and `PROPOSED`.

Knowledge Kernel, Knowledge Hub, Critic Prompt Loop, model verifier, user, or
human reviewer may produce a candidate. A candidate is not approval, committed
memory, active memory, canonical evidence, or action authorization. There is no
Critic path to `APPROVED`, `COMMITTED`, or `ACTIVE`.

## Personal-to-shared boundary

A personal patch never changes visibility automatically. Promotion creates a
new `SharedPromotionProposal` with a new ID and keeps the source patch
unchanged. Independent evidence revalidation, private-data classification,
de-identification when required, HAT scope, domain review, domain approval, and
separate technical commitment are mandatory stages. User approval alone is
insufficient.

## Defense in depth

These application guards are necessary but are not a claim that database
Row-Level Security exists. A later CockroachDB step must enforce matching
tenant/owner predicates, transaction boundaries, and service identities at the
persistence layer. Both layers are required.

Private content must not be placed on a common unfiltered change-data-capture
path. Future export must apply tenant-aware filtering, payload classification,
and protected references before publication. Audit metadata excludes raw
documents, complete prompts/responses, and secrets.
