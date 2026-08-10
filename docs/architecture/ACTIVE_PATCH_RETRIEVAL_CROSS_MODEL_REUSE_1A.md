# Active Patch Retrieval and Cross-Model Reuse 1A

## Boundary

Step 31 consumes the exact Step 30 `ACTIVE` Personal Memory patch and adds a
read-only, owner-private query-time context layer. It does not create another
active-memory path and never retrieves `DETECTED`, `PROPOSED`,
`EVIDENCE_BOUND`, `VALIDATED`, `AWAITING_APPROVAL`, `APPROVED`, or
`COMMITTED` state as usable memory.

Retrieval does not approve, commit, activate, rewrite, supersede, revoke,
delete, export, or promote a patch. Step 32 remains the sole owner of durable
supersession, revocation and personal-to-shared promotion.

## Immutable retrieval contracts

`ActivePatchRetrievalRequest` binds the authenticated tenant/user, request and
route hashes, selected HAT identity, exact effective scope, one Personal
Memory space, a Step 22 `ProviderIdentity`, query-text digest,
`knowledge_as_of`, evaluation time, result bound, policy digest and request
hash. Raw query text is not retained in the contract.

For every considered record, `ActivePatchAssessment` records exact
tenant/owner/slot/scope/temporal/model-binding and current-canonical-evidence
outcomes without changing patch state. `RetrievedActivePatch` preserves the
candidate, proposal, validation, approval, commit and activation hashes plus
the exact Step 30 patch/content identity.

`ActivePatchRetrievalResult` contains deterministic ordered eligible patches,
bounded excluded assessments, explicit truncation hashes, reason codes and a
result hash. `PersonalMemoryContextEnvelope` labels the payload
`PRIVATE USER MEMORY - NON-CANONICAL`, binds its model/request/route/owner
identity and fixes `canonical_evidence_authority=false`.

## ACTIVE-only integrity gate

The repository hard-filters by tenant, owner, Personal Memory slot, HAT scope,
private target/visibility/trust class, active/non-revoked flags, Step 29/30
state versions and exact `ACTIVE` lifecycle. The row parser reconstructs and
verifies the full Step 29 and Step 30 immutable lifecycle before returning a
candidate. It then proves that:

- activation, commit, approval, validation, proposal and candidate lineage is
  intact;
- persisted patch content and scope equal the exact committed patch;
- active, committed and approved statement hashes remain identical;
- the current Step 27 slot is owner-matching and retrieval-eligible.

Any detached hash, receipt, state, owner, tenant or slot fails closed. A
committed-but-inactive item is never returned.

## Scope and temporal applicability

The request must reconstruct the exact current Step 17 route and Step 21
temporal result. Patch scope must equal the query route's canonical effective
scope; a private patch cannot widen or narrow the route. Temporal evaluation
uses the trusted Step 21 evaluation time and `knowledge_as_of`. Existing
`valid_from`, `valid_until` and `expires_at` values are honored. Missing
temporal information that is required for a bounded window is ineligible,
never guessed.

## Model bindings and cross-model reuse

The patch itself remains provider/model independent. Applicability reuses the
Step 22 `ProviderIdentity` and current Step 27 exact-model bindings. The model
binding captured by Step 30 must still be enabled, and the query model must
match an enabled binding on the same exact slot. A binding grants only
query-time applicability; it cannot change owner, scope, content or patch
state.

The same patch ID, patch hash and content hash can therefore be returned to
two distinct permitted provider/model identities without duplicating or
rewriting the memory. An unbound third identity is denied. No Gemma-specific
type, storage path or runtime dependency exists, and no provider call is
needed to prove this property.

## Canonical-evidence separation

Personal Memory is an additional private personalization layer after the
canonical route/evidence pipeline. It is not an Evidence Bundle item, source
registry publication, citation authority, source-authority upgrade or answer
authority. The retrieval policy requires a current Step 21 status that allows
answer use, no current conflict, fresh/applicable selected evidence, and an
exact match between the proposal's canonical evidence references and the
current selected evidence identities.

If current canonical evidence is conflicting, stale, unavailable,
insufficient, invalid, or no longer confirms the bound evidence identities,
the patch is suppressed for that request. Suppression is computed and
hash-bound; it does not persist a Step 32 revocation or supersession.

Structured model context keeps system/kernel policy and canonical evidence
authoritative, then adds bounded private memory as explicitly non-canonical
personalization. Private memory cannot override route scope, canonical facts,
source authority, approval policy or execution authorization.

## Isolation, query shape and bounds

The existing Step 27-30 tables retain RLS and FORCE RLS. The SQL query is
parameterized and filters exact tenant/user/slot/ACTIVE state before Python.
The ordinary application role has no BYPASSRLS. Same-tenant cross-user and
cross-tenant sessions see no row.

No new migration is required. The existing
`memory_items_scope_retrieval_idx` covers tenant, HAT scope, active/revoked
state and trust class. The repository fetch is capped at 129 rows to detect a
hard 128-candidate ceiling. The public default result limit is 8 and the
maximum is 32. Ordering and truncation identities are deterministic and
hash-bound.

## Step 32 boundary

Step 31 is read-only for semantic Personal Memory state. It adds no
supersession, revocation, persistent stale marking, export/delete change,
shared publication, model call, external action or execution authority.
Step 32 owns those later lifecycle decisions.
