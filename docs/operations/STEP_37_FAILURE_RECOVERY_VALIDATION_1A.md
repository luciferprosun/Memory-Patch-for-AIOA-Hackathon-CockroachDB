# Step 37 Failure Injection and Recovery Validation 1A

## Purpose and hard boundary

This runbook validates deterministic failure handling and exact recovery from
base `f7882eed42534fb5c07bf55694886a2fca11823e`. It exercises existing Step
6-36 authority and persistence boundaries; it does not create a production
chaos service.

Run only with repository fakes, temporary files and, where database behavior
requires it, one owned disposable loopback CockroachDB v26.2.4 runtime. Never
point the runner at production or shared infrastructure. Do not retrieve a
production credential, call a real model/provider, mutate AWS/S3, alter a real
external volume, execute an external action or start Step 38.

The policy source is
`docs/reliability/STEP37_FAILURE_RECOVERY_MATRIX_1A.md`. A case that is not in
that closed matrix is not authorized by this runbook.

## Repository and sequencing preflight

From the repository root inspect:

```text
git status -sb
git branch --show-current
git rev-parse HEAD
git remote -v
git diff --name-only
git rev-list --left-right --count main...origin/main
```

At Step 37 task start require:

- branch `main`;
- expected GitHub remote;
- clean worktree;
- no merge, rebase, cherry-pick, revert or bisect;
- `main...origin/main=0 0`;
- local `HEAD` and `origin/main` equal the exact Step 36 base
  `f7882eed42534fb5c07bf55694886a2fca11823e`;
- Step 36 `COMPLETE AND PUSHED`; and
- Steps 37 and 38 not started.

Read `AGENTS.md`, the roadmap, the Step 6 persistence/retry architecture, the
Step 7 S3 adapter, Step 8 external-volume fail-closed policy, Step 10 saga,
Steps 19-21 retrieval/evidence policy, Step 22/25/26 provider/output policy,
Step 30 Personal Memory lifecycle, Step 33 audit, Step 34 review and Step 36
credential matrix before accepting results.

## Failure-injection safety gate

Before any campaign verify:

- `FailurePoint` and `FailureDomain` are closed enums;
- the point-to-domain registry contains every enum member;
- `FailureDirective` permits only sorted, unique occurrences from 1 through
  the bounded maximum;
- scripted injection is contained under `tests/failure_injection` and is not
  imported by Step 30, Step 33, Step 34 or another production service;
- the production default is inert;
- results are immutable/hash-bound and carry explicit attempt, duplicate,
  authority and integrity counts; and
- error messages contain only sanitized point/reason identities.

Do not add an environment switch, HTTP route, UI control or runtime command
that enables production failure injection.

## Focused offline gates

Run the Step 37 focused suites without network or credentials:

```text
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step37_failure_injection -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step37_recovery_idempotency -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step37_authority_recovery -q
```

These suites must remain deterministic. A retry test uses a fixed occurrence
script and injected clocks/sleeps. It must never depend on random packet loss,
wall-clock races, a paid provider or a real object store.

## CockroachDB and transaction campaigns

Validate the Step 6 runner at the exact boundaries below:

1. inject a structured `40001` before commit;
2. prove rollback and complete callback replay;
3. prove only the later attempt is durable;
4. inject a non-`40001` interruption before commit and prove no automatic
   retry;
5. delegate commit successfully, then raise a sanitized release/acknowledgment
   failure;
6. reconnect and replay the exact idempotency identity;
7. prove exactly one semantic result; and
8. restart a bounded child after one durable write and rediscover that exact
   result.

Do not classify acknowledgement loss as `40001`. The result is unknown until
exact replay reconciles it. Every transaction callback must be reviewed for
network/provider/S3/filesystem/subprocess work. Current Step 6, Step 30 and
Step 33 callbacks are expected to contain only repository calls and
deterministic contract/policy work.

If a real database probe is added, use the pinned repository migration runner,
unique owned database/LOGIN names and loopback-only disposable storage. Never
kill a non-owned process. Do not launch a second runtime if another repository
validator already owns the same resources.

## Ingestion, S3 and Object Lock campaigns

Use strict fake S3 clients only. Do not invoke AWS CLI or an SDK client backed
by ambient identity.

Required cases:

- pre-write service outage returns a typed failure and no evidence;
- ambiguous write acknowledgement is followed by exact head/get
  reconciliation, not a second put;
- checksum, content length, metadata, version and lock mismatches fail closed;
- streaming bodies close on both success and failure;
- a Step 10 outage exhausts only its bounded attempts and enters
  `RETRY_WAIT`; and
- saga resume reconciles one snapshot and one Step 9 publication without a
  duplicate external effect.

Record `S3_MUTATIONS=0` and `AWS_MUTATIONS=0` for Step 37 validation. Fake
method-call counters are test observations, not cloud mutations.

## External-volume campaigns

Use only a temporary test directory and synthetic mount-probe outcomes. Never
write to the operator's real Step 8 volume during Step 37.

Validate missing, read-only/write-failure, atomic-finalization and corrupt-cache
states. Required behavior is conservative:

- required operations fail closed;
- optional caches are disabled without returning a system-drive path;
- no directory, target or incomplete staging artifact is silently deleted;
- a target is never overwritten; and
- rebuild accepts bytes only when exact canonical inputs, length and SHA-256
  verify.

Operational recovery is manual. The operator restores and reverifies the
exact mount/device/marker/access/capacity boundary and then explicitly replays
or rebuilds. A two-outcome unit fixture may model that sequence, but it must not
be described as an autonomous production retry policy.

## Provider and verified-output campaigns

Use a protocol-compatible fake provider and the existing Step 22/25/26
services. Inject timeout, transient error, response loss, authentication error,
invalid response and oversized response.

Prove:

- provider calls occur outside persistence transactions;
- transient retries stay inside the existing service attempt ceiling;
- no third Step 22/25 call occurs after two failed attempts;
- terminal failures stop after one attempt;
- `unknown_completion` remains true when any earlier attempt was ambiguous;
- provider execution is reported as at-least-once/possibly duplicated, never
  exactly once;
- no invalid text is persisted; and
- Step 26 returns review/bounded failure and never falls back to known-bad
  Draft V1 under `HAT_ENFORCE`.

No provider network request or provider credential is permitted.

## Vector-index and evidence-policy campaigns

Build synthetic, hash-valid Step 18-21 fixtures. Do not rebuild a production
index.

For a stale or unavailable vector index, prove that the vector modality
contributes no accepted candidate. Exact/full-text results may proceed only
when the existing route and evidence policy truthfully permits a `PARTIAL`
bundle; otherwise the run fails closed. A derived rebuild must be bound to the
exact published chunks, embedding model/revision and lineage digests.

For conflicting canonical evidence, preserve the Step 21 conflict group,
temporal scope and hashes. Prove that no private patch, model output or retry
turns the conflict into certainty. The result must remain qualified,
human-review-required or bounded-failure according to the existing Step 26
policy.

## Personal Memory campaigns

Exercise every Step 30 phase separately with exact existing fixtures:

- failed/stale/wrong-owner approval leaves `AWAITING_APPROVAL` and creates no
  receipt;
- approval acknowledgement loss replays one approval receipt and one
  `APPROVED` edge;
- precommit failure leaves `APPROVED` with no patch/quota/event partials;
- commit acknowledgement loss replays one patch, receipt, event and quota
  effect;
- preactivation failure leaves `COMMITTED` and retrieval-ineligible;
- activation acknowledgement loss replays one receipt and one `ACTIVE` edge;
- supersession/revocation/delete interruption is atomic; and
- interrupted owner export never reports a partial bundle as ready.

Human denial is not retryable approval. No model, Critic, Kernel, reviewer or
recovery harness may approve. The Commit Helper remains the exact Step 36
least-privileged technical boundary and cannot be replaced with application,
reviewer or migrator authority during recovery.

## Audit and review campaigns

For Step 33, inject before append, after durable append acknowledgement loss
and chain-head contention. Prove event-plus-head atomicity, stable receipt
adapter timestamps, exact event replay, unique contiguous sequence and a
verified chain/head. The verifier must not repair a broken chain.

For a business receipt that predates a missing audit append, reconstruct the
typed draft from immutable receipt fields and its business timestamp. Do not
use a new wall-clock `recorded_at`, because a changed draft hash under the same
audit idempotency key is a conflict rather than recovery.

For Step 34, inject before typed handoff and after handoff acknowledgement
loss. Prove stale subject/audit/authorization checks fail closed, exact replay
resolves once, and no incompatible terminal decision or publication/execution
authority appears.

## Credential and scope failures

Remove each exact test-only credential input in turn. Verify that Commit
Helper, provider, reviewer and audit appender operations fail closed and never
retry with `DATABASE_URL`, migrator/admin, reviewer, application or another
purpose.

Repeat cross-user, cross-tenant, wrong-slot, wrong-subject and mixed-role
negatives after a fault. Recovery must revalidate trusted server-side scope;
it cannot trust replayed browser/model fields or a receipt hash as a bearer
credential.

## Controlled validator

After focused tests pass, run the bounded orchestrator:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_step37_failure_recovery_validation.py
```

The runner must emit bounded progress separately from one sanitized canonical
JSON result. It must report every required matrix domain, recovery status,
attempt count, final semantic state and zero duplicate/authority/integrity
violations. A missing required case is failure, not `NOT_APPLICABLE`, unless
the canonical matrix itself explicitly marks the domain as computed rather
than injected.

Do not write the validation evidence artifact until the output digest and all
mandatory cases have been independently checked. The closure run exercises 55
bounded cases. Its final controlled result is `PASS` and the canonical evidence
digest is
`59983b1b399118897440519d98a7ff27c052c85cb5f2007414ada16d2aa97fcc`.

The owned disposable-database phase has independently passed against
CockroachDB v26.2.4. It applied all 18 migrations, replayed all 18 as already
applied, observed the injected retryable `40001`, rejected a changed replay
with `23505`, retained exactly one durable row and completed cleanup without a
force kill or CockroachDB panic. This is a disposable validation result, not a
production-database mutation.

## Contract, regression and static checks

Run after controlled validation succeeds:

```text
python3 scripts/validate_contracts.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m unittest discover -s tests -p 'test*.py' -q

rg -n "ScriptedFailureInjector|tests\.failure_injection" \
  src/aioa_memory_kernel || true

rg -n "subprocess|os\.system|shell=True|boto3|botocore|urlopen|requests\." \
  src/aioa_memory_kernel/reliability tests/failure_injection \
  scripts/run_step37_failure_recovery_validation.py 2>/dev/null || true
```

Review every static hit. Imports or calls in existing provider/storage modules
do not authorize the Step 37 runner to invoke them. Production runtime modules
must not import the scripted test harness.

Run focused regressions for Steps 5-10, 17-22, 25-36, including tenant/user
isolation, RLS/FORCE RLS, transaction cleanup, Object Lock assumptions,
Personal Memory state/hash identity, audit integrity, review authorization and
credential separation.

Observed closure-worktree gates are:

- Step 37 focused suites: 44 tests, failures `0`, errors `0`;
- Python compileall: `PASS`;
- pinned frontend asset-integrity check: `PASS`;
- contract validator: `PASS`; and
- full repository discovery: 1,959 tests, failures `0`, errors `0`.

These counts describe the exact observed run. They must be rerun after the
closure commit as required by the Step 37 prompt; a later run must not be
silently represented by these pre-commit counts.

## Cleanup and acceptance

The runner must remove every owned temporary file, temporary database, test
LOGIN, process, port and memory store. It must close streaming bodies and child
process handles. Cleanup failure is a failed campaign and must not trigger a
broad or destructive cleanup command.

Acceptance requires:

- every row in the recovery matrix has an exercised or explicitly computed
  proof;
- all duplicate, authority and integrity violation counts are zero;
- unknown completion is reconciled rather than guessed;
- no production/provider/AWS/S3/external-volume mutation occurred;
- focused, controlled, contract and full-regression gates pass;
- sanitized evidence is written and digest-verified;
- Step 37-only changes pass the changeset gate; and
- Step 38 remains `NOT STARTED`.

This runbook intentionally does not record a final Step 37 commit SHA. Git
closure and push reachability are recorded only after the approved commit
exists and `main...origin/main=0 0` has been verified. Step 38 remains
`NOT STARTED`.
