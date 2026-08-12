# Step 42 RC Backup and Restore Operations 1A

## Safety preflight

1. Require `main`, the expected origin, clean worktree, local/origin equality,
   divergence `0 0`, no Git operation, Step41 complete, and Steps42/43 not
   started.
2. Confirm the pinned external volume, local E5 artifact revision, CockroachDB
   v26.2.4 binary, Step40 profile, approved OpenRouter/Kimi configuration, and
   environment-only provider capability. Never print credential values.
3. Confirm no `mp-step42-recovery-*` directory or owned Step42 process remains.
4. Run focused contracts:

   ```text
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
     -m unittest tests.test_step42_rc_manifest \
     tests.test_step42_backup_restore \
     tests.test_step42_recovery_authority -q
   ```

## Controlled validation

Run only from the canonical repository:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  scripts/run_step42_rc_backup_restore_validation.py --write-evidence
```

The launcher consumes the exact provider capability, removes it from ambient
inheritance, and re-executes only in the pinned E5 runtime when needed. It then:

1. builds and deep-verifies the recovery-asset and RC manifests;
2. creates a mode-0700 Step42 recovery root and nodelocal backup directory;
3. starts one source CockroachDB and applies/replays 18 migrations;
4. runs German Law retrieval/embedding, Personal Memory, owner-RLS, and audit
   fixtures;
5. freezes a pre-backup watermark;
6. runs detached native backup and native `check_files`;
7. creates/verifies the backup tree receipt;
8. stops the source runtime before starting one distinct restore runtime;
9. bootstraps cluster roles in a temporary database, restores under a new
   database name, and requires 18 replay skips;
10. compares the exact restored watermark;
11. reruns German Law exact/full-text/vector/hybrid/temporal and the approved
    provider correction/verifier path;
12. validates restored active Personal Memory, owner/tenant negatives,
    canonical-conflict suppression, audit chain/tamper detection, optional
    Critic, and Step40 readiness; and
13. drops owned databases/roles, stops exact PIDs, verifies closed ports, and
    removes runtime plus backup trees.

Only sanitized progress stage names are printed. The final stdout line is one
canonical JSON object. A non-zero exit or `closure_eligible=false` is failure.

## Failure diagnosis

- `...REPOSITORY...`: repair the guard; do not mutate the worktree or restore.
- `...PROVIDER...`: provision the approved current capability through its
  existing environment boundary; do not substitute a model or archive a key.
- `...MIGRATION...`: inspect migration identity and controlled runtime logs;
  never bypass or edit bookkeeping rows.
- `...FIXTURE...`: identify the smallest source/Personal Memory/audit contract
  mismatch; do not skip that state class.
- `...BACKUP...`: discard the owned backup tree after cleanup. Never accept an
  unchecked, missing, corrupt, or extra file.
- `...RESTORE...`: do not claim readiness, do not partially activate Personal
  Memory, and never fall back to a broad credential.
- `...WATERMARK...`: treat any mismatch as tested-fixture data loss or drift.
- `...AUDIT...`: do not repair or rewrite history automatically.
- `...CLEANUP...`: stop and resolve only the exact owned PID/path/port before
  another attempt.

## Final validation

After the controlled proof, run compileall, the three focused Step42 modules,
the full supported unittest discovery, contract validator, dependency check,
UI asset check, Step39 Critic validation, Step40 resource validation, high-value
Step41 regression, and `git diff --check`. Update the final regression count in
the Step42 evidence and closure record before the one closure commit.

## Production recovery limitation

This runbook proves a controlled single-node restore using a local nodelocal
artifact. A production operator must separately select encrypted durable remote
storage, access/KMS policy, retention, scheduling, multi-node/managed restore,
and operations audit. This Step42 procedure neither reads nor writes AWS.
