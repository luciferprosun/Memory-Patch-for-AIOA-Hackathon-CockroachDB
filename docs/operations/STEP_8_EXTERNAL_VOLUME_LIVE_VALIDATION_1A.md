# Step 8 External Volume Live Validation 1A

## Purpose

This runbook validates only the Step 8 external-volume runtime boundary. It
does not mount, unmount, format, repair, migrate, delete, overwrite, call AWS,
start CockroachDB, or run Step 10.

## Private configuration

Copy and protect the generic example if the ignored local file is absent:

```bash
cp config/external-data.env.example .local/external-data.env
chmod 600 .local/external-data.env
```

Fill only verified machine-local identity values. Do not commit the local
mountpoint, UUID, label, device source, or local paths.

## Read-only preflight

Run:

```bash
python3 scripts/run_external_volume_validation.py --preflight
```

Required result:

```text
PREFLIGHT_PASS_NO_WRITE
```

Review the sanitized device reference, marker hash, filesystem, transport,
capacity reserve, write-capability result, exact target state, incomplete
staging list, fixed byte length, and SHA-256.

Do not proceed if the target is ambiguous, mismatched, a symlink or special
file, or if an incomplete atomic staging artifact is reported.

## Mandatory operator gate

Before the first live write, display and approve the exact plan. The fixed
operation is:

```text
operation: APPLICATION_SNAPSHOT_STAGING
relative target:
  snapshots/application/
  step8-validation-d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc.json
content length: 88 bytes
SHA-256:
  d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc
write behavior: atomic create, no overwrite, file and directory fsync
verification: exact read-back length and SHA-256
fallback: forbidden
```

The approval must cover that exact write. Repository implementation work or
read-only preflight is not approval.

## Approved live command

Use the project ID and the sanitized device reference from the same successful
preflight:

```bash
python3 scripts/run_external_volume_validation.py \
  --write-validation \
  --confirm-project memory-patch-for-aioa \
  --confirm-device-reference "external-volume-sha256:reviewed-value"
```

Required new-write result:

```text
LIVE_VALIDATION_PASS
```

If the exact final target already exists and exact read-back succeeds, the
script returns:

```text
ALREADY_VALID_NO_WRITE
```

It does not create a duplicate.

## Evidence handling

Commit only sanitized evidence: hashes, byte length, filesystem type,
transport class, capacity policy, verification booleans, operation, relative
path, storage class, and authority status.

Never commit the raw UUID, label, device path, mountpoint, username, serial
number, broad volume listing, personal file name, credential, or secret.

## Completed validation

The recovered Step 8 session passed the read-only preflight, received explicit
approval for the exact fixed plan, and performed one new write. The result was
`LIVE_VALIDATION_PASS`, followed by an independent exact read-back. The final
target is one regular mode `0600` file with the expected 88 bytes and SHA-256;
no duplicate Step 8 snapshot or incomplete staging artifact exists.

Only the sanitized result is tracked:

- [`step8-external-volume-validation.json`](../evidence/external-volume/step8-external-volume-validation.json);
- [`STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md`](../audits/STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md).
