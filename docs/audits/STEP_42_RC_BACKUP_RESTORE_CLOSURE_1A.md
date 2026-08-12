# Step 42 RC Backup and Restore Closure 1A

Record status: VALIDATION PASSED; PRE-COMMIT CLOSURE.

Step 41 base:
`26577fa02c96da7a4b4ae49cdc5f3c168eb1ed80`

Final Step 42 RC Git commit: NOT CREATED.

Push to `origin/main`: NOT PERFORMED.

Step 43: NOT STARTED.

## Scope

Step42 freezes a deterministic RC identity, inventories every recovery asset,
backs up an RC-compatible authoritative CockroachDB fixture, restores it into a
distinct disposable runtime, and validates schema, authority, provenance,
retrieval/model/correction, Personal Memory, audit, optional Critic, and Step40
profile behavior. It performs no AWS operation, secret rotation, production
mutation, feature expansion, or Step43 work.

Architecture:
`../architecture/RC_FREEZE_BACKUP_RESTORE_1A.md`.

Decision:
`../adr/ADR-048-rc-freeze-backup-restore-1a.md`.

Operations:
`../operations/STEP_42_RC_BACKUP_RESTORE_1A.md`.

## RC and recovery freeze

- Step41 parent: exact pushed SHA above.
- RC manifest: `../evidence/release/step42-rc-manifest-1a.json`.
- Recovery assets:
  `../evidence/release/step42-recovery-asset-manifest-1a.json`.
- RC Git identity: the eventual one Step42 closure commit, reported after
  creation and push rather than self-embedded in its parent-bound manifest.
- Freeze rule: no post-closure feature, provider/model, dependency, schema,
  prompt semantic, authority, source/corpus, Personal Memory state-machine, or
  Critic authority change without reopening and revalidating the RC.

## Bounded defects discovered

1. The new Step42 validation initially referenced the wrong module for the
   existing external-runtime configuration constant. It failed before database
   creation; the bounded fix uses the established Step27 external config path.
2. The Step31 and Step33 controlled fixtures intentionally share a synthetic
   tenant/owner root. Sequential composition initially attempted a duplicate
   identity insert. The bounded fix makes only the Step33 identity seed
   idempotent with `ON CONFLICT DO NOTHING`; it does not change business rows,
   RLS, audit, or Personal Memory semantics.
3. The first complete restore reached every post-restore application check but
   read the Step40 profile digest from a legacy field name (`digest`) instead
   of the canonical evidence field (`profile_digest`). The bounded fix reads
   the exact current Step40 contract and adds a direct restored-profile smoke
   regression. No budget, readiness, or authority value was relaxed.
4. A final fail-closed audit found that a PASS evidence file would have been
   written immediately before the cleanup `finally` block. The bounded fix
   materializes PASS only after database/process/path cleanup succeeds. The
   same audit moved external-I/O path validation ahead of runtime-directory
   allocation and rejects symlink or non-private roots.
5. The first model/correction projection used only the Step38 backup case.
   The bounded fix restores the canonical primary-first case order and permits
   the backup case only for the two exact Step38 fallback reasons.
6. Post-restore retrieval originally depended on a private Step38 helper that
   also seeded source state. The bounded fix adds a public, primary-only,
   no-seed replay entry point and leaves the historical Step38 closure path
   unchanged.
7. The evidence secret scanner initially treated the recovery classification
   name `SECRET_DO_NOT_ARCHIVE` as a secret-shaped mapping key. The bounded
   representation projects classifications as sorted pairs, preserving the
   semantic label without weakening the scanner.
8. A correct real-provider answer with no defect is not correction-eligible.
   The bounded result classifier now accepts only either verified corrected
   lineage or the exact canonical no-defect fail-closed result; every other
   provider/model failure remains blocking. The successful final drill
   produced real verified corrected lineage on the primary case.
9. Full regression found one stale historical checkpoint assertion that still
   expected Step42 to be unchecked while simultaneously requiring its closure
   text. The one-line boundary fix now requires Step42 checked and Step43
   unchecked; no runtime behavior changed.

The same pre-final audit expanded the exact recovery watermark to cover
approval, commit, transition, owner slot/model/quota binding, idempotency,
source provenance/publication, and audit-event state directly. This is proof
hardening only; the native database backup mechanism and runtime semantics did
not change.

Every attempt cleaned its exact owned resources and touched no production
resource.

## Validation ledger

| Gate | Required | Observed |
|---|---|---|
| RC/recovery manifest contracts | deterministic, deep verified, secret-free | PASS; RC `memory-patch-aioa-rc-1a-baf2523ff423537b` |
| Native backup | job success, native check-files, SHA-256 tree receipt | PASS; 42 files, 538242 bytes, tree `3c2cf4ea...cf755` |
| Isolated restore | new cluster/database, exact watermark | PASS; watermarks equal, tested-fixture data loss 0 |
| Migration state | 18 applied source, 18 replay skips source/restore | PASS; 18/18/18, CockroachDB v26.2.4 |
| RLS/roles | no widening, catalog equality | PASS; every authority counter 0 |
| German Law retrieval/temporal | exact/full-text/vector/hybrid PASS | PASS; primary case, hard-filter leaks 0 |
| Approved model/correction pipeline | real verified lineage or canonical fail-closed | `PASS_REAL_VERIFIED_LINEAGE`; OpenRouter/Kimi K2, tools disabled |
| Personal Memory | owner-bound ACTIVE only, conflict suppression | PASS; owner rows 1, cross-user/tenant rows 0 |
| Audit | chain verified, all tested tamper detected | PASS; 8 events, 10/10 tamper cases detected |
| Critic | optional, zero authority, no restored promotion | `PASS_OPTIONAL_CANDIDATE_ONLY`; persisted candidates 0 |
| Step40 profile | digest/readiness, no bypass | PASS; fresh peak 786 MiB below 3000 MiB gate |
| Focused Step42 tests | zero failures/errors | 23/23 PASS; failures 0, errors 0 |
| Full final regression | zero failures/errors | 2190/2190 PASS; failures 0, errors 0 |
| Contract/UI/dependency checks | PASS | contract validator, UI asset check, `pip check` PASS |
| Step39/Step40 controlled regressions | PASS | `PASS_PROVIDER_FREE_CONTROLLED`; `PASS_4GB_CONTROLLED` |
| `git diff --check` | PASS | PASS on final pre-commit tree |

## Security and recovery acceptance

Closure requires exact zero values for secret leakage, cross-tenant and
cross-owner unauthorized success, authority escalation, broad BYPASSRLS
introduction, Commit Helper approval spoof, Critic authority escalation,
known-bad Draft V1 fail-open, undetected tested audit tamper, and production
resources touched.

## Limitations

- Local controlled single-node CockroachDB, not production HA/DR.
- Local mode-private nodelocal backup, not production KMS encryption.
- Immutable S3 versions are inventoried and remain protected, but no AWS
  resource is read, copied, or mutated.
- Measured durations and tested-fixture data loss are observations, not SLAs.
- Derived model/embedding/cache artifacts are verified and rebuildable, not
  archived as authority.

## Evidence and verdict

Evidence file:
`../evidence/release/step42-rc-backup-restore-validation.json`.

Evidence digest:
`e993398a2fa493969f4a91dce9a733f22694ec64c334581c910e7efb561a11fc`.

Closure verdict: `PASS_RC_BACKUP_RESTORE_CONTROLLED`; closure eligible. The
final commit remains `NOT CREATED` and push remains `NOT PERFORMED` in this
pre-commit record. Step43 remains NOT STARTED.
