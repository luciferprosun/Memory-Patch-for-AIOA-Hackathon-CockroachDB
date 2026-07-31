# Memory Patch - Step 10 Idempotent S3-CockroachDB Ingestion Saga Closure 1A

## Status

`IMPLEMENTATION AND LIVE RECOVERY VALIDATION COMPLETE`

This record belongs to the intended single Step 10 closure commit. It becomes
completion evidence only when that commit is reachable on `origin/main`.

## Repository and scope

- authorized starting commit:
  `e93536626c105f5186ce7e2c89a419f5bf6c4b83`;
- branch `main`, with starting `HEAD == origin/main` and ahead/behind `0 0`;
- all surviving uncommitted files were attributable to Step 10;
- no interrupted Git operation existed;
- Steps 7, 8, and 9 remained complete;
- Step 11 parsing, normalization, and chunking was not started.

Step 10 adds orchestration only. The Memory Patch kernel remains semantic
authority, CockroachDB stores durable saga progress, Step 9 remains the
publication-policy boundary, and S3 plus the external volume remain storage
evidence. No distributed cross-system ACID guarantee is claimed.

## Implementation

The durable saga advances monotonically through:

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

Migration `0007_step10_idempotent_ingestion_saga` adds:

- deterministic saga runs and separate execution disposition;
- bounded compare-and-set worker claims;
- append-only transition events with previous-digest chaining;
- durable external intents and exact receipts;
- reconciliation and non-destructive orphan records;
- tenant/user RLS and FORCE RLS;
- no runtime delete grant, `BYPASSRLS`, retention bypass, or authority-bearing
  storage state.

Step 6 owns durable idempotency and SQLSTATE `40001` transaction retry. Step 8
owns local staging and fail-closed mount identity. Step 7 owns exact-version S3
and Object Lock evidence. Step 9 owns eligibility and legal publication
transitions. External calls occur outside database transactions.

Parser and validator ports produce typed synthetic validation receipts only.
No concrete parser, normalizer, chunker, embedding, vector index, model call,
or HAT runtime is implemented.

## First approved live attempt

The first approved live attempt reached `PUBLISHED` and completed the expected
external effects exactly once. Step 10 correctly remained uncommitted because
disposable-runtime cleanup used exact-PID force termination after the old
fixed 20-second SIGTERM wait.

- first verdict: `FAILED VALIDATION - NOT COMMITTED`;
- first root cause: `DISPOSABLE_RUNTIME_FORCE_KILL_USED`;
- all saga milestones before cleanup: PASS;
- external-volume artifact: created once, exact read-back PASS;
- S3 Object-Locked version: created once, exact-version read-back PASS;
- commit and push: NONE.

The preserved sanitized evidence is
[`step10-ingestion-saga-validation-failure.json`](../evidence/ingestion/step10-ingestion-saga-validation-failure.json),
with evidence digest
`78a7cfd94c41a40726f9c4bde5f6f6c441615acba384acebab825b5651ff5de4`.
It was not rewritten to conceal the failed attempt.

## Graceful-shutdown and evidence-ordering repair

The pinned v26.2.4 server exposed these bounded phases:

- connections: `0s`;
- initial wait: `0s`;
- jobs: `10s`;
- lease-transfer iteration: `5s`;
- transactions: `10s`.

The configured total is 25 seconds. The repaired bound is
`ceil(25) + 15 seconds scheduling cushion = 40 seconds`, with a test-only hard
cap of 120 seconds.

Before shutdown, the harness closes or rolls back transactions and reaps every
owned interactive SQL child and pipe. It resolves the one owned node, verifies
its RPC and SQL loopback bindings, and invokes `node drain --self --shutdown`
against the owned RPC port with `--drain-wait=40s`.

CockroachDB v26.2.4 reports process exit code 1 after a remote drain shutdown.
That outcome is accepted only when the drain command succeeds and sanitized
server-log evidence contains both `server drained and shutdown completed` and
`shutdown requested by drain RPC`. Any unmatched non-zero exit, missing drain
marker, panic, timeout, SIGKILL, remaining child, open port, or owned temporary
directory fails closure. Force kill remains exact-PID emergency cleanup only
and can never produce PASS.

The validation lifecycle now always combines the primary result and cleanup
result before writing evidence. Failure evidence preserves the first root
cause and separate downstream cleanup facts. Success and failure paths are
exclusive, canonical JSON is read back, and its evidence digest is verified
before the final verdict is returned.

## Zero-external-write recovery validation

The frozen recovery plan was bound to:

- worktree digest:
  `44921855ea686ea3f22f21a932066bb33d1858de651ee6a56f176cb2f163a927`;
- focused repair digest:
  `ec2bfe135effd197fc45d156b62cb91f7bf7447c1c45b5bc04d4a2068cb0fc2f`;
- recovery-plan digest:
  `95b581a3714c2bbac3a8fc72350a390ec00399c91ec3038de549bb64da784182`.

The continuation authorization permitted immediate execution because the
verified plan contained:

```text
new external-volume writes: 0
new S3 writes: 0
deletions: 0
retention changes: 0
CockroachDB: one disposable in-memory validation scope
repository: one exclusive sanitized success-evidence file
```

The recovery used a fresh loopback-only CockroachDB v26.2.4 runtime, applied
migrations `0001` through `0007`, verified checksum replay as a seven-migration
no-op, and created only ephemeral synthetic database records.

The saga reconciled the exact existing external artifact and exact existing S3
version into fresh durable intents and receipts. Both recovery wrappers made
their write method fail closed and counted attempted calls. Results:

- `REGISTERED`: PASS;
- `ACQUIRED_LOCAL`: PASS through exact existing-artifact reconciliation;
- `HASH_VERIFIED`: PASS;
- `SNAPSHOT_UPLOAD_PENDING`: PASS;
- `SNAPSHOT_UPLOADED`: PASS through exact existing-version reconciliation;
- `SNAPSHOT_LOCK_VERIFIED`: PASS;
- `PARSED`: PASS through a typed synthetic Step 10 port;
- `VALIDATED`: PASS through a typed synthetic Step 10 port;
- `PUBLISHED`: PASS through Step 9;
- event chain: 8 transitions;
- publication chain: 3 events;
- exact replay: same completed saga, no duplicate effects;
- conflicting replay: rejected with `IDEMPOTENCY_BINDING_CONFLICT`;
- new external-volume write attempts/calls: `0/0`;
- new S3 PutObject attempts/calls: `0/0`;
- duplicate publication event: NO.

## Existing storage evidence

The external artifact remains one regular exact 92-byte derived-staging file:

```text
ingestion/downloads/step10-validation-s3snap-96865bf2b400b16b5be6ba332d965168626dcf958c3ca494a1fb89c47be492c4.json
```

Its SHA-256 is
`61088c464f21622d0dccd28d41e6f041c9bf7abf165542262c9ea7f8d51241ca`.
There is no system-drive fallback and no target-bound incomplete staging
artifact.

The S3 object remains exactly one version in the existing Step 7 bucket:

- version ID: `kfDFfBsGlAR_KoQxDodzESlhebuYpAMx`;
- content length: 92;
- canonical SHA-256: the same exact payload digest above;
- Object Lock: `GOVERNANCE`;
- retain until: `2026-08-30T07:39:23Z`;
- version count after recovery: 1;
- delete markers: 0.

No object, version, file, retention setting, or unrelated record was deleted,
overwritten, or changed.

## Cleanup evidence

- shutdown method: `NODE_DRAIN_SELF_ON_RPC_WITH_SHUTDOWN`;
- observed drain elapsed time: `20.368s` within the derived `40s` bound;
- drain command and completion marker: PASS;
- server-log completion and drain-RPC exit markers: PASS;
- owned child processes reaped: PASS;
- exact server PID exited without SIGTERM or SIGKILL: PASS;
- force kill used: NO;
- all owned loopback ports closed: PASS;
- disposable database and role removed: PASS;
- owned temporary directory removed: PASS;
- persistent service created: NO;
- persistent database data created: NO.

The canonical successful recovery evidence is
[`step10-ingestion-saga-validation.json`](../evidence/ingestion/step10-ingestion-saga-validation.json),
with evidence digest
`1ea7a24769922d5b94b4c630d2dccfbb009957ddc2b2c2e5d01e1376f6ed93f3`.

## Validation

- compile/import validation: PASS;
- migration manifest and offline validation: PASS, seven migrations;
- fresh migration application: `7 applied / 0 skipped`;
- checksum replay: `0 applied / 7 skipped`;
- targeted Step 10 tests: `91/91`;
- Step 6 through Step 9 focused regressions: `247/247`;
- full repository suite after closure documentation: `724/724`;
- contract validator: PASS;
- exact CockroachDB v26.2.4 binary SHA-256:
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- RLS/FORCE RLS and security catalog: PASS;
- secret, credential, local-identity, destructive-surface, and Step 11 scope
  review: PASS;
- `git diff --check`: PASS.

## Roadmap state

```text
Step 7: COMPLETE
Step 8: COMPLETE
Step 9: COMPLETE
Step 10: COMPLETE AND PUSHED at actual closure commit
Step 11: NOT STARTED
```

Historical order remains explicit: Step 9 completed before the resumed Steps
7 and 8; Step 10 was then implemented and recovered. This closure does not
authorize or start Step 11.
