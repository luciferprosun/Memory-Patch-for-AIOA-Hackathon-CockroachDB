# Step 11 Parsing Pipeline Live Validation 1A

## Scope

This runbook validates the real Step 11 JSON parser and structural validator
against the exact existing Step 10 snapshot. It creates no new S3 version and
no new external-volume artifact. Its only durable output is one exclusive,
sanitized repository evidence file. CockroachDB data exists only in an owned,
loopback-only, in-memory v26.2.4 runtime and is removed after graceful drain.

## Fixed existing evidence

- payload length: `92` bytes;
- payload SHA-256:
  `61088c464f21622d0dccd28d41e6f041c9bf7abf165542262c9ea7f8d51241ca`;
- external relative path:
  `ingestion/downloads/step10-validation-s3snap-96865bf2b400b16b5be6ba332d965168626dcf958c3ca494a1fb89c47be492c4.json`;
- S3 bucket: `aioa-memory-patch-global-3f105fcd-eu-central-1`;
- S3 key:
  `memory-patch/snapshots/v1/global/v1/a4/a41db356e0513b9529a04460b47097fdce17f13cc6fd5ee96064c0effec7f629/s3snap-96865bf2b400b16b5be6ba332d965168626dcf958c3ca494a1fb89c47be492c4.bin`;
- exact version ID: `kfDFfBsGlAR_KoQxDodzESlhebuYpAMx`;
- Object Lock: `GOVERNANCE` through `2026-08-30T07:39:23Z`;
- expected JSON:
  `{"kind":"memory-patch-step10-synthetic","schema_version":"1.0.0","value":"validation-only"}`.

The machine-specific mount path, raw device UUID, AWS account ID, role session
ID, credentials, and SSO cache are not public evidence.

## Prerequisites

1. Repository guard is the exact Step 10 closure baseline and intended Step 11
   worktree.
2. AWS CLI profile is `aoia-admin`, Region is `eu-central-1`, and the caller is
   a non-root temporary assumed SSO role.
3. The Step 8 no-write preflight proves the expected external-volume identity,
   no root-filesystem fallback, and the exact regular-file payload.
4. Read-only S3 checks prove exactly one version, exact bytes and metadata,
   `GOVERNANCE` retention, and no delete marker.
5. The pinned CockroachDB binary reports v26.2.4 and SHA-256
   `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`.
6. Migration offline validation and the full offline test suite pass.
7. Success and failure evidence outputs do not exist and are distinct.

If SSO is expired, authenticate only with:

```bash
aws sso login --profile aoia-admin
```

Do not switch profiles, use root, create keys, or broaden permissions.

## Offline validation

```bash
python3 -m compileall src tests scripts
python3 scripts/run_cockroachdb_migrations.py --offline-validate
PYTHONPATH=src:scripts python3 -m unittest discover -s tests
git diff --check
```

The disposable migration validation command is:

```bash
python3 scripts/run_cockroachdb_migrations.py \
  --live-test \
  --allow-live \
  --cockroach-binary <exact-v26.2.4-binary> \
  --timeout 120
```

It applies migrations `0001` through `0008`, verifies checksum replay,
catalog, RLS, FORCE RLS, grants, and exact graceful cleanup. It performs no AWS
or external-volume write.

The Step 11 harness permits at most 300 seconds for each migration command on
the constrained validation host. This is a hard upper bound rather than an
unbounded retry: the shared migration runner rejects larger values, while
ordinary commands retain their narrower defaults. The bound was raised from
180 seconds only after the first live attempt proved that migration `0007`
could exceed the former limit on this hardware.

Interactive CockroachDB transactions in the Step 11 live harness use a
separate 180-second bound. The first parse-integration attempt reached the
application phase but exceeded the earlier 90-second validation transport
bound. Driver failures retain only a strictly validated uppercase reason code;
raw database diagnostics and SQL text never enter committed evidence.

## Plan command

Use the exact existing capture and retention timestamps:

```bash
python3 scripts/run_parsing_pipeline_validation.py \
  --plan \
  --cockroach-binary <exact-v26.2.4-binary> \
  --aws-binary <resolved-aws-cli-binary> \
  --captured-at 2026-07-31T07:39:23Z \
  --retain-until 2026-08-30T07:39:23Z \
  --evidence-output \
    docs/evidence/parsing/step11-parsing-pipeline-validation.json
```

Planning is read-only for S3 and the external volume. It verifies repository,
binary, caller, mount, exact external bytes, exact S3 version, version count,
delete markers, metadata, checksum, and retention. It prints:

- current worktree digest;
- redacted AWS identity context;
- exact existing S3 and external evidence;
- parser, normalization, chunking, ruleset, and migration profiles;
- disposable database and synthetic scope identities;
- expected row counts;
- explicit zero-write and zero-delete assertions;
- exclusive evidence output;
- canonical plan digest;
- exact execution argument vector.

The plan fails closed if any evidence differs or if the success evidence path
already exists.

## Zero-external-write gate policy

No additional human approval is required only when the printed plan proves:

```text
new S3 writes: 0
new external-volume writes: 0
deletions: 0
retention changes: 0
CockroachDB: disposable loopback-only in-memory runtime
repository: one exclusive sanitized evidence file
```

If any external write is required, do not run the command. Display a new exact
human approval gate instead. Never generate a new key, version, payload, path,
or retention timestamp silently.

## Exact execution

Run the `exact_command_argv` emitted by the same plan. It must contain:

```text
--validate-existing
--confirm-project
--confirm-device-reference
--confirm-external-relative-path
--confirm-payload-sha256
--confirm-bucket
--confirm-object-key
--confirm-version-id
--confirm-plan-digest
```

The harness repeats all preflights before checking confirmations. A changed
repository, worktree, mount, role, object version, retention, or evidence
target changes the plan digest and stops execution.

## Expected disposable flow

1. Start one owned CockroachDB v26.2.4 process on dynamic loopback ports with
   a 640 MiB in-memory store and external I/O disabled.
2. Apply migrations `0001` through `0008`; verify a checksum no-op replay.
3. Seed only the isolated base identities and exact knowledge-version
   reservation needed by the existing Step 10 saga foreign keys.
4. Register a fresh synthetic source through Step 9.
5. Reconcile the exact existing external artifact and exact S3 version through
   Step 10 read-only wrappers.
6. Execute the real `application/json` Step 11 parser.
7. Atomically persist one parsed document, three JSON sections, one knowledge
   version, three chunks, and zero expected findings.
8. Return a real ParseReceipt with `synthetic_validation_boundary=false`.
9. Run the real structural validator and return a real ValidationReceipt with
   `synthetic_validation_boundary=false`.
10. Reach PARSED, VALIDATED, and PUBLISHED through Step 10 and Step 9.
11. Verify exact replay and reject one conflicting replay.
12. Recheck S3 version count, delete markers, external bytes, and zero write
    counters.
13. Drop the disposable database and role, drain the exact owned node, verify
    its PID and ports exited, and remove only the owned temporary directory.
14. Write and read back one canonical sanitized evidence JSON exclusively.

## Required success invariants

- final saga milestone is `PUBLISHED` with eight transition events;
- publication has three Step 9 events and no direct SQL bypass;
- parser and validator synthetic markers are false;
- parsed document, section, chunk, finding, range, and receipt digests match;
- exact replay adds no persistent or external effect;
- conflicting replay returns `IDEMPOTENCY_BINDING_CONFLICT`;
- S3 version ID and count remain unchanged at one;
- S3 PutObject attempts and calls are zero;
- external-volume write attempts and calls are zero;
- delete markers, deletions, and retention changes are zero;
- `chunk_search_documents` remains empty;
- graceful node drain completes within its derived bound;
- force kill is false;
- all owned children, PID, ports, and temporary data are gone;
- no persistent CockroachDB service or data is created.

## Failure and recovery

The lifecycle always captures the primary validation outcome, runs cleanup
exactly once, combines cleanup failures, writes a separate exclusive canonical
failure evidence file, verifies its digest, and then returns failure. Cleanup
failure cannot be reported as success. Force kill is emergency exact-PID
cleanup only and always fails closure.

Do not overwrite either evidence file. Preserve any failed-attempt evidence.
After a Step 11-owned repair, repeat offline checks and generate a new exact
zero-write plan. If the existing S3 or external evidence differs, stop without
replacement, overwrite, deletion, or retention change.

## Deferred boundaries

The validation performs no Markdown, PDF, OCR, Office, archive, HTML, URL,
model, HAT, retrieval, embedding, vector-index, German-law, or Step 12 work.
