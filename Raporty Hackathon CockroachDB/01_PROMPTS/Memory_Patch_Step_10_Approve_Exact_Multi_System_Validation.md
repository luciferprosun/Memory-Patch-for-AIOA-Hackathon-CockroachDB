# MEMORY PATCH STEP 10 — APPROVE EXACT MULTI-SYSTEM LIVE VALIDATION PLAN

I explicitly approve the exact displayed Step 10 multi-system write plan.

Approved plan identity:

- Repository baseline: `e93536626c105f5186ce7e2c89a419f5bf6c4b83`
- Working-files digest: `af798ac7c6a3964d783da0b367378aab5f0339da375f85d9ce48a21ed7fe0964`
- Plan digest: `db000d17600624d15401ed55f6f0b7a845b5c1fadb3d7318373863fa0accfc5e`
- AWS profile: `aoia-admin`
- AWS Region: `eu-central-1`
- Root principal: `NO`
- S3 bucket: `aioa-memory-patch-global-3f105fcd-eu-central-1`
- Payload SHA-256: `61088c464f21622d0dccd28d41e6f041c9bf7abf165542262c9ea7f8d51241ca`
- Payload length: `92`
- Retain until: `2026-08-30T07:39:23Z`
- External-volume reference: `external-volume-sha256:1bb3c26846f6aca5196a2980679f50fa8e1c241225c0e25971efcc998ecd102b`
- Saga ID: `ingsaga-75a5788b53b6697c9dbf574019c7cc42029fb3237a8255b58d12f21f928ed170`

Approved S3 object key:

```text
memory-patch/snapshots/v1/global/v1/a4/a41db356e0513b9529a04460b47097fdce17f13cc6fd5ee96064c0effec7f629/s3snap-96865bf2b400b16b5be6ba332d965168626dcf958c3ca494a1fb89c47be492c4.bin
```

Approved external-volume relative path:

```text
ingestion/downloads/step10-validation-s3snap-96865bf2b400b16b5be6ba332d965168626dcf958c3ca494a1fb89c47be492c4.json
```

This approval is valid only if all displayed values, paths, hashes, identities, the worktree digest, and the plan digest remain unchanged.

Before execution, verify without changing the plan that:

1. the temporary CockroachDB v26.2.4 binary still exists;
2. its exact pinned SHA-256 still matches the repository contract;
3. the repository working-files digest is still `af798ac7c6a3964d783da0b367378aab5f0339da375f85d9ce48a21ed7fe0964`;
4. the resolved plan digest is still `db000d17600624d15401ed55f6f0b7a845b5c1fadb3d7318373863fa0accfc5e`;
5. the target external-volume file remains absent;
6. the target S3 object key still has zero existing versions;
7. the AWS caller remains the approved non-root assumed-role SSO session;
8. the external-volume device reference still matches exactly;
9. no unrelated worktree change appeared.

If any value differs:

- do not execute the live validation;
- do not regenerate a replacement plan silently;
- return to the write gate with the new exact plan;
- preserve all current work.

If every value still matches, execute the exact command already prepared by the current session.

Ensure that terminal line wrapping does not insert whitespace or line breaks inside:

- `external-volume-sha256:1bb3c26846f6aca5196a2980679f50fa8e1c241225c0e25971efcc998ecd102b`;
- the complete S3 object key;
- the payload SHA-256;
- the plan digest;
- any filesystem path.

The approved execution is limited to:

- one fresh loopback-only, in-memory CockroachDB v26.2.4 runtime;
- the displayed isolated database `mp_step10_fc7e5384dfb28b0b_live`;
- one 92-byte external-volume validation artifact;
- one exact S3 Object-Locked version;
- the minimum synthetic Step 10 and Step 9 validation records;
- sanitized repository evidence.

It does not authorize:

- personal data;
- German-law data;
- model calls;
- Step 11 parsing, normalization, or chunking;
- new AWS infrastructure;
- new S3 buckets;
- IAM or permission-set changes;
- root AWS access;
- public access;
- retention bypass;
- S3 deletion;
- external-volume deletion or overwrite;
- persistent CockroachDB services;
- unrelated repository changes.

After execution, verify all nine milestones:

```text
REGISTERED
ACQUIRED_LOCAL
HASH_VERIFIED
SNAPSHOT_UPLOAD_PENDING
SNAPSHOT_UPLOADED
SNAPSHOT_LOCK_VERIFIED
PARSED
VALIDATED
PUBLISHED
```

Then verify:

1. the exact S3 version ID was returned and recorded;
2. Object Lock mode and retain-until metadata match the approved plan;
3. exact S3 read-back SHA-256 and byte length pass;
4. the external-volume read-back SHA-256 and byte length pass;
5. parser and validator receipts are explicitly synthetic Step 10 validation ports;
6. Step 9 publication transition is legal;
7. exact replay creates no additional external effects;
8. conflicting replay is rejected;
9. reconciliation recognizes the existing exact evidence;
10. no duplicate S3 version was created by replay;
11. no duplicate publication event was created;
12. no existing data was overwritten;
13. no existing data was deleted;
14. the disposable CockroachDB PID exits;
15. all owned CockroachDB ports close;
16. the owned temporary runtime directory is removed;
17. no persistent CockroachDB service or database remains.

If live validation fails:

- identify the first root cause;
- separate downstream failures;
- preserve all evidence;
- do not commit;
- do not push;
- return `FAILED VALIDATION — NOT COMMITTED` or `BLOCKED SAFELY — NO COMMIT`.

If live validation passes:

1. run the targeted Step 10 tests;
2. rerun Step 6, Step 7, Step 8, and Step 9 regressions;
3. rerun the full repository test suite;
4. rerun compile/import checks;
5. rerun migration validation;
6. rerun static, security, and secret checks;
7. run `git diff --check`;
8. inspect the complete diff;
9. confirm all changed files belong only to Step 10;
10. update the canonical roadmap and Step 10 closure documents;
11. commit exactly once with:

```text
feat(ingestion): add idempotent s3 cockroachdb saga 1a
```

12. push only to `origin/main`;
13. verify:

```bash
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
git status --short
git log -1 --oneline
git show --stat --oneline HEAD
```

Required final state:

```text
HEAD equals origin/main
ahead/behind equals 0 0
worktree clean
Step 7 remains COMPLETE
Step 8 remains COMPLETE
Step 9 remains COMPLETE
Step 10 is COMPLETE AND PUSHED
Step 11 remains NOT STARTED
```

Return the complete final Step 10 closure report.

Do not start Step 11.
