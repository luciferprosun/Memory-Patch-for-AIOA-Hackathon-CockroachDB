# Step 16 German Law HAT publication validation 1A

## Preconditions

Run only from a clean, synchronized `main` worktree after the Step 15 closure
is reachable from `origin/main`. Confirm the Step 14 and Step 15 manifests,
their generated-file digests, the external-volume identity, and the immutable
source-tree fingerprint before authorizing publication.

The expected inputs are resolved from the committed Step 14 and Step 15
evidence. Do not replace a missing bundle, regenerate a corpus, or point the
runner at a different mount. The source corpus is read-only by policy even if
the filesystem itself is writable.

## Read-only plan

Use the repository runner with the actual source and accepted bundle roots:

```bash
python3 scripts/run_german_law_publication.py --plan \
  --source-root '<approved-source-root>' \
  --step14-bundle-root '<accepted-step14-bundle>' \
  --step15-bundle-root '<accepted-step15-bundle>'
```

The plan verifies repository identity, Step 8 volume identity, Step 14/15
bundle integrity, source-root identity, source-tree fingerprint, deterministic
candidate count, and the planned run ID. It performs no AWS write, S3 write,
database write, source write, model call, download, or publication.

## Explicit write execution

The write invocation needs all confirmations copied exactly from the current
plan: device reference, source-root identity digest, Step 14 manifest digest,
Step 15 manifest digest, and plan digest.

```bash
python3 scripts/run_german_law_publication.py --write-publication \
  --source-root '<approved-source-root>' \
  --step14-bundle-root '<accepted-step14-bundle>' \
  --step15-bundle-root '<accepted-step15-bundle>' \
  --confirm-device-reference '<device-reference>' \
  --confirm-source-root-identity '<source-root-identity-digest>' \
  --confirm-step14-manifest-digest '<step14-manifest-digest>' \
  --confirm-step15-manifest-digest '<step15-manifest-digest>' \
  --confirm-plan-digest '<plan-digest>'
```

Before writes, the runner requires the approved temporary assumed-role AWS
identity, the pinned AWS CLI binary, the configured region and bucket,
versioning, Object Lock, governance retention, and the exact repository-owned
prefix. It uses no root identity, no credentials in arguments or evidence, no
public ACL, no delete, no retention bypass, and no bucket/IAM mutation.

For each eligible candidate it creates or exactly replays a raw ZIP and text
projection snapshot through the Step 7 adapter. The runner rechecks the typed
bucket capability receipt every 64 object writes. Each object still receives
a conditional no-overwrite put and exact version-specific verification.

## Resume and verification

If interrupted, rerun the same confirmed command. The run ID, captured time,
retention intent, checkpoints, and deterministic S3 identities are bound to
the fixed inputs. Successful object keys are reconciled as exact replays;
changed inputs or incompatible checkpoints fail closed.

After success, re-run bundle verification without S3 writes:

```bash
python3 scripts/run_german_law_publication.py --verify-bundle \
  --source-root '<approved-source-root>' \
  --step14-bundle-root '<accepted-step14-bundle>' \
  --step15-bundle-root '<accepted-step15-bundle>' \
  --confirm-device-reference '<device-reference>' \
  --confirm-source-root-identity '<source-root-identity-digest>' \
  --confirm-step14-manifest-digest '<step14-manifest-digest>' \
  --confirm-step15-manifest-digest '<step15-manifest-digest>' \
  --confirm-plan-digest '<plan-digest>'
```

The controlled CockroachDB validation is separate. It must use a loopback-only
disposable v26.2.4 node, a unique database and temporary store, applied and
replayed migrations, and graceful drain. Its pass condition includes process
exit, ports closed, temporary store removed, and `force_kill_used=false` (no
force kill).

## Operational limits and non-goals

The runner streams one candidate at a time and uses a bounded checkpoint batch
of 16. The full corpus run can take substantial time because every immutable
object is individually verified. Do not reduce verification, alter retention,
or sample the candidate set to shorten it.

Step 16 is not retrieval, temporal question resolution, answer generation,
embeddings, OCR, corpus download, Personal Memory work, or Step 17. A failed
or excluded candidate stays unpublished with a deterministic reason code.
