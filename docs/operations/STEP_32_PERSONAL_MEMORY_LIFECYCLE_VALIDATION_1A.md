# Step 32 Personal Memory Lifecycle Validation 1A

## Purpose and safety

This runbook validates supersession, revocation, owner export, logical
deletion and review-only shared promotion. It uses synthetic, non-sensitive
fixtures and an owned disposable CockroachDB. It makes no provider/model,
web, AWS/S3, external-action, source-publication, UI, or production database
call.

## Preflight

From the repository root, require `main`, a clean worktree, the expected
remote, `main...origin/main=0 0`, no active Git operation, and exact Step 31
base `bf6cde9de87ab727f1bd5e48e2abfc7e8e3b85b5`. Verify Step 31 is complete
and pushed while Steps 32 and 33 are not started.

Verify the pinned CockroachDB binary digest through the existing validation
bootstrap. Never point this validation at a production database.

## Static and focused checks

Run:

```text
python3 -m compileall -q src scripts tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_step32_personal_memory_lifecycle -q
python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
```

Review new SQL for exact tenant/owner/slot predicates, RLS/FORCE RLS,
append-only grants, guarded terminal updates and absence of BYPASSRLS,
physical DELETE, source publication, or broad service authority.

## Controlled database validation

Run:

```text
python3 scripts/run_step32_personal_memory_lifecycle_validation.py
```

The script owns its temporary binary copy, in-memory store, ports, database,
roles and request contexts. It applies all 15 migrations, validates migration
replay and catalogs, then proves:

- two exact active patches can form one same-owner supersession;
- exact replay converges and changed replay is denied;
- current retrieval selects the successor and historical retrieval preserves
  the old patch before the effective time;
- an owner revocation is persisted and the patch disappears from retrieval;
- export is deterministic, bounded and owner-only;
- logical deletion completes `DELETED_PENDING` to `DELETED` and suppresses
  retrieval without claiming physical deletion;
- separate owner consent, deterministic de-identification and
  `review_required=true` produce only a shared-promotion proposal;
- no source-registry row is published;
- same-tenant cross-user and cross-tenant reads/mutations are denied;
- the Step 30 Commit Helper can satisfy CockroachDB inbound-FK checks but,
  without a Step 32 SELECT policy, sees zero lifecycle rows; its two
  trigger-planning EXECUTE grants provide no Step 32 mutation policy;
- RLS, FORCE RLS, helper ownership/grants, triggers and no-DELETE grants match
  the security manifest.

The script must finish with a canonical JSON result, a matching validation
digest, zero external effects and complete cleanup. A failed assertion must
also execute cleanup.

## Full regression

Run full discovery and the focused Step 17-31, RLS, persistence, authority,
tenant/user-isolation and serialization regressions:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test*.py' -q
```

All tests must report zero failures and zero errors. Run `git diff --check`
and inspect the changeset before staging.

## Cleanup verification

The controlled result must report removal of its database, roles and
temporary store, an exited CockroachDB process, closed ports and no forced
kill. Confirm that no Step 32 validation process remains. Do not manually
remove an unresolved broad path; cleanup targets only the script-owned
temporary directory.

## Later-step guard

Confirm `step33_started=false`, global audit ledger count `0`, human review
workspace count `0`, Personal Memory UI count `0`, and source publication
count `0`. Append-only Step 32 receipts are not a Step 33 global audit ledger.
