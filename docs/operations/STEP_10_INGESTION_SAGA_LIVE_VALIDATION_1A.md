# Step 10 ingestion saga live validation 1A

## Scope

This runbook controls one synthetic Step 10 validation across:

- one fresh disposable CockroachDB v26.2.4 runtime;
- the verified Step 8 `INGESTION_STAGING` boundary;
- the existing Step 7 Object Lock bucket;
- the existing Step 9 source and publication service.

It does not ingest personal data, German-law data, internet content, or model
output. It does not implement Step 11.

## Safety properties

- The repository baseline, branch, origin, and interrupted Git markers are
  checked before planning and again before execution.
- AWS commands use profile `aoia-admin`, Region `eu-central-1`, and
  `--no-cli-pager`.
- The caller must be an assumed-role SSO session in the `LuciferSOL`
  permission context. Root and IAM-user identities fail closed.
- The external target is a deterministic relative file under
  `INGESTION_STAGING`.
- The external adapter permits no system-drive fallback and no overwrite.
- The S3 key is deterministic and must have zero versions before the approved
  write.
- The S3 adapter creates at most one conditional Object-Locked version.
- No S3 delete, bucket mutation, public access, or retention bypass is
  available.
- CockroachDB binds SQL, RPC, and HTTP only to dynamic `127.0.0.1` ports.
- The CockroachDB store is in memory and external I/O is disabled.
- The exact owned PID, ports, role, database, and temporary directory are
  cleaned up.

## Pinned tools

The CockroachDB server must report `v26.2.4` and have SHA-256:

```text
a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf
```

The official archive SHA-256 is:

```text
3c7de055c07f9101eb0f71b3f5e6b489b0fcf449d3d5a55bfe61eff4f935ce8f
```

The validation harness requires explicit paths to the verified CockroachDB
binary and the resolved regular AWS CLI executable. The exact AWS CLI file
SHA-256 is included in the plan and checked again before every AWS request.
The harness does not install either tool.

## Offline validation

Run from the authorized repository:

```bash
python3 -m compileall src tests scripts

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts \
  python3 -m unittest \
    tests.test_ingestion_saga \
    tests.test_ingestion_saga_validation \
    tests.test_cockroach_cli_dbapi \
    tests.test_aws_cli_s3_client

python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 -m unittest discover -s tests -v
python3 scripts/validate_contracts.py
git diff --check
```

The repository-native Step 6 through Step 9 regression suites must also pass.

## Disposable database integration

Before any S3 or external-volume target write, run one isolated integration
validation with in-memory fakes for both storage backends. It must prove:

- migrations `0001` through `0007` apply from zero;
- checksum replay applies zero migrations and skips seven;
- the actual CockroachDB repository reaches `PUBLISHED`;
- eight saga events and seven external effect records are durable;
- three Step 9 publication events are durable;
- exact replay changes no counts and invokes no new fake effect;
- a conflicting idempotency replay returns
  `IDEMPOTENCY_BINDING_CONFLICT`;
- the exact PID exits without force kill;
- all three loopback ports close;
- the owned temporary directory is removed.

This integration is allowed before the final gate because all external
backends are deterministic fakes and the database is disposable.

## Read-only preflights

### AWS

Re-authenticate only if needed:

```bash
aws sso login --profile aoia-admin
```

Verify the caller and the existing bucket without writing:

```bash
aws sts get-caller-identity \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws s3api head-bucket \
  --bucket aioa-memory-patch-global-3f105fcd-eu-central-1 \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws s3api get-bucket-versioning \
  --bucket aioa-memory-patch-global-3f105fcd-eu-central-1 \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws s3api get-object-lock-configuration \
  --bucket aioa-memory-patch-global-3f105fcd-eu-central-1 \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager
```

Required results are an assumed-role SSO caller, non-root identity, enabled
versioning, enabled Object Lock, and GOVERNANCE default retention.

### External volume

```bash
python3 scripts/run_external_volume_validation.py --preflight
```

The Step 10 plan additionally checks only its exact proposed target and its
target-bound incomplete atomic names. It does not scan unrelated volume
content.

### CockroachDB

The plan verifies the exact binary identity. The final approved execution
starts a fresh disposable runtime and repeats migration, schema, Step 9
security, and Step 10 security validation before the first multi-system
effect.

## Create the exact plan

Select whole-second UTC capture and retain-until timestamps. The retain-until
time must remain safely beyond the seven-day bucket policy for the complete
approval and execution window.

Run:

```bash
python3 scripts/run_ingestion_saga_validation.py \
  --plan \
  --cockroach-binary <exact-v26.2.4-binary> \
  --aws-binary <resolved-aws-cli-binary> \
  --captured-at <YYYY-MM-DDTHH:MM:SSZ> \
  --retain-until <YYYY-MM-DDTHH:MM:SSZ> \
  --evidence-output \
    docs/evidence/ingestion/step10-ingestion-saga-validation.json
```

Planning performs only read-only AWS, S3, and external-volume checks. It
prints:

- repository and redacted caller identity;
- exact S3 key, payload identity, lock mode, and retention;
- sanitized external device reference and exact relative target;
- CockroachDB version, mode, database, and role;
- synthetic tenant, user, source, snapshot, and saga identifiers;
- expected table row counts;
- replay, conflict, and reconciliation behavior;
- retained artifacts and cost implications;
- a canonical plan digest;
- the exact execution argument vector.

The plan fails if the external target exists, a target-bound incomplete
artifact exists, the S3 key already has a version, or the evidence file
already exists.

## Mandatory gate

Do not run `--write-validation` until the complete printed plan has explicit
human approval. Approval must bind the project, sanitized device reference,
object key, payload digest, timestamps, and plan digest.

The gate text is:

```text
MULTI-SYSTEM WRITE GATE — STEP 10

Step 10 implementation, migrations, offline tests, security review, and
disposable CockroachDB integration validation are complete.

The next operation will execute only the displayed synthetic Step 10
ingestion-saga validation across one fresh loopback-only disposable
CockroachDB v26.2.4 runtime, the verified external volume, and the existing
Step 7 S3 Object Lock bucket.

The CockroachDB runtime uses an in-memory store and will be removed after
validation. No production or persistent CockroachDB service will be created.

No personal data, German-law data, model calls, public access, retention
bypass, destructive cleanup, or unrelated resources will be used.

No final Step 10 multi-system write has been performed yet.

Explicitly approve or reject the exact displayed plan.
```

## Approved execution

Copy the exact `exact_command_argv` from the approved plan. Do not reconstruct
or shorten it. The command includes all confirmations:

```text
--confirm-project
--confirm-device-reference
--confirm-object-key
--confirm-payload-sha256
--confirm-plan-digest
```

The harness repeats all preflights before accepting those confirmations. A
changed mount, identity, key state, repository baseline, Step 10 worktree
digest, AWS role, or evidence target invalidates the approved plan.

## Expected live effects

The approved validation creates only:

- seven synthetic base rows in the disposable database;
- one Step 9 registry row;
- one saga row;
- eight append-only saga events;
- seven external effect rows;
- five Step 6 persistence-operation rows;
- three Step 9 publication events;
- one 92-byte external staging artifact;
- one 92-byte S3 Object-Locked version plus metadata;
- one sanitized repository evidence JSON after all cleanup succeeds.

No existing item is overwritten or deleted.

## Success checks

The run must prove:

- final milestone `PUBLISHED`;
- exact event-chain verification;
- exact payload hash and length on the external volume;
- exactly one external write;
- exactly one S3 `put-object`;
- exact S3 version ID;
- exact-version byte read-back;
- verified metadata and Object Lock retention;
- one parser and one validator synthetic receipt;
- exactly three Step 9 publication events;
- exact replay changes no database count and makes no new external write;
- conflicting replay fails before external work;
- read-only reconciliation recognizes the same local artifact and S3 version;
- no orphan conflict;
- no overwrite, delete, public access, or retention bypass;
- database and role removed;
- owned PID exited without force kill;
- ports closed;
- temporary directory removed.

## Failure and recovery

On failure:

1. report the first structured error code;
2. write one exclusive sanitized failure-evidence JSON containing the plan
   digest, deterministic target identities, observed write-attempt counts, and
   exact owned-runtime cleanup result;
3. do not delete an external staging artifact;
4. do not delete or overwrite an S3 version;
5. inspect the durable intent and exact external evidence;
6. reconcile before any retry;
7. do not commit or push a failed closure.

An S3 version retained by Object Lock remains evidence even if a later
database operation fails. An external artifact remains derived staging
evidence and grants no publication authority.

### First live attempt and proven cleanup defect

The approved first live attempt reached `PUBLISHED`, created the one approved
external artifact and one approved Object-Locked S3 version, and passed exact
read-back, replay, conflict, and reconciliation checks. Closure still failed
because the old cleanup path waited a fixed 20 seconds after SIGTERM and then
used exact-PID SIGKILL. The failed-attempt evidence is preserved separately
and Step 10 remained uncommitted.

The live server exposed these shutdown settings:

| Setting | Value |
| --- | ---: |
| `server.shutdown.connections.timeout` | `0s` |
| `server.shutdown.initial_wait` | `0s` |
| `server.shutdown.jobs.timeout` | `10s` |
| `server.shutdown.lease_transfer_iteration.timeout` | `5s` |
| `server.shutdown.transactions.timeout` | `10s` |

The bounded server phases total 25 seconds, so the previous 20-second process
wait was shorter than the server's configured graceful lifecycle. The repair
derives `ceil(sum(server.shutdown.*)) + 15 seconds`, producing 40 seconds for
the observed v26.2.4 settings, with a test-only hard cap of 120 seconds.

Before shutdown, every validation-owned transaction, connection, cursor,
interactive SQL child, pipe, and writable evidence handle is closed or
reaped. The harness then:

1. resolves and verifies the exact owned node and RPC/SQL bindings;
2. runs `cockroach node drain --self` against the owned RPC loopback port with
   `--shutdown`, the derived `--drain-wait`, and no broad process target;
3. requires both command success and the bounded completion marker;
4. waits for the recorded server PID to exit within the derived bound;
5. uses exact-PID SIGTERM only as a failed drain fallback;
6. treats exact-PID SIGKILL only as failed emergency cleanup;
7. verifies the pinned binary's controlled exit outcome, closed ports, reaped
   children, and removal of only the owned temporary directory.

For `node drain --shutdown`, the v26.2.4 `start-single-node` parent reports
exit code 1 together with the terminal diagnostic `shutdown requested by drain
RPC`, even after logging `server drained and shutdown completed`. The harness
accepts that narrowly proven pair as the pinned binary's controlled remote
shutdown outcome. Any other non-zero exit, missing completion marker, panic,
SIGKILL, or unmatched log evidence fails closure.

Failure evidence is now finalized only after cleanup. Primary validation and
cleanup results are combined, the first root cause remains explicit, a
canonical sanitized success or failure file is written and read back, its
digest is verified, and only then does the command return or raise its final
verdict. Success and failure output paths must be distinct.

### Zero-external-write recovery validation

`--recovery-plan` verifies the preserved failed-attempt evidence, the exact
existing external artifact, and the exact existing S3 version read-only. Its
plan must state all of the following:

```text
new S3 writes: 0
new external-volume writes: 0
deletions: 0
retention changes: 0
CockroachDB writes: disposable in-memory validation scope only
repository writes: one exclusive sanitized success or failure evidence file
```

`--recovery-validation` requires exact confirmations for the project, device
reference, external relative path, payload digest, bucket, key, version ID,
retain-until timestamp, preserved failure-evidence digest, and recovery-plan
digest. The recovery wrappers make both `PutObject` and external atomic write
fail closed. They also count attempted calls, so a forbidden call cannot be
hidden as zero successful writes.

The recovery creates only fresh ephemeral database rows. The saga reconciles
the existing external bytes and exact S3 version into durable intents and
receipts, reaches all nine milestones, verifies exact replay and conflicting
replay, and then exercises the repaired graceful shutdown. If exact existing
evidence cannot be reconciled, the command stops rather than selecting a new
path, object key, version, payload, timestamp, or retention plan.

## Cleanup

The harness drops only its exact disposable database and synthetic login role,
drains the verified owned node, terminates only its recorded CockroachDB child
PID, verifies its three ports are closed, and removes only its owned temporary
runtime directory.

It never runs broad process-kill commands. An exact-PID force kill is emergency
orphan prevention only and always fails closure. The harness does not remove
the S3 version or the approved external evidence artifact.

## Closure boundary

The approved first attempt is preserved as failed cleanup evidence. The
zero-external-write recovery then passed all saga, replay, reconciliation, and
graceful-cleanup checks, followed by all post-validation regressions. The
canonical roadmap, documentation index, and
[`Step 10 closure record`](../audits/STEP_10_IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_CLOSURE_1A.md)
therefore record the intended closure commit. Step 11 remains not started.
