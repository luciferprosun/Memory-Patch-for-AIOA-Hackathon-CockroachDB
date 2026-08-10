# ADR-040: Append-only owner-partitioned audit hash chain

## Status

Proposed. It becomes accepted only when the Step 33 closure commit is
reachable on `origin/main`.

## Context

Steps 1-32 produce immutable security-relevant results and Personal Memory
receipts, but the repository needs one normalized, verifiable audit view.
That view must detect tampering and support a bounded private export without
turning audit metadata into business authority or duplicating private text.

A single global chain would serialize unrelated tenants and owners. A debug
log would not provide canonical hashes, deterministic order or isolation.
Redacting stored payload in an export can also invalidate proof if the
original digest is not retained explicitly.

## Decision

1. Step 33 audit rows are append-only. Ordinary runtime has no UPDATE/DELETE
   grant and a trigger rejects semantic mutation independently of privileges.
2. Chains are partitioned by exact tenant and owner. A null-owner tenant
   system partition is supported, while V1 owner export remains exact-owner
   only.
3. The chain ID, explicit genesis sentinel, payload digest, event hash and
   receipts use the existing canonical JSON and SHA-256 utilities with
   domain separation.
4. A Step 6 serializable transaction locks a bounded chain-head row, inserts
   exactly one event and advances the head by one. Unique sequence and replay
   keys protect concurrency and idempotency.
5. The ledger mirrors typed business facts. An audit event never creates
   approval, commit, activation, revocation, deletion, publication or
   execution authority.
6. Audit storage minimizes private content. Safe identifiers, result hashes,
   state and reason codes are preferred over prompts, answers, source chunks
   or Personal Memory statements.
7. Verification is deterministic and read-only. It checks genesis/range
   anchor, sequence, predecessor, event/payload hashes, receipts and head, and
   never repairs a broken chain.
8. Owner export preserves canonical chain order and range proof. Hash-only
   redaction omits representation but retains the original payload digest and
   marks the omission instead of pretending the event hash covers redacted
   bytes.
9. `audit_events` is extended rather than replaced. Existing legacy rows are
   not falsely claimed as Step 33 chained events.
10. RLS and FORCE RLS enforce tenant/owner reads and writes. The runtime role
    has no BYPASSRLS and no unrestricted all-tenant export.
11. Step 34 owns human-review workspace, queues and decisions. Step 35 owns
    the Personal Memory UI.

`Step 34: NOT STARTED`.

## Consequences

Append work is O(1) against a locked head. Full verification and bounded
export are explicit read operations. Owners receive independently
recomputable sequence and hash proof without raw private memory.

The owner partition avoids global contention but means a cross-owner review
view needs a distinct, authorized later capability. Earlier business writes
and later audit appends are not claimed to be distributed-transaction atomic;
the audit receipt is the only valid proof that append succeeded.

## Rejected alternatives

### One global chain

Rejected because it creates unnecessary contention and conflicts with owner
privacy boundaries.

### Mutable audit records with a latest checksum

Rejected because historical rewriting or deletion would be hard to detect
and would weaken receipt lineage.

### Store all raw business payloads

Rejected because audit proof generally needs hashes and safe state metadata,
not duplicated private text or credentials.

### Export redacted payload as though it were the hashed original

Rejected because that silently breaks verification. The export keeps the
original digest and an explicit redaction marker.
