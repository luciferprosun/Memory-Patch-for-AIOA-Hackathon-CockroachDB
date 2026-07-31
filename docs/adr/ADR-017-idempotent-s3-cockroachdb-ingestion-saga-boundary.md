# ADR-017: Idempotent S3-CockroachDB ingestion saga boundary

- Status: Accepted and live-recovery validated for Step 10 closure
- Date: 2026-07-31
- Scope: Step 10, Idempotent S3-CockroachDB Ingestion Saga 1A

## Context

Memory Patch must coordinate source registration, external-volume staging,
immutable S3 snapshot storage, downstream parse and validation receipts, and
publication state. Those systems do not share a transaction manager. Treating
them as one ACID unit would create false guarantees and make crash recovery
unsafe.

Existing production boundaries already assign narrow responsibilities:

- Step 6 owns serializable CockroachDB transactions, SQLSTATE `40001` retry,
  and durable idempotency.
- Step 7 owns deterministic S3 keys, exact version IDs, checksums, metadata,
  versioning, and Object Lock evidence.
- Step 8 owns fail-closed external-volume identity, contained operation roots,
  exact-byte atomic no-overwrite writes, and read-back evidence.
- Step 9 owns source identity, provenance, eligibility, and legal publication
  transitions.
- The Memory Patch kernel remains the semantic authority.

Step 10 needs a durable orchestration boundary without absorbing any of those
authorities or implementing the Step 11 parser.

## Decision

CockroachDB is the durable authority for ingestion orchestration state. A saga
run advances through this exact monotonic milestone sequence:

1. `REGISTERED`
2. `ACQUIRED_LOCAL`
3. `HASH_VERIFIED`
4. `SNAPSHOT_UPLOAD_PENDING`
5. `SNAPSHOT_UPLOADED`
6. `SNAPSHOT_LOCK_VERIFIED`
7. `PARSED`
8. `VALIDATED`
9. `PUBLISHED`

Milestones never move backward and cannot be skipped. Retry, active claim,
quarantine, operator review, and completion are represented by a separate
execution disposition.

### Durable identity and replay

The run identity is derived from canonical immutable facts rather than a
random identifier or timestamp alone. The same idempotency key and same
canonical request return the same saga. A conflicting key binding fails
closed before new external work.

Step 6 `persistence_operations` remains the durable idempotency authority for
saga registration and Step 9 publication transitions. Only a structured
CockroachDB serialization failure with SQLSTATE `40001` may retry the
database callback.

### External intent and receipt

Every side effect has a deterministic effect identity and a durable intent.
The intent is committed before the external call. A receipt is attached only
after exact evidence has been verified.

The seven effect kinds are:

- `ACQUISITION`
- `HASH_VERIFICATION`
- `S3_UPLOAD`
- `S3_LOCK_VERIFICATION`
- `PARSE`
- `VALIDATION`
- `PUBLICATION`

External calls occur outside CockroachDB transactions. A database retry can
never repeat an external call merely because the database callback retried.

### Worker concurrency

A worker acquires a bounded lease through compare-and-set. A second worker
cannot claim an active lease. An expired lease can be reclaimed only when the
stored state version and expiry still match. Each successful transition clears
the claim. A claim conflict does not clear another worker's lease.

### Evidence chain

Every milestone transition appends an immutable event containing:

- sequence number;
- previous event digest;
- from and to milestones;
- reason and actor boundary;
- idempotency reference;
- exact prerequisite receipt digest;
- canonical event digest and timestamp.

Historical saga events are append-only. The database rejects mutation or
deletion of event history.

### Storage boundaries

External-volume staging uses only the Step 8 `INGESTION_STAGING` operation.
It performs a fresh mount and device check, uses a deterministic contained
relative path, refuses fallback storage, refuses overwrite, detects
target-bound incomplete atomic artifacts, and verifies exact bytes.

S3 upload uses only the Step 7 adapter. A pure storage plan supplies the
deterministic key before the write. Successful progression requires one exact
version ID, checksum and length equality, metadata equality, Object Lock mode
and retain-until equality, and exact-version payload read-back. ETag is never
treated as SHA-256.

S3 and the external volume remain storage evidence only. Neither can publish a
source or establish semantic truth.

### Publication and Step 11 boundaries

Step 9 remains the only publication policy boundary. Step 10 calls its service
for the legal sequence `REGISTERED -> REVIEW_REQUIRED -> ELIGIBLE ->
PUBLISHED`. Direct publication-table mutation is not part of the domain
service.

Step 10 defines narrow typed parse and validation receipt ports. A receipt must
bind the exact source, saga, snapshot, S3 version or parse output, component
name and immutable version, contract version, output digest, and completion
time. Concrete parsing, normalization, and chunking remain Step 11.

The controlled live validation may use ports explicitly marked
`synthetic_validation_boundary=true`. This proves orchestration only and is
not production parser evidence.

### Failure, quarantine, and orphan handling

Typed failure classification separates:

- database serialization retry;
- bounded transient service failure;
- expired credentials;
- authorization failure;
- contract violation;
- data-integrity mismatch;
- external-volume unavailability or unsafe identity;
- S3 lock failure;
- parse or validation failure;
- publication ineligibility;
- conflicting replay;
- active worker conflict;
- operator review.

Identity, binding, or integrity conflicts require quarantine. A transient
outage does not become quarantine without an integrity conflict.

Orphan records are evidence and never authority. Resolution can attach exact
evidence, classify an exact duplicate, quarantine a conflict, require operator
review, or mark future cleanup eligibility. Step 10 does not delete retained
S3 versions, bypass retention, or delete unknown external artifacts.

### Isolation

The four Step 10 tables use the existing Step 5 tenant and user request
context:

- `ingestion_sagas`
- `ingestion_saga_events`
- `ingestion_external_effects`
- `ingestion_orphans`

RLS and FORCE RLS are enabled. Runtime roles have no `BYPASSRLS`. Shared
records require tenant-shared context. Private records require the exact user
context. Append-only tables grant no runtime update or delete.

## Consequences

### Positive

- A crash after external success can be reconciled without blind duplicate
  writes.
- Exact replay is safe across process restarts.
- S3 Object Lock and external-volume audit evidence remain non-destructive.
- Step 9 publication policy cannot be bypassed by storage success.
- The Step 11 parser can be added later behind an already typed boundary.

### Costs and limitations

- The workflow is eventually consistent across systems.
- Durable intent may temporarily exist without a receipt.
- Conflicting external evidence can require operator review.
- Retained S3 versions cannot be cleaned up by this step.
- The disposable CockroachDB validation plane is test-only and creates no
  persistent production database.

## Rejected alternatives

- Distributed ACID across CockroachDB, S3, and a filesystem.
- External calls from inside retryable database transactions.
- Timestamp-only or random-only idempotency.
- ETag as a content digest.
- S3 storage success as publication eligibility.
- Raw filesystem writes that bypass Step 8.
- Direct S3 client construction inside the saga domain.
- Automatic deletion of orphan evidence.
- A concrete parser, normalizer, or chunker in Step 10.
