# Audit Ledger, Hash Chain and Audit Export 1A

## Boundary and authority

Step 33 adds an observational integrity layer over immutable facts already
created by Steps 1-32. The ledger records that an event was presented to the
audit boundary; it does not recreate the business operation and cannot
approve, commit, activate, revoke, delete, publish or execute anything.
Authority remains with the original typed receipt or result.

The public port has only three operations: typed append, read-only chain
verification and bounded owner export. Step 34 review queues, assignments,
decisions and UI are absent. Step 35 Personal Memory UI is absent.

## Implemented event inventory

`AuditEventType` is a closed registry for implemented facts. Its current
families are:

| Family | Recorded facts |
| --- | --- |
| Kernel and routing | request received, route and knowledge-policy decisions |
| Evidence and temporal | Evidence Bundle, temporal resolution, claim/evidence validation |
| Answer pipeline | Draft V1, Correction Packet, Draft V2 verification, verified or blocked answer |
| Personal Memory setup | slot create/configure/state, correction candidate |
| Proposal workflow | proposal, evidence binding, validation, awaiting approval |
| Approved private patch | human approval, technical commit, activation |
| Lifecycle | supersession, revocation, owner export, delete request/deletion |
| Shared review input | shared-promotion proposal and de-identification review required |
| Security/integrity | idempotency conflict, integrity failure, owner/tenant denial, policy block |

The registry contains no event for shared publication, Step 34 review
decisions or another unimplemented feature. Closed `AuditActorType` values
distinguish the human owner, Kernel, model adapter, Critic, Commit Helper,
Activation Service, system policy, future review service and migration
service. An actor tag is descriptive and grants no permission. A human event
is accepted only when its authenticated actor ID equals the owner; every
append, verification and export also binds the authenticated tenant.

Adapters map current Step 26, Step 30 and Step 32 immutable results into
bounded drafts. They retain receipt/result hashes and state facts, never raw
answer or Personal Memory text, and do not repeat business validation.

## Canonical event and payload

`AuditEventDraft` accepts only typed event, subject and actor families. It
binds tenant/owner/run identity, safe subject identity and hash, complete
policy identity when present, route and lineage hashes, sorted reason codes,
idempotency identity and canonical UTC times. Payload keys and values pass a
recursive 16 KiB minimization policy. Secret-shaped fields, authorization
material, machine paths and raw prompt, answer, source or Personal Memory
content fail closed.

The immutable `AuditEventEnvelope` binds every semantic dimension plus:

- `chain_id` and contiguous `sequence_number`;
- the explicit predecessor hash;
- the domain-separated payload digest;
- draft hash and idempotency key; and
- deterministic event ID and event hash.

Serialization reuses the repository canonical JSON contract: normalized
UTF-8 JSON, stable keys, typed enum values, no ambiguous floats and SHA-256.
The payload digest uses domain `MEMORY_PATCH_AUDIT_PAYLOAD_V1`; the event hash
uses `MEMORY_PATCH_AUDIT_EVENT_V1`. The raw safe payload is stored separately
but its digest is inside the event hash. All deserialization paths reconstruct
the frozen contract and reject detached hashes.

## Chain partition and genesis

The Step 33 policy partitions chains by exact `(tenant_id, owner_user_id)`.
This makes private history owner-isolated and avoids a global write hotspot.
A tenant-system chain is representable with a null owner for non-private
facts, but the Step 33 owner export API deliberately exports only an exact
authenticated owner's chain. Step 34 may later add a separate reviewer
capability without weakening this API.

`chain_id` is a deterministic SHA-256 identity over the partition policy and
tenant/owner tuple. Chain scope identifiers use a deliberately restricted
ASCII vocabulary so CockroachDB and Python derive identical bytes without a
second escaping implementation.

Sequence 1 must point to the domain-separated sentinel
`sha256("MEMORY_PATCH_AUDIT_GENESIS_V1")`. Later events must use exactly the
previous event hash. Missing predecessors, gaps, duplicates and reorderings
are invalid.

## Durable append and concurrency

Migration `0016_step33_audit_ledger_hash_chain` minimally extends the
existing Step 4 `memory_patch.audit_events` table with Step 33 projections
and creates `audit_chain_heads`. Earlier audit rows remain readable legacy
records but are not silently represented as chained Step 33 events.

The repository takes one short Step 6 serializable transaction:

1. resolve exact idempotent replay;
2. insert or lock the owner chain head;
3. recheck replay after lock acquisition to close the wait-race window;
4. construct the next event from the locked sequence/hash;
5. insert the immutable event; and
6. advance the guarded head by exactly one.

Unique `(tenant, chain, sequence)` and `(tenant, chain, idempotency_key)`
indexes close races. Exact replay returns the existing entry; changed input
under the same replay key fails. Appending uses O(1) head work and does not
scan historical events.

Ordinary runtime has SELECT/INSERT on audit events and no UPDATE/DELETE. A
database trigger rejects semantic UPDATE or DELETE even for a caller that
later receives such a relation privilege. The mutable head is only an
operational accelerator: its trigger accepts one-step movement only when the
corresponding just-inserted event exists and links the old hash. No ordinary
head DELETE is granted.

Both tables have RLS and FORCE RLS. Runtime access requires exact tenant and,
for a private chain, exact user context. The validation role has no BYPASSRLS.
The database role is a trusted repository boundary, never an end-user or
model credential; broader production credential hardening remains Step 36.

An immutable business result and its audit append are separate durable facts
unless a calling workflow explicitly places both in one supported database
transaction. Step 33 makes no distributed exactly-once claim. Callers may
report `audited=true` only after receiving a durable `AuditAppendReceipt`;
append failure cannot manufacture a success receipt.

## Verification and tamper detection

`AuditChainVerifier` is read-only. It verifies chain identity, genesis or a
range anchor, sequence continuity, unique sequence and event identity,
predecessor linkage, reconstructed event hash, payload digest, append receipt
and optional chain head. The typed result binds the checked range, first and
last hashes, failure sequence, closed failure reasons and versioned verifier
policy digest.

Verification never edits or repairs data. Controlled cases prove detection
of payload, event-type, subject-hash and predecessor changes, a deleted
middle event, reordering, forged insertion, duplicate sequence and a changed
head.

The SHA-256 chain is an integrity proof, not a digital signature or an
external notarization. Ordinary runtime cannot rewrite history, but Step 33
does not claim detection after a privileged administrator replaces an entire
chain and every local anchor. No S3 mutation or external mirror is performed
by this controlled closure.

## Proof-carrying owner export and redaction

`AuditExportRequest` is hash-bound to one tenant/owner chain, an exact
sequence range, maximum event count, redaction profile and request time.
Step 33 V1 rejects sparse event-type filtering because omitting interior
events without skip proofs could produce a misleading chain view.

Exports are ordered strictly by `(chain_id, sequence_number)`, capped at
1,000 events by default, 10,000 events hard maximum, one exact owner chain
per request and 8 MiB serialized bytes. It fetches one extra row to produce a stable
sequence-based continuation token.

Every range includes its predecessor anchor, first and last event hashes and
a verification result. `HASH_ONLY` export omits payload representation while
preserving the original payload digest and event hash with an explicit
redaction marker. `SAFE_METADATA` may expose only payload already accepted by
the ledger minimization policy. Redaction never recomputes the original event
hash over altered bytes and exports no secret or credential material.

## Later-step boundary

The ledger is not a review workspace, reviewer assignment mechanism,
moderation dashboard or Personal Memory UI. Step 34 owns human-review
workflow. Audit reading remains observational and cannot mutate business
state.
