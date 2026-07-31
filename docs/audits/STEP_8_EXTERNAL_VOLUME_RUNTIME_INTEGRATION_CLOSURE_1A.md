# Memory Patch - Step 8 External Volume Runtime Integration Closure 1A

## Status

`IMPLEMENTATION AND LIVE VALIDATION COMPLETE`

This record belongs to the intended single Step 8 closure commit. It becomes
completion evidence only when that commit is reachable on `origin/main`.

## Interrupted-session recovery

The previous Step 8 session ended during a power loss. Recovery started from
read-only evidence rather than conversational memory.

- authorized starting commit:
  `2cb5e5dbe214b1c84cd0a951ad97cc08a4bb345f`;
- branch `main`, with `HEAD == origin/main` and ahead/behind `0 0`;
- exact expected repository and remote: PASS;
- real external repository filesystem, distinct from system root: PASS;
- interrupted merge, rebase, cherry-pick, revert, or bisect: NONE;
- recovery classification:
  `B - UNCOMMITTED STEP 8 WORK SURVIVED`;
- six modified and nine untracked files were all attributable to Step 8;
- no Step 8 local or pushed commit existed;
- no previous live Step 8 validation artifact or target-bound staging file
  existed.

Every surviving file was inspected. Complete implementation, tests, and
documentation were preserved. No reset, clean, stash, restore, checkout,
rebase, amend, or automatic artifact removal was performed.

## Runtime volume identity

The separately mounted runtime volume passed fresh verification before every
preflight and the approved write:

- explicit block-device mount, distinct from the system root filesystem;
- `ext4` filesystem and `mmc` transport matched private configuration;
- required `rw`, `nodev`, and `nosuid` mount posture;
- verified marker identity and prepared directory tree;
- conservative free-space reserve of 20 GiB;
- sanitized device reference:
  `external-volume-sha256:1bb3c26846f6aca5196a2980679f50fa8e1c241225c0e25971efcc998ecd102b`;
- marker SHA-256:
  `3cd367f4533d4cdd0fb8bcf2777bb39bb57a515ec5e83b0cd4739ce4923894f4`;
- no system-drive fallback.

Raw UUID, label, device source, mountpoint, username, serial number, and local
configuration are intentionally absent from tracked evidence.

## Adapter and fail-closed boundary

The typed runtime boundary provides:

- safe, non-executing parsing of the private configuration file;
- fresh mount, block-device, marker, tree, permission, and capacity checks;
- operation-specific roots and explicit fail-closed or disable-without-
  fallback behavior;
- canonical relative-path validation and prepared-parent enforcement;
- `lstat`, same-filesystem, `O_NOFOLLOW`, inode, and regular-file checks;
- bounded exact reads with length and SHA-256 verification;
- mode `0600`, `O_EXCL`, no-overwrite atomic staging;
- file and directory `fsync`, exact staged and published read-back;
- narrow detection and preservation of power-loss staging artifacts;
- sanitized storage-only status and write evidence.

The adapter never creates directories and exposes no durable-file delete,
overwrite, internal-disk fallback, credential, database, publication,
approval, commit, or execution operation.

## Approved live validation

The operator explicitly approved the displayed fixed plan after recovery,
offline validation, security review, and a same-session read-only preflight.
Immediately before the write:

- exact target state: `ABSENT`;
- target-bound incomplete staging artifacts: `0`;
- fixed payload length: 88 bytes;
- fixed payload SHA-256:
  `d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc`.

Exactly one `APPLICATION_SNAPSHOT_STAGING` write was performed at:

```text
snapshots/application/step8-validation-d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc.json
```

The result was `LIVE_VALIDATION_PASS`:

- atomic create without replacement: PASS;
- file `fsync`: PASS;
- directory `fsync`: PASS;
- exact published read-back: PASS;
- content length 88: PASS;
- SHA-256 equality: PASS;
- Step 7 fixture byte equality: PASS;
- storage class `EXTERNAL_DERIVED`: PASS;
- authority status `STORAGE_EVIDENCE_ONLY`: PASS;
- system-drive fallback: FALSE.

Independent read-only verification found one regular mode `0600` file with
one link, exact bytes and hash, no duplicate Step 8 snapshot, and no incomplete
staging artifact. The validation artifact is intentionally retained as
derived-storage evidence and was not deleted.

The sanitized structured record is
[`step8-external-volume-validation.json`](../evidence/external-volume/step8-external-volume-validation.json).

## Offline, regression, and security validation

- compileall for `src`, `tests`, and `scripts`: PASS;
- Step 8 targeted tests: `46/46`;
- Step 7 storage and documentation regressions: `71/71`;
- Step 9 source-registry and persistence regressions: `130/130`;
- related regressions total: `201/201`;
- final full repository suite: `633/633`;
- repository contract validator: PASS;
- local toolchain verifier: PASS;
- external-volume read-only preflight: PASS;
- diff whitespace validation: PASS;
- secret and local-identity scan: PASS.

Security review confirmed:

- no committed raw device identity, mountpoint, username, or secret;
- no shell execution in the Linux probe or validation command;
- no overwrite, rename, broad cleanup, or system-drive fallback path;
- no symlink or special-file acceptance;
- no unbounded read or write;
- no public delete API;
- no storage evidence converted into semantic authority.

## Scope and historical preservation

Step 7 and Step 9 implementation, closure records, migrations, evidence, and
commits remain unchanged. The historical Step 7 and Step 8 deferral record
also remains unchanged because it accurately describes the earlier decision.

No AWS call, CockroachDB runtime, Step 10 ingestion state, HAT runtime, demo
UI, AOIA-Core component, EC2 migration, AgentCore resource, ECS resource,
model-provider dependency, or real source content was introduced.

## Roadmap status

```text
Step 7: COMPLETE
Step 8: COMPLETE AND PUSHED at actual closure commit
Step 9: COMPLETE
Step 10: NOT STARTED
```

`Step 8: COMPLETE AND PUSHED` becomes true only when this intended closure
commit is reachable on `origin/main`. Step 8 completion does not authorize or
start Step 10.
