# ADR-013: CockroachDB Persistence, Idempotency, and Retry Boundary

- Status: Accepted
- Date: 2026-07-29

## Context

Steps 4 and 5 established a tenant-ready CockroachDB schema and SQL-enforced
tenant/user isolation. They intentionally did not provide application
persistence APIs, a serializable retry boundary, or durable idempotency and
resume state.

CockroachDB may require a whole transaction to be retried with SQLSTATE
`40001`. Retrying an individual statement can replay only part of a logical
operation. Retrying unrelated constraint, policy, authentication, or
configuration failures can hide permanent defects. External model, HTTP, S3,
or agent calls inside a database transaction would be unsafe to replay and
would keep transactions open across nondeterministic work.

The repository and validated Python environment contain no pinned PostgreSQL
DB-API driver. A production driver cannot be silently installed or introduced
unpinned.

Future framework adapters may need neutral external-artifact correlation, but
an external reference alone cannot be treated as globally unique, trusted,
evidence, authority, or successful execution.

## Decision

Implement the persistence core against a narrow typed DB-API-style protocol
and receive a connection factory from a future trusted composition root. Use
deterministic fake connections for unit tests and the pinned CockroachDB CLI
for live SQL conformance. Leave production driver/factory/pool wiring outside
Foundation 1A.

Use one canonical transaction runner that:

- begins an explicit `SERIALIZABLE` transaction;
- establishes the existing Step 5 trusted tenant/user context inside every
  attempt;
- reruns the complete callback only for structured SQLSTATE `40001`;
- allows exactly ten attempts with at most one second of backoff;
- rolls back, clears local transaction state, and discards the connection
  before sleeping;
- never retries any other or unknown SQLSTATE;
- invalidates the callback transaction handle before commit/rollback returns.

Expose an external-call guard backed by transaction-local context state.
Future external integrations must claim durable intent in one short
transaction, perform external work with no transaction open, then bind a
verified result or explicit interruption/failure in another transaction.

Add forward migration
`0005_step6_persistence_idempotency_retry_foundation` with one
`memory_patch.persistence_operations` table. Use separate shared and private
idempotency indexes, compare-and-set status transitions, bounded sanitized
failure metadata, and a complete-or-absent composite external-reference
identity. Make stable operation binding immutable.

Enable and force RLS on the new table. Keep ownership with
`mp_schema_owner`; grant `mp_app_runtime` only SELECT, INSERT, and UPDATE.
Reuse exact Step 5 tenant and user policy predicates. Preserve all four fixed
roles, historical Step 5 evidence, and absence of `BYPASSRLS`.

Provide a representative immutable repository for kernel-run identity, source
snapshots, evidence items, and audit events. Exact duplicate facts are
reusable; an identity bound to different facts fails closed.

## Alternatives rejected

- Installing an unpinned driver was rejected because dependency identity and
  compatibility would be unreviewed.
- Shelling out from production repository methods was rejected; CLI use is
  limited to the bounded validation harness.
- Statement-only retry was rejected because CockroachDB requires complete
  transaction replay.
- Retrying all SQLSTATE values was rejected because constraints, RLS,
  permissions, malformed context, and unknown failures are not transient
  serialization conflicts.
- An unbounded retry loop or backoff was rejected because it can conceal
  outages and hold resources indefinitely.
- External calls inside transaction callbacks were rejected because replay
  could duplicate side effects.
- A global idempotency key was rejected because tenant/user ownership is part
  of the security identity.
- Uniqueness on `external_ref` alone was rejected because systems, versions,
  adapters, artifact kinds, and tenants can reuse the same text.
- NVIDIA-branded schema/classes and external execution state were rejected as
  premature Step 6 coupling and authority expansion.
- A trigger-driven lifecycle was rejected because state authorization belongs
  to deterministic adapter compare-and-set logic.

## Consequences

Positive:

- retry behavior is bounded, deterministic, and `40001`-only;
- every retry receives a fresh transaction and exact trusted context;
- external-call boundaries fail closed while a persistence transaction is
  open;
- durable operation state distinguishes in-progress, interrupted,
  failed-final, and completed outcomes;
- exact duplicates reuse logical results while conflicting bindings fail;
- cross-tenant and cross-user operation state remains SQL-isolated;
- immutable evidence identities cannot silently change lineage or content;
- future adapters have a neutral, composite collision boundary without
  receiving authority.

Constraints:

- no production DB driver/factory or pool is wired in this step;
- application authentication, certificates, secrets, and Step 36 credential
  hardening remain separate;
- synthetic `40001` tests do not close the natural-contention deferral;
- the local CLI harness does not prove production transport or distributed
  contention frequency;
- external calls must be deliberately split into intent and result phases;
- Step 7 S3 snapshot authority and Object Lock remain future work.

The complete operating model and validation record are in the
[Step 6 architecture record](../architecture/COCKROACHDB_PERSISTENCE_IDEMPOTENCY_RETRY_FOUNDATION_1A.md).
