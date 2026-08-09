# ADR-031: Correction Packet construction and integrity

## Status

Proposed. It becomes accepted only when the Step 24 closure commit is
reachable on `origin/main`.

## Context

Step 23 freezes exact Draft V1 claims, evidence links, and evidence-candidate
assessments. Step 25 will need deterministic correction input without gaining
retrieval, source-authority, policy, approval, or execution powers. A plain
JSON object or model-written correction prompt cannot provide that boundary.

## Decision

1. Step 24 accepts only the verified Step 23 `PacketInputSnapshot`.
2. The versioned `CorrectionPacketV1A` reuses upstream claim records and binds
   every Step 20/21/22/23 identity, route, tenant, user, HAT, and scope fact.
3. Required corrections and prohibited claims are derived by fixed rules from
   Step 23 candidate assessments. No model chooses or formats them.
4. Refuted claims are removed/prohibited, unverified claims are qualified,
   temporal and authority mismatches receive explicit correction types, and
   supported claims are preserved.
5. Conflicts preserve both evidence sides and require qualification. Rank,
   modality count, and vector similarity cannot resolve them.
6. Citations bind immutable evidence-link, candidate, source/version/chunk,
   content, authority, publication, and temporal identities. Step 24 performs
   no retrieval.
7. The repository's canonical JSON implementation is the sole serializer.
   Stable ordering and bounded fields make semantic replay byte/hash stable.
8. `packet_hash` is the public semantic identity. A separate domain-separated
   HMAC-SHA-256 receipt provides Kernel-side authenticity/integrity.
9. HMAC key material is runtime-only and never enters a packet, receipt,
   provider/model context, committed artifact, or log representation.
10. No migration is added. Existing Step 4 tables remain the approved durable
    target, but persistence is deferred until the complete upstream run,
    Draft, claim, scope, route, and action-policy rows are available together.
11. A Correction Packet is non-authoritative correction data. It grants no
    approval, execution, publication, HAT activation, or memory-write power.
12. Step 25 owns Draft V2 generation and layered final verification.

Step 25: NOT STARTED.

## Consequences

The same frozen Step 23 input and policy always produces the same packet bytes
and hash. A trusted runtime may authenticate the packet under a named key
without changing semantic content. Non-sufficient, stale, conflicting, or
unavailable evidence can still produce an honest bounded packet, but the
evidence status is preserved and never upgraded.

Coordinated durable packet persistence remains future orchestration work. This
is safer than inventing missing route/action-policy values or silently writing
partial upstream lineage.

## Rejected alternatives

### Let a model compose the packet

Rejected because packet construction must be deterministic, replayable, and
unable to invent evidence or corrections.

### Store an HMAC key or authenticator inside packet semantics

Rejected because the key must remain Kernel-side and key rotation must not
change the semantic packet hash.

### Add a new Step 24 packet table

Rejected because Step 4 already owns correction-packet persistence and a
parallel table would create competing authority and RLS boundaries.

### Guess missing legacy persistence columns

Rejected because route/action-policy identities are security semantics, not
convenience defaults.
