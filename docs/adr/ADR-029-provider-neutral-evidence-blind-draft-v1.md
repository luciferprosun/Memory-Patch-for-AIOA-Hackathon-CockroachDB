# ADR-029: Provider-neutral, evidence-blind Draft V1 generation

## Status

Proposed. It becomes accepted only when the Step 22 closure commit is
reachable on `origin/main`.

## Context

Step 21 produces verified temporal, conflict, freshness, and evidence status.
The correction pipeline needs a genuine baseline answer that has not seen any
of that correction evidence. The provider must also remain replaceable and
must never inherit Kernel database, approval, commit, or execution authority.

## Decision

1. The model receives only a minimal generic instruction and the exact
   original query. Step 18-21 evidence and correction data remain out of band.
2. The Kernel API is the provider-neutral `DraftV1Provider` protocol. Step 22
   V1 pins `moonshot-ai` and `moonshot-v1-8k` in a digest-bound config.
3. The hosted provider exposes a stable declared model ID but no immutable
   deployment or weight revision. That limitation is explicit and hash-bound;
   no false immutable-version claim is made.
4. Tools, function calling, web browsing, and code execution are disabled.
   Tool-call responses fail closed. Returned text is inert, untrusted data.
5. Provider identity, prompt template, generation parameters, timeout, retry,
   original-query digest, Step 21 lineage, result, and exact Draft V1 bytes
   are independently hash-bound.
6. Provider credentials remain in the machine-local environment and never
   enter the request contracts, persistence records, or committed evidence.
   Provider code has no DB, AWS, approval, commit, or memory-write capability.
7. Model calls happen outside database transactions. Completed Draft V1 is
   persisted afterward in a short transaction through the existing Step 4
   drafts table and Step 5 tenant/user RLS.
8. Retry is bounded to two attempts and only safe transient classes. Local
   persistence is idempotent; provider execution is not claimed exactly once.
9. Provider output cannot change route, policy, evidence, approval, execution,
   or memory state.
10. Step 23 owns claim extraction and evidence binding.

Step 23: NOT STARTED.

## Consequences

A later verifier can compare a byte-exact, genuinely uncorrected Draft V1 to
evidence-derived corrections. Exact replay avoids duplicate local drafts, and
the provider can be replaced only by a new explicit identity/config decision.

The approved hosted account may be unavailable or out of quota. This is an
operational model failure, not permission to choose an arbitrary provider or
to inject evidence into the prompt.

## Rejected alternatives

### Send the Evidence Bundle to improve V1

Rejected because it would erase the measurable V1-to-V2 correction boundary.

### Give the model retrieval or database access

Rejected because provider output is not authority and a Draft V1 adapter does
not need those capabilities.

### Retry indefinitely or merge attempts

Rejected because completion after a timeout can be unknown and merged outputs
would not be one exact provider response.

### Add a Step 22 draft table

Rejected because the existing tenant-scoped, immutable Step 4 drafts schema
already represents Draft V1 safely.
