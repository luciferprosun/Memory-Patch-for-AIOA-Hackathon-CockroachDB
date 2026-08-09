# ADR-032: Draft V2 generation and layered claim verification

## Status

Proposed. It becomes accepted only when the Step 25 closure commit is
reachable on `origin/main`.

## Context

Step 24 produces an immutable, canonical Correction Packet and a separate
HMAC-SHA-256 receipt. A corrected model response is useful only if the Kernel
can prove that the packet was authentic, exact corrections were applied,
prohibited assertions were not repeated, citations remain packet-bound, and
model-based semantic judgment cannot erase deterministic contradictions.

## Decision

1. Draft V2 generation starts only after the exact packet hash, HMAC receipt,
   Draft V1, request, tenant, user, route, and scope lineage verify.
2. Draft V2 receives exactly Draft V1 plus the canonical verified Correction
   Packet and a fixed provider-neutral correction instruction.
3. The Step 22 pinned, tool-less provider boundary, identity, credential
   isolation, timeout, and bounded retry infrastructure are reused.
4. Generation occurs outside database transactions. Draft V2 uses stage 2 of
   the existing Step 4 drafts table; no migration or parallel store is added.
5. Draft V2 generation never implies verification. Step 23 exact-span parsing
   is reused for immutable Draft V2 claims.
6. Contract/schema, packet compliance, deterministic fact/date/source,
   citation, and evidence-binding checks run before semantic aggregation.
7. A bounded semantic verifier may provide only a typed candidate signal over
   packet-permitted claim/evidence identities. It is not authority.
8. Deterministic contradiction, temporal invalidity, source-authority facts,
   packet integrity, and invalid citation always defeat a semantic support
   signal.
9. Final Step 25 claim verdicts and the verification summary are immutable,
   ordered, and hash-bound. `VERIFIED`, `FAILED`, `INCOMPLETE`, and
   `CONFLICTING` remain distinct.
10. No retrieval, browsing, final-answer assembly, approval, execution, or
    Personal Memory capability is introduced.
11. The old Step 4 claim-verdict vocabulary is not overloaded with Step 25
    layered semantics. Durable verification persistence is deferred pending a
    coordinated schema decision.
12. Step 26 alone owns verified-answer assembly and the fail-closed output
    decision.

Step 26: NOT STARTED.

## Consequences

The same immutable generation input produces the same request identity; exact
returned bytes produce the same Draft V2 hash. Provider text itself may vary,
so bitwise model determinism is not claimed. Every generated factual claim is
independently assessed, and semantic uncertainty cannot be promoted to
verified support.

Draft V2 can be safely persisted using the existing tenant/user RLS boundary.
The richer verification result remains an immutable in-process/audit contract
until its exact durable vocabulary and upstream foreign-key chain are frozen.

## Rejected alternatives

### Trust the corrected model response

Rejected because a model can omit corrections, repeat prohibited claims,
invent citations, or self-certify.

### Use one model-verifier call as the final decision

Rejected because model agreement is not authority and cannot override exact
packet, date, source, or citation facts.

### Retrieve new evidence during verification

Rejected because Step 25 must verify against the frozen packet universe and
must not widen scope after correction.

### Add Step 25 database tables

Rejected because the existing drafts table already supports stage 2 and an
unreviewed verdict schema would create competing semantics.
