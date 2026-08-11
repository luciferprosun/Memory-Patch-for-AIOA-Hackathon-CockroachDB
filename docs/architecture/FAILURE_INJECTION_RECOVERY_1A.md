# Failure Injection and Recovery 1A

## Status, base, and scope

Step 37 is implemented from the exact Step 36 base
`f7882eed42534fb5c07bf55694886a2fca11823e`. This document defines the
failure-injection and recovery architecture. Sanitized controlled evidence and
the closure record bind validation outcomes separately; this document does not
invent a Git closure commit SHA or push result.

The scope is a bounded deterministic campaign over failure domains already
implemented by Steps 6-36:

- CockroachDB connection, transaction, serialization, and acknowledgement
  failures;
- process interruption and durable resume;
- Step 10 ingestion-saga checkpoints;
- provider timeout, transient failure, response loss, authentication failure,
  malformed response, and oversized response;
- S3 availability, acknowledgement, checksum, and Object Lock failures;
- Step 8 external-volume missing, read-only/write, rename, and corruption
  failures;
- stale vector-derived state and conflicting canonical evidence;
- Personal Memory approval, commit, activation, lifecycle, and export
  interruptions;
- Step 33 audit append and chain-head contention;
- Step 34 typed review handoff interruption; and
- missing dedicated Step 36 credentials.

Step 37 does not add a production chaos service, environment-controlled fault
switch, generic callback executor, network proxy, source publication path,
external-action authority, or recovery administrator. It performs no
production mutation and no AWS or S3 mutation. Step 38 German Law full
end-to-end integration is not started.

## Reused contracts

Step 37 changes no business authority boundary. It exercises and records the
following existing contracts.

| Upstream step | Contract reused for recovery |
| --- | --- |
| Step 6 | `SerializableTransactionRunner`, SQLSTATE `40001` retry, trusted request context, and durable idempotency binding |
| Step 7 | deterministic S3 key, `IfNoneMatch: *`, exact-version checksum/retention verification, and read-only reconciliation |
| Step 8 | fresh volume identity verification, atomic no-overwrite writes, operation-specific fail-closed policy, and no system-drive fallback |
| Step 10 | durable ingestion intent/receipt milestones, retry disposition, reconciliation, and non-destructive orphan handling |
| Steps 19-20 | immutable embedding/model/source lineage and optional, exactly bound vector contribution to an Evidence Bundle |
| Steps 21 and 26 | explicit stale/conflicting evidence state, fail-closed answer output, and typed human-review result |
| Steps 22 and 25 | provider-neutral bounded calls outside persistence transactions and exact immutable Draft identity |
| Step 30 | independently replay-protected approval, technical commit, and activation receipts |
| Step 33 | typed append, owner-partitioned hash chain, stable event identity, exact replay, and read-only verification |
| Step 34 | case-specific human decision and independently idempotent typed business handoff |
| Step 36 | purpose-bound credentials, no admin fallback, least privilege, RLS/FORCE RLS, and secret redaction |

No recovery outcome upgrades model, storage, cache, audit, reviewer, or HAT
output into approval, commit, publication, canonical-evidence, or execution
authority.

## Closed deterministic injection boundary

`aioa_memory_kernel.reliability` defines the production-safe contracts:

- `FailureDomain` is a closed family of implemented reliability domains;
- `FailurePoint` is a closed, versioned registry of exact boundaries;
- `FailureDirective` identifies sorted unique occurrences, bounded to ten;
- `InjectedFailure` exposes only a sanitized code, occurrence, and explicit
  `completion_unknown` flag;
- `RecoveryStatus` distinguishes bounded retry, exact replay, resume, rebuild,
  compensation, fail-closed, human review, and manual operator recovery; and
- `FailureRecoveryCaseResult` is immutable and hash-bound to its case,
  subject, attempt count, final semantic state, reason codes, and duplicate,
  authority, and integrity violation counts.

Production runtime exposes only `NoOpFailureInjector`. It validates the typed
point and subject hash and never fails. The occurrence-aware
`ScriptedFailureInjector` lives under `tests/failure_injection`; production
services neither import nor construct it. Tests attach scripted protocol
adapters only at existing ports such as database connection factories,
provider adapters, S3 clients, mount probes, and durable repository methods.

There is deliberately no free-form failure-point name, callable embedded in a
directive, environment variable that enables faults, or production endpoint
that can trigger a fault. This keeps failure injection deterministic,
bounded, auditable, and incapable of becoming an execution mechanism.

## Exactly-once discipline

Step 37 uses the term "exactly once" only for a verified semantic result under
one immutable identity. It never claims exactly-once process execution or a
distributed exactly-once protocol.

| Boundary | Attempt semantics | Safe semantic-result discipline |
| --- | --- | --- |
| CockroachDB serializable callback | Callback may execute more than once after SQLSTATE `40001` | One committed semantic result through transaction rollback plus durable idempotency/CAS |
| CockroachDB commit acknowledgement | Completion may be unknown to the caller | Do not auto-retry as a new operation; replay the same request and idempotency identity, then read back the one result |
| Provider invocation | At least once when a call is attempted; a timeout or lost response may leave completion unknown | At most the existing bounded call count; preserve unknown completion and never claim exactly-once provider execution |
| S3 `PutObject` | Completion may be unknown after a transport failure | Reconcile the deterministic key and exact version before any further write; a mismatch is a conflict, not a replay success |
| External-volume write | One atomic no-overwrite attempt per explicit call | Freshly verify the exact volume; preserve incomplete artifacts and require conservative operator recovery where needed |
| Personal Memory phases | A client may replay after losing a response | The same approval, commit, or activation identity returns the same receipt; changed semantics conflict |
| Audit append | A caller may replay after losing a response | Stable draft plus one audit idempotency key returns the same event and preserves one chain sequence |
| Review handoff | A caller may replay after losing a response | The exact decision/handoff identity resolves once; changed subject or decision conflicts |

All replay paths retain the original tenant, owner, slot, route, subject hash,
expected state/version, receipt lineage, credential purpose, and policy digest.
Recovery never generates a fresh key merely to escape a conflict.

## CockroachDB and Step 6 recovery

`SerializableTransactionRunner` opens a fresh connection for every attempt,
starts `SERIALIZABLE`, restores the trusted Step 5 tenant/user context, runs
the complete callback, invalidates the transaction handle, and commits. Only
SQLSTATE `40001` after successful rollback/close/context cleanup is retryable.
The existing policy is bounded to ten attempts and a maximum one-second
backoff.

Failures before `BEGIN`, database-unavailable errors without `40001`,
authorization failures, contract failures, and cleanup failures do not enter
the automatic callback retry loop. They return a sanitized typed failure.
`TRANSACTION_CLEANUP_FAILED` is terminal because the next attempt cannot be
proven isolated.

A serialization failure at commit rolls back the whole attempt. The next
attempt recomputes the callback under the same request and idempotency
identity. It does not resume from an in-memory midpoint. This yields one
durable semantic effect even though insert work may have been attempted more
than once.

If commit succeeds but connection release or acknowledgement fails, the
runner reports `CONNECTION_RELEASE_FAILED`. The database effect may already
be durable, so this condition is not relabelled as `40001` and is not blindly
retried. Recovery reissues the exact request and idempotency identity. The
repository must return the existing result or reject changed semantics.

## No external effects inside a retried callback

Provider and S3 adapters call `assert_no_open_persistence_transaction()`
before each external attempt. Step 8 filesystem writes and every other
external effect must also remain outside a Step 6 callback. A retried callback
may contain only short database reads, writes, CAS transitions, immutable
receipt construction, and deterministic hashing.

The required ordering for a cross-system operation is:

1. persist an exact intent or prerequisite in a short transaction;
2. close the transaction;
3. perform the bounded external operation;
4. reconcile exact external identity and bytes;
5. persist the exact receipt in another short transaction; and
6. on restart, read durable state and resume from the first missing receipt.

This is a resumable saga, not cross-system ACID.

## Process interruption and ingestion resume

A process may stop before a durable write, after a durable write, or after a
Step 10 saga checkpoint. Recovery starts from the stored request digest,
scope digest, idempotency identity, milestone, external intent, and exact
receipt. In-memory progress is never authoritative.

The deterministic process-kill fixture terminates only an owned disposable
child after an atomic durable write. Restart with the same identity must read
the existing result and produce no second semantic effect. Production process
kills and restart campaigns are outside the controlled repository run.

The Step 10 saga resumes from its durable milestone and separate execution
disposition. An S3 outage after upload intent enters bounded retry or
`RETRY_WAIT`; an exact object found during reconciliation attaches its
receipt; a mismatch quarantines or requires operator review. Recovery neither
skips a milestone nor uses storage success as source-publication authority.

## S3 failure and reconciliation

Step 7 writes one deterministic object key with `IfNoneMatch: *`, SHA-256,
versioning, encryption, and Object Lock constraints. Step 37 observes these
cases through an injected fake `S3ClientProtocol` only:

- a prewrite service failure fails closed and reports no storage evidence;
- a timeout or lost acknowledgement after a possible write is completion
  unknown;
- `reconcile_snapshot` performs read-only `HeadObject` plus exact-version
  `GetObject`, verifies bytes, metadata, checksum, encryption, and retention,
  and returns the existing evidence only when all facts match;
- reconciliation that finds no object returns absence, not success;
- an existing mismatched deterministic key is a conflict;
- checksum, version, or Object Lock mismatch never reports immutable success;
  and
- a streaming body is closed even when header or body integrity validation
  fails.

After ambiguous completion, the recovery campaign does not issue a second
`PutObject`. The Step 10 saga reconciles first and only a separately
authorized bounded policy may decide whether a confirmed absence permits a
later write attempt. Step 37 performs no live AWS request, bucket mutation,
S3 write, delete, retention change, or credential lookup.

## Step 8 external-volume recovery

The external volume is derived or staging storage, never the sole canonical
authority. Every new explicit attempt begins with a fresh identity, marker,
mount-option, read-only, capacity, containment, and access check. There is no
internal-disk or system-drive fallback.

The production recovery posture is conservative:

- a missing, unmounted, identity-mismatched, or read-only volume stops the
  required operation, or disables an optional cache according to the existing
  operation-specific Step 8 policy;
- an operator restores the exact approved device and mount policy before a
  new explicit attempt; production code does not spin an automatic remount
  loop;
- a target-bound `.aioa-step8-atomic-` artifact is preserved for narrow
  read-only inspection and requires manual operator recovery;
- rename/write ambiguity does not authorize overwrite, deletion, directory
  creation, or a new target path; and
- corrupt derived cache/index bytes are suppressed and may be rebuilt only
  from verified canonical inputs after the exact target is made safe.

A deterministic test that presents "missing, then verified" represents two
operator-bounded observations. It must not be interpreted as a production
automatic retry or permission to write elsewhere.

## Provider failure semantics

Step 22 Draft V1 and Step 25 Draft V2 provider calls occur outside database
transactions. The existing attempt policy permits no more than two calls.
Only transient network, timeout, capacity, or server failures may receive the
one bounded retry. Authentication, invalid-request, policy, identity,
tooling, malformed-response, and oversized-response failures stop after one
call.

Provider execution is at-least-once when a request is sent, not exactly once.
A timeout or lost response may mean the provider completed generation even
though the Kernel received no usable response. `unknown_completion` is
sticky across the bounded attempt sequence: a later known transient failure
must not erase an earlier unknown completion. Retry exhaustion preserves that
fact in the typed error.

No provider response becomes durable Draft state unless the exact response is
received, validated, hash-bound, and persisted. In the Step 26 HAT-enforced
path, provider exhaustion produces a bounded human-review result and never a
known-bad Draft V1 fallback. Repeating a provider request can consume provider
capacity more than once; Step 37 reports that limitation honestly and grants
the model no recovery, approval, commit, or execution authority.

## Stale vector-derived state

Step 19 embeddings and vector results are derived, model-versioned artifacts.
Step 20 verifies the exact query, source/chunk lineage, model digest, vector
result hash, and candidate hashes before fusion. A stale or mismatched vector
result is rejected; it is never silently treated as current evidence.

Recovery has two explicit forms:

1. if the caller constructs an existing valid lexical-only Step 20 request
   with both vector identities absent, the bounded lexical branch may proceed
   without claiming vector coverage; or
2. rebuild the derived embedding/index outside the user request transaction
   from verified canonical chunks and the pinned model, then create a new
   exact vector result and downstream bundle.

A request that already binds vector request/result hashes cannot silently
drop or replace them. Cache corruption, an unavailable index, or a model
digest mismatch grants no source authority and does not mutate canonical
source bytes.

## Conflicting or stale canonical evidence

Conflicting or stale evidence is a semantic policy result, not a transient
infrastructure exception. Step 21 preserves conflict groups and explicit
freshness status. Step 26 applies the existing answer ceiling and returns a
bounded failure or human-review result when policy requires it.

The recovery framework must not retry until one answer happens to look
acceptable, select a preferred source with a model, rewrite the conflict, or
weaken HAT enforcement. Recovery requires a new verified evidence/version
input, an explicit policy-supported temporal context, or the Step 34 human
review path. An unresolved conflict remains unresolved.

## Failed or interrupted owner approval

No approval, denial, stale request, owner mismatch, tenant mismatch, slot
mismatch, hash mismatch, or missing dedicated credential may create a commit
or activation. A failed pre-approval attempt leaves the proposal in
`AWAITING_APPROVAL` and performs no automatic approval.

If the exact owner approval transaction committed but its acknowledgement was
lost, replaying the same approval request and nonce returns the existing
receipt. Changed proposal content or changed semantics under that identity is
a conflict. If current state/version changed before approval, the UI or caller
must refresh and obtain a new deliberate owner action; it cannot replay stale
consent against new content.

The same rule applies independently to Step 30 technical commit and
activation. A response loss may leave `APPROVED`, `COMMITTED`, or `ACTIVE`
already durable. Exact replay returns the existing receipt and produces no
second patch, quota increment, transition event, or content mutation.

## Audit and review recovery

Step 33 audit append holds chain-head lock, event insert, head CAS, and
idempotency completion in one short serializable transaction. Chain-head
contention follows Step 6 `40001` retry. A lost append acknowledgement is
resolved with the same typed draft and idempotency key; exactly one event and
sequence may exist.

Receipt adapters default `recorded_at` to the immutable business occurrence
time. Reconstructing an adapter later therefore yields the same draft hash
instead of turning a recovery replay into a timestamp conflict. When a
business result and audit append are separate durable facts, the business
operation must not report `audited=true` without an append receipt. A
reconciler may reconstruct the typed event from the immutable receipt and
append that exact fact; it cannot fabricate or alter the business outcome.

Step 34 review handoff has an independent replay identity. A response loss
after a successful typed handoff is recovered by the exact same decision,
case, subject hash, expected state/version, and handoff request. Changed
subject or decision conflicts. Invalid audit-chain context continues to fail
closed or escalate under Step 34 policy and is never auto-repaired.

## Credential and authority failures

A missing Commit Helper, provider, reviewer, review-service, audit, or other
dedicated credential fails closed through Step 36 purpose-bound loading. A
recovery path never tries the application, migrator, admin, provider, or
another service credential as a broader fallback.

All retries and replays preserve the authenticated tenant/owner context and
the original credential purpose. Cross-user or cross-tenant requests remain
denied under service checks and RLS/FORCE RLS. A recovery result records zero
authority violations only when no actor gained a new capability.

## Controlled-validation safety boundary

The planned Step 37 campaign is offline and deterministic by default. It may
use protocol fakes, temporary files, an owned disposable child process, and
an owned disposable loopback CockroachDB runtime. It must not:

- connect to or stop a production database;
- run a production process-kill, restart, or network-partition campaign;
- call a paid or live model provider;
- mutate AWS, S3, Object Lock, an external production volume, or secrets;
- delete or overwrite external evidence;
- widen a database role or bypass RLS;
- publish a source, approve on behalf of a user, or execute an external
  action; or
- claim a PASS before the focused tests, full regression, contract validator,
  and controlled runner actually complete.

Sanitized validation evidence may record only closed case identities, hashes,
attempt counts, final semantic states, and zero/nonzero violation counters.
It contains no raw secret, private memory text, provider response, AWS
identity, local path, or production identifier.

## Known limitations

- SQL transactions and durable idempotency provide one semantic database
  result, not exactly-once callback execution.
- Provider completion after timeout can remain unknown; no provider-side
  idempotency or exactly-once claim is made.
- CockroachDB plus S3 remains a reconciled saga, not distributed ACID.
- Step 8 target-bound incomplete artifacts intentionally require manual
  operator recovery; Step 37 adds no automatic cleanup.
- SHA-256 audit chains are local integrity proofs, not external notarization.
- Vector rebuild is derived-data recovery and does not prove semantic source
  freshness.
- The architecture record does not itself prove that any campaign passed.

## Step 38 boundary

Step 37 does not run the German Law HAT full end-to-end flow, select a legal
question, exercise a live model or corpus, or create a release candidate.
Those activities belong to Step 38 or later. `Step 38: NOT STARTED`.
