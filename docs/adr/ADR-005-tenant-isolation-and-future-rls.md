# ADR-005: Require Application Guards and Future Database RLS

- Status: Accepted
- Date: 2026-07-25

## Context

Application checks alone can regress, while database controls alone cannot
validate all in-memory routing and proposal relationships.

## Decision

Step 1A enforces exact tenant/user/space ownership in application contracts and
negative tests. A future CockroachDB step must add matching Row-Level Security
and service identities; this ADR does not claim they exist now.

## Consequences

Missing context fails closed. Kernel and Critic events cannot target another
owner. Shared retrieval cannot include private memory. Persistence work must
re-audit the same invariants rather than replace them.
