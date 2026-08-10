# Personal Memory Supersession, Revocation, Export, Deletion and Shared Promotion 1A

## Boundary

Step 32 extends the exact Step 27-31 Personal Memory lineage. It does not
create a second slot, patch, activation, or retrieval system. Every operation
binds one tenant, owner, Personal Memory space and exact Step 30 patch hash.

The authority classes remain separate:

- a private Personal Memory patch is non-canonical personalization;
- a shared-promotion proposal is review input, not shared active memory;
- neither object is canonical evidence or a source-registry publication;
- no lifecycle record grants model, provider, approval, publication, or
  execution authority.

## Supersession and historical immutability

`PersonalMemorySupersessionRequest` and
`PersonalMemoryPatchSupersession` bind the old and successor patch IDs,
patch/state hashes, exact owner/slot/scope, actor, effective time, replay
identity and state version. Both patches must have intact Step 30 ACTIVE
lineage and identical owner/tenant/slot/scope. The old content and all of its
receipts remain immutable.

The database records an append-only supersession relation and applies a
terminal overlay to the old `memory_items` row. Current Step 31 retrieval
suppresses the old patch and returns the applicable successor. A historical
query before `effective_at` may still select the old patch; the successor is
not backdated. Exact replay is idempotent and a changed pair under the same
replay identity fails closed.

## Revocation

`PersonalMemoryRevocationRequest` and `PersonalMemoryPatchRevocation` bind an
exact ACTIVE patch, owner/slot, state hash, effective time and replay
identity. Revocation may be initiated only by the authenticated human owner
or the exact deterministic lifecycle-policy actor. A model, Critic, HAT, or
arbitrary system actor cannot revoke memory.

Revocation adds an immutable receipt and terminal overlay. It disables future
retrieval without rewriting content or erasing approval/commit/activation
lineage. Revocation is not deletion and reports that distinction explicitly.

## Deterministic owner export

`PersonalMemoryLifecycleExportRequest` is authenticated-owner-bound and
targets one exact slot. `PersonalMemoryLifecycleExportBundle` contains a
deterministically ordered, bounded set of allowed slot and lifecycle records.
The bundle is canonical JSON, hash-bound, owner-private and replay safe.

Exports are capped at 1,024 records and 8 MiB. Recursive validation rejects
secret-like fields, credentials, machine-local paths and foreign-owner data.
An owner export neither performs de-identification nor creates a shared
promotion.

## Logical owner deletion

Deletion reuses the Step 27 `DELETED_PENDING` to `DELETED` slot transition.
`PersonalMemoryDeletionRequest` and `PersonalMemoryDeletionResult` bind the
exact owner, slot, patch/state hashes, expected version, tombstone identity,
time and replay identity. Step 32 implements logical deletion only:

- `logical_delete=true` and `physical_delete=false`;
- the private patch becomes retrieval-ineligible;
- private model bindings are removed and the slot becomes `DELETED`;
- immutable retention lineage and the tombstone remain;
- any independently reviewed shared artifact is not mutated.

Deletion is distinct from revocation and does not imply that a shared review
or publication was deleted.

## Shared-promotion review boundary

Step 30 private approval is not consent to share. A separate
`SharedMemoryPromotionConsent` from the authenticated owner is required for
the exact patch, target HAT, candidate shared-text hash and de-identification
policy digest.

`SharedMemoryPromotionProposal` retains the source patch provenance, exact
candidate shared statement hash, scope, consent hash, canonical-evidence
compatibility and privacy assessment. Its state is
`SHARED_PROMOTION_PROPOSED`, with `review_required=true`,
`shared_active=false`, `source_registry_published=false`, and
`canonical_evidence=false`. Step 32 provides no publication or activation
transition.

## De-identification

The versioned deterministic policy
`personal-memory-shared-deidentification-1a` redacts bounded obvious email,
account/private-ID and caller-supplied private-identifier patterns. The
assessment is reconstructed from the exact committed patch text; callers
cannot substitute detached candidate text or widen its scope. Candidate text,
policy digest, findings and source patch hash are all integrity-bound.

Deterministic redaction is not a proof that re-identification is impossible.
Accordingly, ambiguous or transformed content remains `REVIEW_REQUIRED`.
A model cannot self-certify privacy. Canonical-evidence conflict also remains
review-only and never creates authority.

## Persistence, isolation and replay

Migration `0015_step32_personal_memory_lifecycle` adds five append-only
owner-private record tables and bounded terminal metadata on the existing
Step 30 `memory_items` carrier. It adds no parallel patch store. Composite
foreign keys and service queries preserve tenant, owner and slot identity.

All five tables and `memory_items` use RLS and FORCE RLS. The ordinary runtime
role receives only the exact SELECT/INSERT operations plus the guarded
terminal UPDATE; it receives no DELETE or BYPASSRLS privilege. Security
invoker helpers re-check current owner context, slot snapshot and patch
lineage. Terminal updates are accepted only when their immutable receipt is
already present and exact. Serializable Step 6 transactions, uniqueness,
expected state versions and per-operation replay identities prevent partial
or conflicting lifecycle writes.

Each service operation obtains one owner-scoped transactional snapshot of the
slot, patch lineage and existing lifecycle records, then performs its receipt
insert and conditional terminal update in the same serializable transaction.
The terminal update is a compare-and-set over the exact active state/version;
a concurrent winner therefore makes every changed competing request fail
closed instead of producing a split receipt/state result.

CockroachDB validates inbound lifecycle foreign keys when the Step 30 Commit
Helper inserts a parent patch row. That narrow role therefore has relation
`SELECT` on only the five Step 32 child tables that reference `memory_items`
or `personal_memory_spaces`.
It has no Step 32 SELECT policy, so FORCE RLS exposes zero lifecycle rows, and
it has no Step 32 insert, update, or delete authority. CockroachDB also
requires `EXECUTE` on the terminal matcher and trigger function while planning
the Step 30 activation update. Those SECURITY INVOKER functions see no Step 32
rows for the Commit Helper and cannot create a lifecycle record or authorize
a terminal mutation by themselves.

For the symmetric Step 32 owner terminal update, CockroachDB also plans the
existing Step 30 Commit Helper policy. The ordinary runtime receives EXECUTE
on only `step30_commit_helper_authorized()` so that policy can be evaluated.
The invoker check returns false because the runtime is not a member of the
Commit Helper role; the grant conveys neither role membership nor commit
authority.

## Step 31 retrieval integration

Step 31 remains read-only. Current retrieval requires no Step 32 terminal
marker. Historical retrieval admits only an exact `SUPERSEDED` record whose
effective time is later than the query's `knowledge_as_of`, and excludes a
successor before its effective time. `REVOKED` and `DELETED` patches are never
returned. Retrieval performs no lifecycle mutation.

## Later-step boundaries

Step 32 does not implement the Step 33 global audit ledger, hash chain or
audit export. It does not implement the Step 34 human review workspace or the
Step 35 Personal Memory UI. The append-only receipts in this step are bounded
domain records, not the later global ledger. Shared publication remains
absent.
