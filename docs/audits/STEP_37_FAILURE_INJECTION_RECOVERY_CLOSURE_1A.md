# Step 37 Failure Injection and Recovery Closure 1A

## Record status

This record captures the completed Step 37 implementation surface and the
observed pre-commit validation gates. It deliberately does not invent a Git
closure SHA or assert `COMPLETE AND PUSHED` before the approved commit and push
exist and are verified reachable from `origin/main`.

- Exact Step 36 base: `f7882eed42534fb5c07bf55694886a2fca11823e`.
- Step 36 was complete and pushed at task start.
- Step 37 implementation is complete in the closure worktree.
- Final 55-case controlled-validation result: `PASS`.
- Step 38 is `NOT STARTED`.
- Final Step 37 Git closure SHA: `NOT CREATED`.
- Push result: `NOT PERFORMED`.

No final commit SHA is invented in this record.

## Scope

Step 37 owns deterministic test-only failure injection, a complete recovery
matrix, bounded recovery campaigns and proof that existing state, integrity,
scope and authority contracts survive faults. It does not create a production
chaos API, provider outage, AWS/S3 mutation, production database outage,
production volume-corruption campaign, external action or German Law full
end-to-end flow.

The canonical policy artifact is
`docs/reliability/STEP37_FAILURE_RECOVERY_MATRIX_1A.md`. It includes explicit
recovery for CockroachDB unavailability and serialization, commit
acknowledgement loss, process restart, Step 10 saga interruption, model
provider failure, S3 outage and ambiguous write, external-volume
missing/read-only/corrupt states, stale vector index, conflicting evidence,
failed approval, Personal Memory lifecycle, audit, review handoff and missing
dedicated credentials. No recovery cell is implicit.

## Implementation inventory

The Step 37 changeset defines a closed, versioned failure contract:

- schema `1.0.0`;
- registry `step37-closed-failure-point-registry-1a`;
- recovery policy `step37-recovery-policy-1a`;
- typed `FailureDomain`, `FailurePoint`, `RecoveryStatus`,
  `FailureDirective` and `FailureRecoveryCaseResult`; and
- an inert production-safe injector boundary with scripted behavior confined
  to tests.

Each case result binds its domain, point, subject hash, bounded attempt count,
final semantic state, recovery status, sorted reason codes, duplicate count,
authority-violation count, integrity-violation count and deterministic result
hash. Production services must not import the scripted test injector.

The focused campaign coverage includes:

- exact-occurrence deterministic injection;
- Step 6 `40001` full callback retry;
- post-commit acknowledgement loss followed by exact idempotent replay;
- bounded subprocess restart after a durable write;
- Step 22/25 provider transient and terminal failures;
- Step 26 fail-closed verified output with no known-bad Draft V1 fallback;
- Step 7 S3 outage, acknowledgement ambiguity, checksum/Object Lock and body
  cleanup behavior through strict fakes;
- Step 8 missing-volume/no-fallback and exact derived rebuild behavior through
  temporary fixtures;
- Step 10 snapshot/publication reconciliation and `RETRY_WAIT`;
- Step 30 approval, commit and activation acknowledgement-loss replay;
- Step 33 audit append replay and hash-chain verification;
- Step 34 typed handoff replay; and
- Step 36 missing-credential, wrong-purpose, owner/tenant and authority
  negatives.

## Transaction and idempotency decision

Step 6 remains the only automatic database transaction retry authority. It
retries the complete callback only for exact SQLSTATE `40001`, in a new
serializable transaction with trusted context re-established, for at most ten
attempts. Rollback/release failure, authentication/connection failure and
other SQLSTATE values do not become broad retries.

The key recovery distinction is:

- transaction attempts may run more than once;
- one exact immutable/idempotent database operation may create one semantic
  durable effect; and
- an acknowledgement lost after commit is an unknown result that requires
  exact replay, not a claim that the first attempt failed.

Step 30 approval, technical commit and activation remain three separate
durable edges. A crash after approval leaves `APPROVED`; a crash after commit
leaves `COMMITTED`; neither edge is skipped. Exact replay reuses its original
receipt, event and content identity. Quota and lifecycle state changes remain
inside the same transaction as the phase they describe.

The Step 6, Step 30 and Step 33 transaction callbacks inspected for Step 37
contain repository calls and deterministic contract/policy work only. Model,
provider, AWS/S3, filesystem and subprocess effects are outside those retried
callbacks. The existing external-call guard remains defense in depth.

## Provider completion semantics

Step 37 explicitly rejects an exactly-once provider claim. Provider invocation
is at least once within the existing bounded attempt policy. A timeout or lost
response can mean the provider completed while the Kernel received no usable
response; a later retry may run inference again and incur another provider
cost.

The current service change preserves `unknown_completion=true` across the
whole bounded attempt sequence if any earlier attempt was ambiguous. Only one
valid immutable local draft identity may be persisted after a successful
response. On exhaustion, no text is invented or persisted and Step 26 retains
its human-review/bounded-failure behavior.

No real provider call or credential is permitted in Step 37 validation.

## S3, ingestion and external-volume recovery

S3 and CockroachDB do not form a distributed ACID transaction. An ambiguous
S3 write is reconciled through the deterministic key and exact version,
content hash, length, metadata, encryption and Object Lock evidence before any
later write. A second blind put, ETag-as-hash assumption, latest-version
fallback, cleanup delete or governance bypass is forbidden.

Step 10 resumes from the last durable milestone/intent/receipt. Existing exact
external evidence is attached once; mismatch enters quarantine or operator
review. Publication remains Step 9 authority and cannot be inferred from
storage success.

Step 8 recovery is deliberately conservative and manual. A missing/read-only
or identity-mismatched volume fails a required operation or disables an
optional cache without a system-drive path. The operator must restore and
freshly verify the exact device, marker, filesystem, access and capacity,
inspect any target-bound incomplete artifact, and then explicitly replay or
rebuild from canonical inputs. Production does not run an autonomous volume
retry or automatic incomplete-file cleanup.

Step 37 uses fake S3 clients and temporary external-volume fixtures only. It
performs no AWS mutation, S3 mutation or write to the real operator volume.

## Retrieval and evidence recovery

A stale/unavailable Step 19 vector index is derived-data failure, not evidence
authority. Its candidates are suppressed. Existing exact/full-text modalities
may continue only when the route and evidence policy truthfully accept a
`PARTIAL` result; otherwise the run fails closed. Rebuild uses exact published
chunks, pinned embedding model identity and immutable lineage outside a
retried database callback.

Step 21 canonical conflict is preserved rather than retried away. Private
Personal Memory cannot override it, and a model/reviewer note cannot transform
unresolved conflict into canonical certainty. Step 26 continues to block the
known-bad Draft V1 fallback under `HAT_ENFORCE`.

## Audit and review recovery

Step 33 event insert and chain-head compare-and-set remain one short
transaction. Exact `40001` contention retries the full append. Post-commit
acknowledgement loss replays the same event identity and verifies one sequence,
one event hash and a consistent head.

Receipt adapters use the immutable business occurrence timestamp when no
explicit recording timestamp is supplied. This makes deterministic
reconstruction after a process gap produce the same draft hash and
idempotency binding. A changed timestamp under the same key is not silently
accepted as replay.

Step 34 handoff recovery revalidates case, decision, subject, reviewer scope
and audit context. A lost acknowledgement returns the existing exact handoff
and terminal case state; it cannot create a second incompatible decision,
publish a source or execute an external action.

## Authority and isolation proof requirements

Recovery must preserve all Step 36 boundaries:

- no missing dedicated credential falls back to application, migrator/admin,
  reviewer, provider, audit reader or a generic database URL;
- normal application, reviewer, provider, audit reader and migrator purposes
  cannot be reinterpreted as Commit Helper authority;
- a provider credential has only provider-call capability;
- tenant, owner, slot, route, HAT scope, source, state version and immutable
  hashes are revalidated after restart; and
- receipt/event hashes are integrity references, not bearer credentials.

Every final case must report zero duplicate semantic effects, zero authority
violations and zero integrity violations. A recovery that widens scope or
privilege is a failed campaign even when it reaches a nominal business state.

## Validation status

The following gates were observed against the Step 37 closure worktree:

| Gate | Observed result |
| --- | --- |
| Step 37 focused offline suites | `44/44 PASS`; failures `0`; errors `0` |
| Compileall | `PASS` |
| Pinned frontend asset-integrity check | `PASS` |
| Contract validator | `PASS` |
| Full repository regression | `1959/1959 PASS`; failures `0`; errors `0` |
| Disposable CockroachDB v26.2.4 phase | `PASS`; 18 migrations applied, 18 replay-skipped |
| Transaction fault proof | injected `40001` observed; changed replay rejected with `23505`; durable row count `1` |
| Disposable-runtime cleanup | `PASS`; clean shutdown, no force kill, no CockroachDB panic |
| Final 55-case controlled validator | `PASS` |
| Sanitized evidence digest | `59983b1b399118897440519d98a7ff27c052c85cb5f2007414ada16d2aa97fcc` |
| Changeset/staging gate | performed only after final evidence is verified |
| Post-commit validation | required after the closure commit exists |
| Push and `main...origin/main=0 0` | required after post-commit validation passes |

The live database phase used one owned loopback disposable runtime and
synthetic identities only. It made no production database, provider, AWS/S3,
external-volume or external-action mutation. The sanitized evidence path is
`docs/evidence/reliability/step37-failure-recovery-validation.json`.

## Known limitations and retained boundaries

- Scripted failure injection is a test facility, not production runtime
  observability or a chaos control plane.
- Provider calls can be duplicated after unknown completion; the system
  guarantees bounded attempts and one accepted local result, not exactly-once
  remote execution.
- Cross-system S3/database completion is reconciled, not transactionally
  atomic.
- Step 8 incomplete artifacts and mount restoration require manual operator
  judgment; Step 37 does not add delete/repair authority.
- Derived vector rebuild is not canonical evidence and is not run inside a
  database transaction.
- A missing audit event after an already durable legacy business receipt
  requires deterministic reconciliation; the ledger does not create the
  business fact.
- In-memory focused fixtures do not replace any required disposable
  CockroachDB proof of transaction, RLS or role behavior.
- No production database, provider, AWS/S3, external-volume or external-action
  failure was injected.
- Step 38 German Law full end-to-end integration is not implemented.

## Git closure boundary

The implementation and observed worktree validation do not themselves create
a pushed checkpoint. After the final controlled result and evidence digest
above are verified, the closer must update the Step 37 roadmap/AGENTS
checkpoint, stage only Step 37 files, pass cached diff checks and create the
single approved closure commit:

```text
test(reliability): add failure injection recovery validation 1a
```

The required focused suites, contract validator and controlled validator must
then pass again against the clean committed tree. Only after a non-force push,
fetch and exact `main...origin/main=0 0` verification may the final report say
`COMPLETE AND PUSHED`. No final commit SHA is recorded in advance, and Step 38
remains `NOT STARTED`.
