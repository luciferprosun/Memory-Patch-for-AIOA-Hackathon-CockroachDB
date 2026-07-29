# ADR-014: Source Registry, Provenance, and Publication Boundary

- Status: Accepted
- Date: 2026-07-29

## Context

Steps 4 through 6 established a tenant-ready CockroachDB schema, forced RLS,
serializable transactions, and durable idempotency. They did not define a
typed source registry, a complete provenance DAG, publication eligibility, or
an append-only publication lifecycle.

Step 7 S3 snapshot authority and Step 8 external-volume runtime integration
were explicitly deferred by the user. Step 9 therefore must remain useful
without pretending that source acquisition, Object Lock, parsing, ingestion,
or production storage integration exists.

Source metadata can inform policy without becoming authority. Models, HATs,
critics, registry rows, eligibility decisions, and publication state must not
approve, commit, execute, or authorize answers.

## Decision

Add one typed source package and forward migration `0006` with three tables:
`memory_patch.source_registry_entries`,
`memory_patch.source_provenance_edges`, and
`memory_patch.source_publication_events`.

Bind every registry entry to exact tenant/source/HAT scope, authority-neutral
metadata, license/access/redaction state, canonical scope digest, parser and
transformation identities, origin metadata, exact artifact identity, and an
optimistic publication pointer. Require exact `REGISTERED` genesis at the
typed service, repository, SQL constraint, and INSERT policy.

Represent provenance as a bounded, source-scoped DAG. Reject self-edges,
mixed scopes, conflicting replay, and cycles. Compute publication eligibility
from all reachable roots and edges, not one selected branch.

Use a declared publication transition graph. Append immutable events with a
monotonic sequence and previous-event digest. Bind actor type and reference,
eligibility, reasons, and event time into the event digest. Update the
registry pointer through compare-and-set in the same transaction.

Reuse the Step 6 serializable transaction runner and durable idempotency
service for registrations, edges, and transitions. Retry only SQLSTATE
`40001`. Keep external calls outside the transaction.

Enable RLS and FORCE RLS on all three tables. Reuse the existing tenant/user
context, retain `mp_schema_owner` ownership, and grant the runtime no DELETE.
Permit publication actor types only for trusted application, human review,
and migration boundaries. Explicitly reject model, HAT, and critic actors.

## Alternatives rejected

- Treating a URL, filename, authority label, or license label as sufficient
  source identity was rejected because each omits canonical scope and lineage.
- Binding eligibility to one arbitrarily selected root was rejected because a
  second reachable branch could change provenance without changing the
  decision.
- Allowing a non-genesis initial publication pointer was rejected because it
  bypasses the event chain.
- Mutable publication-history rows were rejected because they erase the
  evidence required to verify state and digest chains.
- Direct `REGISTERED -> PUBLISHED` was rejected because review and eligibility
  cannot be skipped.
- Model, HAT, or critic publication authority was rejected because these
  actors remain advisory.
- Retrying every SQL error was rejected; only structured `40001` is
  retryable.
- Implementing S3, the Step 8 adapter, or the Step 10 saga inside Step 9 was
  rejected as a roadmap and authority-boundary violation.
- A database cycle trigger was not added. The trusted typed repository rejects
  cycles before persistence, while SQL still enforces self-edge and immutable
  identity constraints.

## Consequences

Positive:

- registration starts from one exact, auditable genesis;
- scope, parser, transformation, artifact, and lineage facts are
  deterministic;
- every reachable provenance branch affects eligibility;
- exact retries remain idempotent and conflicting bindings fail closed;
- publication history is append-only and pointer-consistent;
- tenant and private-user isolation is enforced in SQL;
- actors and publication state grant no broader authority.

Constraints:

- the trusted application boundary must use the typed service for cycle
  detection;
- local single-node validation does not prove production transport or
  distributed behavior;
- source acquisition, parsing, chunking, S3, external-volume runtime wiring,
  and ingestion remain outside this decision;
- Step 10 remains operationally dependent on deferred Step 7.

The operating model is documented in
[`SOURCE_REGISTRY_PROVENANCE_PUBLICATION_STATES_1A.md`](../architecture/SOURCE_REGISTRY_PROVENANCE_PUBLICATION_STATES_1A.md).
