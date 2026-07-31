# Idempotent S3-CockroachDB Ingestion Saga 1A

## Purpose

This document defines the Step 10 runtime and persistence boundary. It
coordinates existing Step 6 through Step 9 services and does not create a
distributed transaction, a semantic-authority service, or a Step 11 parser.

## Authority model

| Component | Authority in Step 10 |
| --- | --- |
| Memory Patch kernel | Semantic authority |
| CockroachDB saga state | Durable orchestration authority only |
| Step 9 source service | Source eligibility and publication transition authority |
| S3 adapter | Immutable storage evidence only |
| External-volume adapter | Derived local staging evidence only |
| Parser and validator ports | Typed downstream receipts only |
| Models and HATs | No approval, commit, execution, or publication authority |

No storage response, worker, parser, validator, model, or HAT may directly
approve or publish.

## Non-negotiable invariants

- CockroachDB is the durable orchestration authority, not semantic truth.
- S3 is immutable storage evidence only.
- The external volume is derived staging only.
- Step 9 remains the publication authority boundary.
- No external call occurs inside a database transaction.
- No distributed ACID transaction is claimed.
- Parser and validator validation adapters are synthetic typed receipt
  boundaries only; they are not production parsing or validation services.

## Canonical lifecycle

```text
REGISTERED
  -> ACQUIRED_LOCAL
  -> HASH_VERIFIED
  -> SNAPSHOT_UPLOAD_PENDING
  -> SNAPSHOT_UPLOADED
  -> SNAPSHOT_LOCK_VERIFIED
  -> PARSED
  -> VALIDATED
  -> PUBLISHED
```

Each edge requires exactly one durable prerequisite intent or receipt digest.
The transition event and saga row update occur in one short serializable
CockroachDB transaction. The event sequence and previous digest create an
append-only hash chain.

`PUBLISHED` is terminal. Quarantine blocks publication. A milestone is never
used to represent a retry or backward recovery.

## Execution disposition

The execution disposition is separate from the canonical milestone:

- `READY`
- `CLAIMED`
- `RETRY_WAIT`
- `OPERATOR_REVIEW`
- `QUARANTINED`
- `COMPLETED`

Claims contain a digest, claim time, and expiry. A state-version
compare-and-set prevents duplicate workers. An active claim conflict is
retryable but cannot clear or replace the other worker's lease.

## Durable data model

### `ingestion_sagas`

Stores deterministic run identity, Step 9 source binding, exact snapshot
facts, current milestone, execution disposition, version counter, attempts,
retry time, worker lease, quarantine reason, terminal timestamp, and canonical
run digest.

The immutable identity includes tenant, source and HAT scope, optional owner,
knowledge version, idempotency key, Step 9 registry and scope digests, payload
hash and length, media type, contained local relative path, snapshot identity,
capture time, and retention time.

### `ingestion_saga_events`

Stores the append-only transition chain. Historical events cannot be updated
or deleted. Event identity is derived from all transition facts.

### `ingestion_external_effects`

Stores intent before external work and receipt after verified success. The
only permitted update converts the exact stored intent to its exact receipt.
Intent identity and receipt binding cannot change.

### `ingestion_orphans`

Stores unresolved external evidence and its non-destructive classification.
`cleanup_performed` is always false in 1A. A row cannot authorize publication
or deletion.

## Transaction boundary

Every service operation opens one serializable transaction through the Step 6
runner:

1. begin transaction;
2. set the trusted Step 5 request context;
3. read or compare-and-set repository state;
4. append or attach exact evidence;
5. commit;
6. close the connection.

Only SQLSTATE `40001` retries the callback. External work is preceded by
`assert_no_open_persistence_transaction()`. AWS, filesystem, parser,
validator, and publication orchestration calls are not inside a database
transaction.

## Orchestration by milestone

### `REGISTERED -> ACQUIRED_LOCAL`

- Require the exact Step 9 registry entry and registration genesis.
- Commit an acquisition intent.
- Inspect only target-bound Step 8 incomplete staging names.
- Reconcile an existing exact artifact or acquire the injected bytes.
- Use `INGESTION_STAGING` atomic no-overwrite write.
- Verify exact payload, SHA-256, length, mount identity, and no fallback.
- Attach the local storage receipt.

No general network downloader is implemented.

### `ACQUIRED_LOCAL -> HASH_VERIFIED`

- Commit a hash-verification intent.
- Read exact local bytes through Step 8.
- Verify payload equality, canonical SHA-256, byte length, snapshot identity,
  and manifest binding.
- Attach the verification receipt.

### `HASH_VERIFIED -> SNAPSHOT_UPLOAD_PENDING`

- Derive the Step 7 bucket reference and deterministic object key without an
  AWS call.
- Commit the S3 upload intent before any upload.

### `SNAPSHOT_UPLOAD_PENDING -> SNAPSHOT_UPLOADED`

- Reconcile the exact deterministic key before writing.
- If absent, call only the Step 7 `persist_snapshot` boundary.
- Retry only a bounded transient service failure.
- Reconcile after ambiguous service failure before retrying.
- Record the exact S3 version and structured storage evidence.

The adapter uses conditional no-overwrite semantics. It never assumes ETag is
SHA-256.

### `SNAPSHOT_UPLOADED -> SNAPSHOT_LOCK_VERIFIED`

- Require the upload receipt and exact version ID.
- Retrieve that exact version.
- Verify exact bytes, hash, length, metadata, versioning, lock mode, and
  retain-until timestamp.
- Attach a separate lock-verification receipt.

### `SNAPSHOT_LOCK_VERIFIED -> PARSED`

- Require the exact locked-version evidence.
- Reconcile or invoke the injected `ParserPort`.
- Accept only a typed receipt bound to the exact snapshot, S3 version, and
  input digest.

The production parser is not implemented in Step 10.

### `PARSED -> VALIDATED`

- Reconstruct the exact typed parse receipt from durable evidence.
- Reconcile or invoke the injected `ValidatorPort`.
- Require an accepted typed receipt bound to the exact parse output.

### `VALIDATED -> PUBLISHED`

- Reconstruct the exact validation receipt.
- Reconcile existing Step 9 publication first.
- Otherwise invoke the Step 9 publication service.
- Exercise only legal compare-and-set state transitions.
- Attach a publication receipt bound to the exact Step 9 event.

Storage success alone cannot reach this milestone.

## Reconciliation matrix

| Observed state | Action |
| --- | --- |
| Database intent, local artifact absent | Retry acquisition if policy permits |
| Exact local artifact, receipt absent | Verify through Step 8 and attach receipt |
| Local artifact hash mismatch | Quarantine, preserve evidence |
| Target-bound incomplete atomic artifact | Stop for operator review, do not delete |
| Database intent, S3 object absent | Retry only under bounded policy |
| Exact S3 version exists, receipt absent | Verify exact version and attach receipt |
| Database receipt, exact S3 version missing | Fail closed and require review |
| S3 bytes, metadata, or lock conflict | Quarantine and record orphan evidence |
| Exact parser or validator receipt exists | Attach or reuse it idempotently |
| Step 9 already published with exact event | Reconcile and reuse the event |

## Retry and failure policy

Database serialization, transient service failure, credentials, authorization,
contract, integrity, volume identity, S3 lock, parser, validator, publication,
idempotency conflict, and worker conflict are distinct typed classes.

Integrity and identity conflicts quarantine the saga. Service unavailability
without integrity conflict uses a retry or operator-review disposition.
Credentials and authorization failures never trigger blind retries or broader
permissions.

## Orphans and cleanup

Orphan handling is non-destructive:

- attach exact evidence;
- classify an exact duplicate;
- quarantine conflict;
- require operator review;
- record cleanup eligibility after policy conditions.

Step 10 contains no S3 delete operation, no Object Lock bypass, no external
artifact deletion, and no casual unquarantine switch.

## Tenant and user isolation

RLS and FORCE RLS cover all Step 10 tables. The trusted request context binds
tenant, optional user, and access mode. Shared sagas cannot carry a user.
Private sagas require the exact owner user. Runtime roles have no `BYPASSRLS`
and no runtime delete grant.

## Runtime and imports

Production imports do not create clients, open sockets, start subprocesses,
inspect mounts, access AWS, or open a database. All external boundaries are
injected.

Repository-controlled integration validation uses an exact CockroachDB
v26.2.4 binary in a disposable loopback-only single-node runtime with an
in-memory 640 MiB store, 64 MiB cache, 128 MiB SQL memory limit, and external
I/O disabled. No persistent database service or store is created.

## Step 11 boundary

Step 10 defines `ParserPort`, `ParseReceipt`, `ValidatorPort`, and
`ValidationReceipt`. It does not implement generic parsing, normalization,
chunking, embeddings, or vector indexing. Test and live-validation receipt
ports are explicitly synthetic and cannot be documented as production
parsers.

## Limitations

- Cross-system completion is eventually consistent, not ACID.
- Operator review may be required after ambiguous or conflicting evidence.
- Retained S3 versions remain until their retention and later cleanup policy
  permit an operator action.
- The disposable validation database is removed after each run, so sanitized
  evidence is the durable validation record.
