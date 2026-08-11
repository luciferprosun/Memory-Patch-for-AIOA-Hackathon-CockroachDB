# ADR-044: Use closed test-only failure injection and exact recovery identities

## Status

Proposed. It becomes accepted only when the Step 37 closure commit is
reachable on `origin/main`. This ADR is based on
`f7882eed42534fb5c07bf55694886a2fca11823e`; the closure record owns validation
and push evidence.

## Context

Steps 6-36 established serializable CockroachDB transactions, durable
idempotency, an S3/CockroachDB ingestion saga, exact external-volume and S3
identity, provider-neutral model calls, evidence conflict handling, Personal
Memory approval/commit/activation, an append-only audit chain, typed human
review, and purpose-separated credentials.

These boundaries need deterministic failure proof. A useful campaign must
show that interruption does not duplicate semantic effects, widen authority,
silently use stale evidence, or invent success. At the same time, a fault
facility must not become a production chaos endpoint or arbitrary execution
hook.

Not every boundary has the same completion semantics. CockroachDB can provide
one committed semantic effect under transaction plus idempotency. Provider and
S3 network calls may complete even when the caller loses the response. The
external volume deliberately requires conservative manual recovery for some
ambiguous states. A single claim of "exactly once everywhere" would therefore
be false.

## Decision

1. Define a closed, versioned `FailureDomain`, `FailurePoint`,
   `RecoveryStatus`, and immutable `FailureRecoveryCaseResult`. Bind every
   campaign result to an exact subject hash, bounded attempt count, final
   semantic state, reason codes, and duplicate, authority, and integrity
   violation counters.
2. Keep the production-facing injector inert. `NoOpFailureInjector` may only
   validate a typed point and hash. Put occurrence-aware scripted injection
   under `tests/failure_injection`, and forbid production imports,
   environment-controlled faults, arbitrary callbacks, and dynamic point
   names.
3. Inject failures through existing closed ports: connection factories,
   provider adapters, S3 clients, external-volume probes, repositories, and
   owned disposable child processes. Do not add a generic public chaos API.
4. Preserve Step 6 retry semantics. Automatically retry only SQLSTATE
   `40001`, rerun the complete short callback under the same request context,
   and keep the existing ten-attempt and backoff ceilings. Do not retry when
   transaction cleanup cannot be proven safe.
5. Treat post-commit acknowledgement loss as unknown client completion, not a
   serialization retry. Recover by replaying the exact idempotency identity
   and request hash and reading the existing result. Changed semantics under
   that identity fail closed.
6. Keep provider, S3, filesystem, model, and other external effects outside
   retried database callbacks. Cross-system progress uses durable intent,
   bounded external work, exact reconciliation, and durable receipt.
7. Describe provider execution as at-least-once when an attempt is sent.
   Timeout or response loss may leave completion unknown. Preserve that flag
   across bounded Step 22/25 attempts and never claim provider-side exactly
   once. Nonretryable authentication or response-contract failures stop
   immediately.
8. Reconcile an ambiguous S3 write through the deterministic key and exact
   version, checksum, metadata, encryption, and Object Lock evidence before
   considering any later write. Do not issue a blind second `PutObject` and
   do not treat mismatch as idempotent success.
9. Preserve the Step 8 operation-specific fail-closed policy. Missing,
   read-only, identity-mismatched, or ambiguous external-volume states receive
   no system-drive fallback. An operator must restore and reverify the exact
   volume before a new explicit attempt. Preserve target-bound incomplete
   artifacts for manual inspection; do not auto-delete or overwrite them.
10. Treat stale vector-derived state as derived-data failure. Reject mismatched
    vector lineage. Permit only an already-valid lexical-only request with no
    vector identities, or rebuild derived vectors from verified canonical
    chunks outside the request transaction. Never silently drop a vector hash
    already bound to a request.
11. Treat conflicting or stale canonical evidence as a semantic policy result,
    not a transient error. Preserve the Step 21 status and Step 26
    fail-closed/human-review boundary. Do not retry toward a preferred answer
    or let a model resolve authority.
12. A failed, denied, stale, or mismatched owner approval leaves no commit or
    activation and never auto-approves. Lost acknowledgements for Step 30
    approval, commit, and activation recover only through their independent
    exact replay identities and immutable receipts.
13. Recover Step 33 audit acknowledgement loss with the same stable draft and
    idempotency key. Receipt adapters use the immutable business occurrence
    time when reconstructing `recorded_at`. An audit fact mirrors a business
    fact and cannot manufacture it.
14. Recover Step 34 typed handoff with the exact decision, case, subject,
    state/version, and handoff identity. Changed or stale inputs conflict;
    broken audit context is not auto-repaired.
15. Preserve Step 36 credential purposes, tenant/owner scope, RLS/FORCE RLS,
    and no-admin-fallback behavior during every retry and replay. Recovery
    authority is never broader than the original operation authority.
16. Restrict controlled campaigns to fakes, temporary artifacts, owned
    disposable child processes, and an owned disposable CockroachDB runtime.
    Perform no production process kill, provider call, AWS/S3 mutation,
    production-volume mutation, secret rotation, source publication, approval,
    or external action.
17. Do not start Step 38. Step 37 supplies recovery proof only; it does not run
    the German Law HAT full end-to-end scenario.

## Consequences

Recovery tests are reproducible and can target exact occurrence numbers
without exposing a production fault switch. Every campaign can state whether
it recovered by bounded retry, exact replay, resume, rebuild, compensation,
human review, fail-closed behavior, or manual operator action.

Database work may be attempted more than once while still yielding one
durable semantic result. Post-commit ambiguity requires a read/replay round
trip. Provider calls may execute more than once and may consume provider
capacity even when no response is usable. S3 and CockroachDB remain an
eventually reconciled saga, not a distributed transaction. External-volume
recovery can require operator work rather than automatic availability.

Stable audit adapter timestamps allow an immutable receipt to reconstruct the
same draft after interruption. This closes a replay conflict without changing
the Step 33 hash-chain algorithm or granting audit business authority.

Step 37 validation must report actual outcomes. This ADR alone is not evidence
that focused tests, full regression, a controlled runner, commit, or push
passed.

## Rejected alternatives

### Add a production chaos endpoint or environment toggle

Rejected because it creates a new operational attack surface and an
unbounded way to interrupt authority-bearing paths.

### Retry every database exception

Rejected because only SQLSTATE `40001` has the existing safe full-transaction
retry contract. Authorization, integrity, cleanup, configuration, and unknown
post-commit outcomes require different handling.

### Call providers or S3 inside a serializable callback

Rejected because the callback can rerun. An external side effect could then
duplicate without sharing CockroachDB rollback semantics.

### Claim exactly-once provider execution

Rejected because timeout and response loss can hide a completed provider
request, and the provider boundary does not expose a transaction or
repository-controlled idempotency guarantee.

### Blindly repeat `PutObject` after acknowledgement loss

Rejected because the write may already exist under Object Lock. Exact
read-only reconciliation must precede any later authorized write decision.

### Fall back from a missing external volume to the system disk

Rejected because it breaks Step 8 identity, capacity, containment, and data
placement guarantees. Recovery is a fresh exact-volume verification and may
require manual operator action.

### Silently ignore a stale vector result

Rejected because a Step 20 request that binds vector hashes must either verify
those exact inputs or fail. Optional lexical-only operation requires an
explicitly valid request with no vector identities.

### Auto-resolve evidence conflicts or auto-approve after failure

Rejected because conflict is a semantic status and approval is an explicit
authenticated owner action. Neither can be created by retries, models,
Critics, audit records, or recovery workers.

### Repair a broken audit chain during verification

Rejected because verification is observational. Repair would rewrite the
evidence whose integrity is being assessed.

### Run the Step 38 German Law scenario as recovery validation

Rejected because the repository sequencing rule permits only one roadmap
step per task. `Step 38: NOT STARTED`.
