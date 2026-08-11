# Step 37 Failure and Recovery Matrix 1A

## Status, base, and scope

This matrix is the canonical Step 37 recovery policy prepared from exact base
`f7882eed42534fb5c07bf55694886a2fca11823e`. It covers the failure domains
implemented through Steps 6-36 and the Step 37 closed failure-point registry.
It is the frozen policy input to the controlled validator. Validation evidence,
the closure record, and Git state report execution outcomes separately; this
policy artifact does not invent a closure commit SHA or push result.

Failure injection is deterministic, bounded, and test-only. Production
services do not import the scripted injector. A production-safe no-op boundary
may validate a closed failure-point identity, but it cannot trigger a fault.
The matrix authorizes no production outage, no production credential use, no
AWS or S3 mutation, no external action, and no Step 38 work.

## Exactly-once vocabulary

`Exactly once` is not used as a blanket distributed-systems claim. The precise
discipline is:

- **Transaction attempt:** may execute more than once after an exact CockroachDB
  `40001`. The entire callback is retried in a new serializable transaction.
- **Database semantic effect:** converges to one durable result when the exact
  tenant/owner/operation/idempotency identity and immutable request binding are
  replayed. Compare-and-set state versions, immutable receipts, uniqueness and
  idempotency rows provide this property.
- **Commit acknowledgement loss:** completion is unknown to the caller. It is
  not automatically retried as `40001`; recovery replays the same semantic
  request and reads the already durable result or commits it once.
- **Provider invocation:** is **at least once within the bounded attempt
  policy**, not exactly once. A timeout or lost response can leave provider
  completion unknown. A retry may execute inference again and may incur
  duplicate provider cost, even though only one verified immutable local result
  may later be persisted.
- **S3 operation:** has no cross-system exactly-once guarantee. A deterministic
  key, conditional no-overwrite write, exact version, checksum and Object Lock
  make a verified object reconcilable. After an ambiguous write, reconcile the
  exact object before any later write attempt.
- **External-volume write:** uses atomic no-overwrite local semantics. It is
  derived storage, not a distributed exactly-once effect. Incomplete staging
  artifacts stop the operation for manual inspection and are never
  automatically removed.
- **Read-only/rebuild work:** may run more than once. It is acceptable only when
  each run is bounded and its accepted output is bound to exact immutable input
  hashes.

Every recovered case must report `duplicate_side_effect_count=0`,
`authority_violation_count=0`, and `integrity_violation_count=0`. Multiple
attempts are not themselves duplicate semantic side effects.

## Canonical recovery matrix

There are no implicit or blank recovery paths. `None` below means that an
automatic retry is intentionally forbidden; the recovery column still states
the exact next action.

| Domain and closed point(s) | Fault and detection | Immediate safe state | Automatic retry | Exactly-once / replay discipline | Required recovery or resume | Required proof |
| --- | --- | --- | --- | --- | --- | --- |
| CockroachDB availability: `DB_BEFORE_BEGIN`, `DB_READ_FAILURE` | Connection acquisition, trusted-context setup, or read fails with a sanitized non-`40001` error. | No success is reported; no callback result is accepted. | None. Connection/authentication failures are not promoted to serialization retries. | No database effect is claimed. | Restore the exact least-privileged database boundary, then explicitly replay the same request and idempotency identity. | No broader credential fallback; zero partial rows; exact replay returns or creates one result. |
| CockroachDB serialization: `DB_COMMIT_SERIALIZATION_FAILURE` | Structured SQLSTATE is exactly `40001` in the callback or at commit. | The attempt is rolled back, its transaction handle is invalidated, context is cleared and the connection is released. | Full callback, at most the Step 6 limit of ten attempts with bounded backoff. | Attempts may repeat; the committed semantic result exists once. | Let the Step 6 runner rerun the complete callback under the same immutable request. | First attempt leaves no partial row; later attempt commits one result; no external call occurs in either callback. |
| Transaction interruption before commit: `DB_BEFORE_COMMIT` | A non-serialization injected interruption occurs after work but before commit. | Rollback leaves the pre-attempt durable state. | None for a non-`40001` signal. | No committed effect from the interrupted attempt. | Reissue the exact request after the failure source is removed. | State, receipt, event and quota counts remain unchanged before replay; replay commits once. |
| Commit acknowledgement loss: `DB_AFTER_COMMIT_ACK_LOST` | Commit may have succeeded but release/acknowledgement fails, such as `CONNECTION_RELEASE_FAILED`. | Completion is explicitly unknown; the caller must not infer failure or success. | None as an ordinary transaction retry. | Exact request replay discovers one durable result; a changed request conflicts. | Reconnect and replay the same tenant/owner/idempotency/request hashes. | One durable effect, one result hash, one receipt, and zero second semantic insert. |
| Process restart after durable write: `PROCESS_AFTER_DURABLE_WRITE` | The process exits after a durable result but before returning it. | Durable database state remains authoritative; volatile response state is lost. | None inside the terminated process. | Restart replay uses the same operation identity and reads the existing result. | Restart the bounded worker/service and submit the exact request identity. | Two process attempts converge to one durable effect and the same result hash. |
| Process restart after saga checkpoint: `PROCESS_AFTER_SAGA_CHECKPOINT` | The worker exits after one durable Step 10 milestone/intent/receipt. | The last committed saga milestone and disposition remain authoritative. | Worker restart is a new resume, not a hidden transaction retry. | Durable intent/receipt identity prevents repeating an already verified effect. | Load the saga, reconcile the exact external effect, then continue from the first incomplete edge. | No milestone regression, no duplicate S3 publication, contiguous event history. |
| Ingestion state interruption: `SAGA_AFTER_STATE_WRITE` | Failure follows a committed saga state or intent. | The committed milestone/intent is retained; later edges are absent. | None until state is reread. | State transition exists once. | Resume from durable state; do not recreate or skip the intent. | One state edge and one event; downstream effect count remains zero until resumed. |
| Ingestion object interruption: `SAGA_AFTER_OBJECT_WRITE` | S3 write may exist while its database receipt is absent. | Completion is unknown across systems; no later milestone is reported. | No blind second write. | Cross-system exactly-once is not claimed; deterministic object identity makes reconciliation possible. | Head/read the exact deterministic object/version, verify bytes, checksum, metadata and lock, then attach one receipt or enter review/quarantine. | One verified snapshot identity, one attached receipt, no duplicate publication and no cleanup/delete. |
| Ingestion finalization interruption: `SAGA_BEFORE_FINALIZE` | Exact validation/publication work exists but saga finalization is interrupted. | Last committed prerequisite remains; `PUBLISHED` is not fabricated. | None before exact reconciliation. | Exact Step 9 event and saga receipt are replay identities. | Re-read Step 9 publication state and exact event; attach/reuse it, then finalize once. | One publication event and one terminal saga transition. |
| Provider transient failure: `PROVIDER_TIMEOUT`, `PROVIDER_TRANSIENT_FAILURE`, `PROVIDER_RESPONSE_LOST` | Timeout, transient transport/server failure, or lost response; `unknown_completion` is retained across attempts. | No provider text is accepted without a valid response contract. | Service-specific bounded policy only: Steps 22/25 permit at most two attempts; Step 26 does not broaden its finalization retry. | Provider execution is at least once and may complete more than once. Local persistence accepts at most one verified immutable draft identity. | Retry only within the existing attempt ceiling, outside every DB transaction. On exhaustion return typed unavailable/review output and preserve `unknown_completion=true` if any attempt was ambiguous. | Exact call count bound; no third call; no Draft V1 fallback under `HAT_ENFORCE`; no database/provider credential crossover. |
| Provider terminal failure: `PROVIDER_AUTH_FAILURE`, `PROVIDER_INVALID_RESPONSE`, `PROVIDER_OVERSIZED_RESPONSE` | Authentication, policy/contract, invalid response, or size validation fails. | Response is rejected and no draft/result is persisted. | None. | No provider success or local semantic effect is claimed. | Correct configuration or request through the owning operational boundary, then issue a new authorized run; do not retry with another secret or model silently. | One provider attempt; sanitized error; zero secret leakage and zero authority widening. |
| S3 unavailable before verified write: `S3_PUT_FAILURE` | Step 7 write returns typed service unavailability. | Snapshot success and immutable evidence are not reported. | The S3 adapter does not blind-retry; Step 10 may perform only its bounded saga policy and then enter `RETRY_WAIT`. | No cross-system exactly-once claim. | Reconcile the deterministic key first; if absent, a later bounded saga resume may write under conditional no-overwrite semantics. | No false version/lock evidence; bounded attempts; no production or validation AWS mutation in Step 37. |
| S3 acknowledgement loss: `S3_ACK_LOST` | Write request may have succeeded but the response was lost. | Object completion is unknown; database receipt stays absent. | No second `PutObject` before reconciliation. | Deterministic key plus exact version/checksum/metadata reconciliation; external invocation itself is not exactly once. | Head and retrieve the exact object, verify bytes and Object Lock, then attach one storage receipt; write again only when exact absence is established and policy permits. | One `PutObject` in the ambiguous fixture, one verified version, one receipt, zero delete. |
| S3 read outage: `S3_READ_FAILURE` | Exact-version head/get cannot complete. | Bytes and lock status are unverified; downstream publication/verification stops. | Bounded orchestration retry only where the existing saga policy says so. | Read attempts may repeat; no state is advanced without exact evidence. | Restore read access, then reread the same version and verify all immutable fields. | No fallback to ETag/latest version/local cache as authority; state remains pre-verification. |
| S3 integrity mismatch: `S3_CHECKSUM_MISMATCH` | Exact bytes, content length, SHA-256, metadata, version, encryption, or retention differs. | Fail closed; no success receipt. | None. | A conflicting object is never treated as replay. | Preserve evidence and route the saga to quarantine/operator review. | Mismatch detected, streaming body closed, zero publication and zero delete. |
| S3 capability failure: `S3_OBJECT_LOCK_REJECTION` | Versioning/Object Lock/retention preflight or write evidence is invalid. | No immutable-storage claim and no publication. | None until an authorized operator fixes capability. | No effect is accepted as canonical immutable snapshot. | Restore the exact approved bucket capability outside Step 37, then rerun preflight and the authorized workflow. | Zero `PutObject` after failed preflight; no governance bypass; Step 37 performs no AWS mutation. |
| External volume missing or read-only: `VOLUME_MISSING` | Fresh Step 8 identity/options/access/capacity check fails. | Required operation fails closed; optional cache operation is disabled; no system-drive fallback path is returned. | **No autonomous production retry.** | No durable external-volume effect is claimed. | Conservative manual operator recovery: restore and reverify the exact volume, marker, filesystem, access and capacity; then explicitly replay or rebuild from canonical immutable inputs. | No fallback, no directory creation, no silent alternate device, exact rebuilt bytes/hash. |
| External-volume write failure: `VOLUME_WRITE_FAILURE` | An owned temporary write fails before the no-overwrite publication link. | No target is published; the adapter closes the descriptor and removes only its unpublished temporary artifact. | None automatically. | No durable semantic effect is claimed. | Restore and reverify the exact volume, then explicitly rebuild the derived artifact from immutable inputs. | No target, no leftover owned pre-publication staging artifact, no system-drive fallback. |
| External-volume atomic interruption: `VOLUME_RENAME_FAILURE` | Atomic link/rename or finalization fails and a target-bound staging artifact may remain. | Final success is not reported; artifact is preserved. | None. | No overwrite and no automatic cleanup. | Operator inspects the exact `.aioa-step8-atomic-*` artifact and target; only after safe resolution may the exact write be explicitly retried. | Existing target unchanged; incomplete artifact reported; no delete/overwrite API invoked. |
| Derived cache corruption: `VOLUME_CACHE_CORRUPTION` | Exact length/SHA-256 or safe-file checks fail for embedding/index/package cache. | Corrupt cache is never consumed as evidence. | None against the corrupt bytes. | Rebuild may run more than once; accepted bytes are exact-input/hash bound. | Disable the cache, manually isolate the bad artifact, then deterministically rebuild from verified canonical source/model inputs after the Step 8 volume gate passes. | No source-authority upgrade, no system fallback and rebuilt hash/length verified. |
| Vector index stale or unavailable: computed Step 19/20 failure state | Stored vector/model/lineage digest is stale, query fails, or expected index output cannot be verified. | Vector candidates are suppressed; they cannot be silently accepted or recast as current. | No blind query/result reuse. | Vector/index work is derived and rebuildable; no exactly-once claim. | Use only exact/full-text modalities when the existing route/evidence policy explicitly permits a truthful `PARTIAL` result; otherwise fail closed. Rebuild the derived index from exact published chunks and pinned model identity outside a DB retry callback. | Stale vector contributes zero candidates; hard scope remains; result is `PARTIAL` or unavailable, never falsely `COMPLETE`. |
| Conflicting canonical evidence: computed Step 21 state | Current verified sources disagree under the same scope/time policy. | Preserve `CONFLICTING`; answer certainty and Personal Memory override are blocked. | None merely because evidence conflicts. | Conflict is a semantic state, not a transient duplicate-work problem. | Return a qualified failure/human-review path or await new canonical evidence through existing ingestion/review authority. Do not mutate evidence, auto-resolve, or let private memory win. | Conflict group and hashes preserved; no known-bad Draft V1 fallback; zero publication/approval/execution authority. |
| Audit failure before append: `AUDIT_BEFORE_APPEND` | Mandatory typed append fails before an event is inserted. | No `audited=true` or resolved business result may be claimed when policy requires audit. | Exact `40001` only through Step 6; otherwise none. | If business and audit share one transaction, both roll back. If a prior immutable business receipt already exists, deterministic reconciliation must reuse it. | Replay the exact typed event built from the immutable receipt and stable business timestamp; do not rebuild with a new semantic timestamp. | Zero orphan partial chain row; exact event replay or explicit unresolved audit gap. |
| Audit acknowledgement loss: `AUDIT_AFTER_APPEND_ACK_LOST` | Event and chain-head update may be durable while response is lost. | Completion unknown to caller; chain remains the source of truth. | No blind new event. | Stable event idempotency/draft hash returns the existing envelope; one chain sequence only. | Reconnect and append the exact reconstructed draft; verify full chain and head. | One event, one sequence, same event hash, verified chain. |
| Audit chain-head contention: `AUDIT_CHAIN_HEAD_CONTENTION` | Serializable chain-head compare-and-set produces exact `40001`. | Attempt rolls back event and head together. | Full transaction retry within Step 6 bound. | Competing attempts obtain unique contiguous sequences; semantic replay remains one event. | Retry from a freshly locked/read chain head. | No duplicate/gap, previous hash links, head equals last event. |
| Audit-chain tamper: `AUDIT_CHAIN_TAMPER` | Payload, event type, subject hash, previous hash, sequence, ordering, membership or head no longer matches the canonical hash chain. | Verification returns broken; no repair or trusted-history claim is made. | None. | A forged or changed record is never an idempotent replay. | Preserve the broken evidence and fail closed or route through the existing authorized review/operator boundary. | Tamper matrix detected, chain not auto-repaired, business authority remains zero. |
| Owner approval precondition failure: `PM_BEFORE_APPROVAL` | Wrong owner/tenant/hash/state/version, stale presentation, invalid receipt binding, or explicit denial/cancel. | Proposal remains `AWAITING_APPROVAL`; no approval receipt, commit or activation. | None. | A denied or invalid approval is not an idempotent success. | Refresh exact proposal state for the owner. A new deliberate human action may use a new valid request; no model/system retry may approve. | State/version unchanged; model/Critic/Kernel approval actions zero; no broader credential. |
| Approval acknowledgement loss: `PM_AFTER_APPROVAL_ACK_LOST` | Approval transaction may be durable but response is lost. | Completion unknown; do not show committed/active. | No blind new nonce or changed approval. | Exact approval request/nonce replay returns the same receipt and `APPROVED` state; changed replay conflicts. | Replay the exact owner-bound request and render the authoritative receipt/state. | One approval receipt/event/state edge; commit and activation counts remain zero. |
| Technical commit failure before commit: `PM_BEFORE_COMMIT` | Dedicated credential unavailable or proposal/receipt/slot/quota/binding/evidence/state revalidation fails. | Patch remains `APPROVED`; no committed patch, quota increment or commit receipt. | None, except an exact `40001` inside the existing transaction runner. | Failed precondition cannot be converted to replay success. | Repair only the owning condition through its existing authority, then submit an exact current commit request; stale approval may require renewed owner flow. | Atomic zero partial rows/quota/events; Commit Helper cannot approve or widen owner scope. |
| Technical commit acknowledgement loss: `PM_AFTER_COMMIT_ACK_LOST` | Commit transaction may be durable but response is lost. | Completion unknown; patch is not assumed active. | No changed request and no skip to activation. | Exact commit idempotency key and hashes return one committed patch/receipt; changed replay conflicts. | Replay the exact commit request under the Commit Helper role, then read authoritative state. | One patch, receipt, event and quota effect; content equals approved proposal. |
| Activation failure before commit: `PM_BEFORE_ACTIVATION` | Receipt/hash/state/slot/quota/model-binding revalidation fails. | Patch remains `COMMITTED`; retrieval remains ineligible. | None except exact `40001`. | No activation effect is claimed. | Refresh current state, correct only through existing owner/policy services, then issue a valid exact activation request. | Zero active-state/event partials; no state skip or content rewrite. |
| Activation acknowledgement loss: `PM_AFTER_ACTIVATION_ACK_LOST` | Activation may be durable but response is lost. | Completion unknown; callers reread rather than manufacture ACTIVE. | No changed request. | Exact activation replay returns the same receipt and one `ACTIVE` edge. | Replay the exact request under the activation service and verify state/content lineage. | One activation receipt/event; active content hash equals committed and approved hashes. |
| Other Personal Memory lifecycle transaction: `PM_LIFECYCLE_BEFORE_COMMIT` | Supersession, revocation or logical deletion fails before commit. | Prior lifecycle/retrieval eligibility remains authoritative. | Exact `40001` only. | Atomic transaction prevents half-applied lifecycle relation and retrieval suppression. | Replay the exact owner-scoped request after correcting the typed failure. | One relation/receipt/state edge or zero; no cross-owner change. |
| Personal Memory lifecycle acknowledgement loss: `PM_LIFECYCLE_AFTER_COMMIT_ACK_LOST` | A supersession, revocation or deletion transition may be durable while the caller loses its response. | Completion remains unknown until exact owner-scoped read/replay. | No changed request and no new replay identity. | Exact request replay returns the immutable lifecycle receipt and never duplicates retrieval suppression. | Replay the same tenant/owner/slot/patch/hash/state-version identity and verify current Step 31 eligibility. | One lifecycle record, unchanged content hash, consistent retrieval result, no cross-owner mutation. |
| Personal Memory export interruption: `PM_EXPORT_INTERRUPTED` | Deterministic owner export assembly stops before a complete bundle. | No ready bundle/hash is reported; source memory is unchanged. | None automatically. | Export is read-only and reproducible; repeated assembly may occur, but only one complete bundle identity is accepted. | Reissue the exact bounded owner request and rebuild in deterministic order from current authorized records. | No partial bundle success, no other-owner row, no secret, stable hash for identical state. |
| Review handoff before durable transition: `REVIEW_BEFORE_HANDOFF` | Subject/case/audit/authorization/state revalidation or typed downstream handoff fails. | Case remains unresolved or explicitly escalated; business state is unchanged. | None except exact transaction `40001`. | A decision receipt cannot authorize an arbitrary or stale handoff. | Refresh subject and verified audit context, then replay only an allowed exact typed request or create a new review version. | No arbitrary SQL/publication/execution; one compatible terminal decision at most. |
| Review handoff acknowledgement loss: `REVIEW_AFTER_HANDOFF_ACK_LOST` | Typed handoff and case resolution may be durable while response is lost. | Completion unknown to caller. | No changed decision or second terminal action. | Exact handoff replay returns one result/receipt and one resolved state. | Replay the exact decision/case/subject hashes and verify audit context. | One handoff, one resolution, same receipt hash, no incompatible decision. |
| Dedicated credential unavailable: all `CREDENTIAL_*_UNAVAILABLE` points | Commit Helper, provider, reviewer or audit-appender exact credential is absent. | Owning operation fails closed with sanitized configuration error. | None and no broader-role retry. | No semantic effect exists. | Provision/restore only the exact credential purpose, validate its positive and negative capabilities, then replay the original typed request if still current. | No migrator/admin/master fallback; no capability composition; secret leakage zero. |

## Closed failure-point coverage

The Step 37 registry is versioned as
`step37-closed-failure-point-registry-1a`, with recovery policy
`step37-recovery-policy-1a` and schema `1.0.0`. The rows above cover every
closed point in these registry families:

- database and transaction: all `DB_*` points;
- restart and saga: all `PROCESS_*` and `SAGA_*` points;
- provider: all `PROVIDER_*` points;
- S3: all `S3_*` points;
- external volume: all `VOLUME_*` points;
- audit: all `AUDIT_*` points;
- Personal Memory: all `PM_*` points;
- review handoff: all `REVIEW_*` points; and
- missing dedicated authority: all `CREDENTIAL_*_UNAVAILABLE` points.

Vector-index staleness, canonical-evidence conflict and explicit/stale failed
approval are existing computed business states rather than permission to add
new production fault hooks. They are nevertheless mandatory Step 37 recovery
rows because the roadmap names them explicitly.

## Cross-domain invariants

1. No model, Critic, HAT, failure injector, audit event, recovery worker or
   reviewer gains approval, technical commit, canonical publication or
   external-execution authority.
2. Tenant, owner, slot, route, source, HAT scope, state version, immutable hash
   and dedicated credential checks are revalidated after every restart or
   ambiguous completion.
3. A recovery never retries with migrator/admin authority, widens scope, skips
   a state, changes approved content, rewrites audit history, deletes an S3
   object, or falls back from the external volume to the system disk.
4. Provider and storage calls remain outside retried CockroachDB transaction
   callbacks. The callbacks inspected for Step 6, Step 30 and Step 33 contain
   database repository calls and deterministic contract work only.
5. Failures expose sanitized reason codes and explicit final states. Unknown
   completion remains unknown until exact reconciliation succeeds.
6. Step 8 production recovery is conservative and operator-mediated. A
   bounded test may model “fault, operator restores exact volume, explicit
   replay,” but it is not an autonomous production retry loop.
7. Step 37 validation uses fakes and owned disposable resources. It performs
   no production mutation and no AWS/S3 mutation.
8. Step 38 German Law full end-to-end integration is not implemented or
   started by this matrix.

## Acceptance state

This matrix is complete as a policy artifact. The controlled validator binds
its exact SHA-256 digest into sanitized evidence and rejects missing injected
or computed proof domains. Git completion and push are recorded only by the
separate closure record; no final Step 37 commit SHA is recorded here.
