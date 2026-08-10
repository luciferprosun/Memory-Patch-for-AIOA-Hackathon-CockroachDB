# Step 30 User Approval, Commit and Activation Validation 1A Runbook

## Preflight

Run from the repository root on `main`. Require a clean worktree, no active
Git operation, the expected remote, and `main...origin/main = 0 0`. Confirm
Step 29 is complete and pushed while Steps 30 and 31 are not started. Freeze
`origin/main` as the Step 30 base SHA.

Use only the pinned repository CockroachDB binary and an owned disposable
runtime. Do not use production data or credentials. Validation performs no
provider/model call, retrieval, web access, AWS/S3 mutation, external action,
or Step 31 lookup.

## Focused checks

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step30_user_approval_commit_activation -q

python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
```

The focused suite verifies immutable request/receipt/state contracts, exact
content identity, trusted-time revalidation, owner-only approval, independent
replay identities, no-skip transitions, least-privileged migration policy,
quota serialization, RLS/FORCE RLS and absence of Step 31 APIs.

## Controlled validation

```bash
python3 scripts/run_step30_user_approval_commit_activation_validation.py
```

The runner starts one disposable CockroachDB `v26.2.4`, applies and replays
all fourteen migrations, validates role/function/table grants and the
role-aware epoch-only slot-update trigger, verifies that the helper's
foreign-key identity and run-lineage reads are `SELECT`-only and exact
tenant/owner RLS-scoped, including the owner-private audit FK check, creates
one normal application login and one
separate test login that inherits only
the request-context setter and Commit Helper roles, then builds a real Step 29
`AWAITING_APPROVAL` fixture.

Expected positive matrix:

- owner `HUMAN_USER` approval and deterministic approval receipt;
- `AWAITING_APPROVAL -> APPROVED`;
- dedicated technical `APPROVED -> COMMITTED` with inactive durable patch;
- activation-service `COMMITTED -> ACTIVE`;
- proposal, committed and active statement SHA-256 values are identical;
- exact approval, commit and activation replays return their receipts.

Expected negative matrix:

- direct `AWAITING_APPROVAL -> COMMITTED` and `APPROVED -> ACTIVE` fail;
- changed reuse of every replay identity fails;
- changed post-presentation hashes fail;
- archived or delete-pending slot, quota exhaustion, stale evidence, wrong
  owner/tenant and cross-owner Commit Helper access fail;
- Commit Helper cannot insert approval, update HAT/source registries, change
  slot material, delete private carriers, or perform external execution.

## Full regression

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
```

Retain focused Step 17 and Step 20-29 regressions, Step 5 RLS, Step 6
persistence/idempotency, Personal Memory authority invariants, tenant/user
isolation and canonical serialization.

## Failure and cleanup

Any integrity, owner, state, evidence, slot, quota, binding, role, RLS,
replay, content-identity or cleanup failure blocks the Git closure commit.
The validator drops only its owned database and temporary roles, stops its
owned process, verifies closed ports, and removes its temporary store. Never
commit secret values, role login credentials, private proposal text or
machine-specific paths in evidence.
