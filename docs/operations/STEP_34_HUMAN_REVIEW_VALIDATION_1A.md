# Step 34 Human Review Validation 1A

## Purpose and safety

This runbook validates typed review intake, bounded queueing, reviewer
claim/decision concurrency, stale-decision protection, typed handoff, Step 33
audit integration and least-privileged RLS. It starts one owned disposable
CockroachDB and makes no model/provider, web, AWS/S3, retrieval, external
execution or source-publication call. It creates no Step 35 UI.

## Preflight

From the repository root require `main`, clean worktree at task start, the
expected remote, `main...origin/main=0 0`, no active Git operation and exact
Step 33 base `6f8f14b8acde20a8044d929ba7f6582f2c36785b`.

Verify that Step 33 architecture, ADR, runbook, evidence, closure, roadmap
and AGENTS checkpoint are reachable from that commit; Steps 34 and 35 must be
not started. Verify the pinned CockroachDB v26.2.4 binary through the existing
external-volume configuration. Never supply a production database.

## Static and focused validation

Run:

```text
python3 -m compileall -q src scripts tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_step34_human_review_workspace -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_step34_review_authorization -q
python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
```

Review new code and SQL for closed case/decision types, exact hashes and state
versions, bounded notes/context, parameterized tenant predicates, RLS/FORCE
RLS, no BYPASSRLS and no arbitrary business mutation API.

## Controlled disposable validation

Run:

```text
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_step34_human_review_validation.py
```

The script verifies and copies the pinned binary, starts one owned runtime,
creates a unique database and four no-BYPASSRLS validation logins, applies all
17 migrations, replays them idempotently and verifies the live Step 34
catalog. The logins inherit only app, human-reviewer or review-service plus
request-context capabilities.

The real controlled path uses one Step 26 `HumanReviewRequired`, a persisted
Step 33 source event and verified owner chain. A sanitized typed Step 32
shared-promotion context exercises its separate decision family. The script
then proves:

- exact case, claim, decision and handoff replay, including case/claim
  read-back after the lifecycle has advanced;
- service and database denial of a non-OPEN initial case;
- deterministic fixed-order one-winner database claim conflict, while the
  focused suite separately proves the real threaded two-reviewer race;
- minimum-disclosure, bounded queue and detail;
- invalid cross-family, stale and changed-subject decisions are denied;
- answer and shared-promotion handoffs preserve their authority boundaries;
- all security-relevant actions append to a chain that still verifies;
- ordinary runtime lacks review reads, and unauthorized same-tenant and
  cross-tenant reviewer contexts see no cases;
- broken audit context is explicitly fail-closed; and
- no answer, source publication, private mutation or execution action occurs.

Progress is bounded JSON on stderr; stdout is one canonical sanitized result.
Failure still executes exact database, role, process, port and temporary-store
cleanup.

## Full regression and static gates

Run full discovery and the focused Step 17/20-33, persistence, RLS, authority,
tenant/user, audit-integrity and serialization regressions:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test*.py' -q
```

Run and review the prompt-specified Step 35 leakage, authority and secret
searches. Expected closed vocabulary and comments are not authority. Run
`git diff --check`, inspect every changed path, and stage only Step 34 files.

## Cleanup and acceptance

Accept only when database and all validation roles are removed, the exact
owned process exits, ports close, the temporary store is removed and no force
kill is used. Recompute the validation digest after removing its own field.

The final gate requires all tests and validators green, one Step 34 closure
commit, non-force push, clean status and local/remote divergence `0 0`.

Confirm `step35_started=false` and `personal_memory_end_user_ui=0`.
