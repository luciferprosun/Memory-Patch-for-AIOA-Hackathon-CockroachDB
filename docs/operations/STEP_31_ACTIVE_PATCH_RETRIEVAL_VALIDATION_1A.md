# Step 31 Active Patch Retrieval Validation 1A Runbook

## Preflight

Run from the repository root on `main`. Require a clean worktree, no active
Git operation, the expected remote and `main...origin/main = 0 0`. Verify that
Step 30 is complete and pushed while Steps 31 and 32 are not started, then
freeze `origin/main` as the Step 31 base.

Use only the pinned repository CockroachDB binary and an owned disposable
runtime. Do not use production data, provider credentials or personal user
content. The validation performs no model/provider call, web request, AWS/S3
mutation, approval, commit, patch-state mutation or shared promotion.

## Focused checks

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step31_active_patch_retrieval -q

python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
```

The focused suite verifies immutable hashes, Step 30 lifecycle
reconstruction, ACTIVE-only retrieval, exact content identity, owner/tenant
and slot isolation, route scope, temporal applicability, provider-neutral
model bindings, canonical-conflict suppression, bounds, deterministic
ordering and the read-only Step 32 boundary.

## Controlled validation

```bash
python3 scripts/run_step31_active_patch_retrieval_validation.py
```

The runner starts one disposable CockroachDB `v26.2.4`, applies and replays
all fourteen migrations, checks the existing retrieval index and RLS/FORCE
RLS catalog, and creates sanitized Step 30 lifecycle fixtures.

Expected positive matrix:

- one exact `ACTIVE` owner patch is retrieved;
- two distinct provider/model identities return the same patch ID, patch hash
  and content SHA-256;
- exact scope and temporal applicability pass;
- the context is immutable and marked private/non-canonical;
- owner RLS sees one row and semantic state is unchanged after retrieval.

Expected negative matrix:

- `COMMITTED` and `AWAITING_APPROVAL` records are not retrieved;
- an unbound third model is denied;
- scope and temporal mismatches are denied;
- current canonical-evidence conflict suppresses the patch;
- same-tenant cross-user and cross-tenant sessions see zero rows.

The default result bound is 8, the maximum returned bound is 32, and the hard
candidate ceiling is 128. Queries are parameterized and deterministic. Real
provider inference is not required because cross-model reuse is a storage,
identity and applicability property.

## Full regression

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
```

Retain focused Step 17-31 regressions, Step 5 RLS, Step 6
persistence/idempotency, Personal Memory authority invariants, tenant/user
isolation and canonical serialization.

## Failure and cleanup

Any integrity, state, owner, tenant, slot, scope, temporal, binding,
canonical-evidence, RLS, bound or cleanup failure blocks the Git closure
commit. The validator drops only its owned database and temporary roles,
stops its owned process, verifies closed ports and removes its temporary
store. Never commit secrets, raw private query/patch text, role login values
or machine-specific paths in evidence.
