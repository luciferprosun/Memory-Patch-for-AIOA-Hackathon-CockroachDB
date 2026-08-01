# Step 15 German Law temporal and jurisdictional normalization validation 1A

## Preconditions

Use only clean, synchronized `main` after the pushed Step-14 closure. Resolve the source root and Step-14 bundle from Step-14 evidence rather than hardcoding a local path. Verify the Step 8 external-volume adapter, Step-14 manifest digest, source-root identity, and fixed source-tree digest first. The corpus is logically immutable.

## Read-only plan

```bash
python3 scripts/run_german_law_temporal_jurisdiction_normalization.py \
  --plan \
  --source-root "$STEP15_SOURCE_ROOT" \
  --step14-bundle-root "$STEP14_BUNDLE_ROOT"
```

The plan must show zero source writes/deletes/moves, zero AWS/S3/network/model actions, zero publication and Step 16 work, the Step-14 input digest, the new root-relative Step-15 target, policy digest, and exact plan digest.

## Write or safe resume

```bash
python3 scripts/run_german_law_temporal_jurisdiction_normalization.py \
  --write-bundle \
  --source-root "$STEP15_SOURCE_ROOT" \
  --step14-bundle-root "$STEP14_BUNDLE_ROOT" \
  --confirm-device-reference "$STEP15_DEVICE_REFERENCE" \
  --confirm-source-root-identity "$STEP15_SOURCE_ROOT_IDENTITY" \
  --confirm-step14-manifest-digest "$STEP14_MANIFEST_DIGEST" \
  --confirm-plan-digest "$STEP15_PLAN_DIGEST"
```

The normalizer streams Step-14 records and only reads hash-bound metadata. It may resume its own incomplete checkpoint after re-verifying every immutable fact. Output publication is atomic and no-overwrite. Do not edit a checkpoint, remove a `.part`, or force a changed source to match. The command must end with zero source-tree writes, modifications, and deletions.

## Verify bundle

```bash
python3 scripts/run_german_law_temporal_jurisdiction_normalization.py \
  --verify-bundle \
  --source-root "$STEP15_SOURCE_ROOT" \
  --step14-bundle-root "$STEP14_BUNDLE_ROOT" \
  --confirm-device-reference "$STEP15_DEVICE_REFERENCE" \
  --confirm-source-root-identity "$STEP15_SOURCE_ROOT_IDENTITY" \
  --confirm-step14-manifest-digest "$STEP14_MANIFEST_DIGEST" \
  --confirm-plan-digest "$STEP15_PLAN_DIGEST"
```

Verification recomputes canonical manifest and generated-file length/SHA-256, rejects symlinks and `.part` residue, and does not trust manifest claims alone.

## Controlled CockroachDB validation

```bash
python3 scripts/run_german_law_temporal_jurisdiction_validation.py \
  --step14-bundle-root "$STEP14_BUNDLE_ROOT" \
  --step15-bundle-root "$STEP15_BUNDLE_ROOT" \
  --cockroach-binary "$COCKROACH_V26_2_4_BINARY" \
  --evidence-output docs/evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json \
  --confirm-device-reference "$STEP15_DEVICE_REFERENCE" \
  --confirm-step14-manifest-digest "$STEP14_MANIFEST_DIGEST" \
  --confirm-step15-manifest-digest "$STEP15_MANIFEST_DIGEST" \
  --confirm-source-tree-digest "$STEP15_SOURCE_TREE_DIGEST"
```

The runtime is unique, loopback-only, in-memory CockroachDB v26.2.4. It applies and replays existing migrations, proves review-only proposal semantics and Step-9 compatibility/replay/conflict behavior, verifies no `PUBLISHED` state and no runtime DELETE grant, then drops the database and drains the exact owned PID. Any force kill, remaining port, process, temporary store, or evidence mismatch fails validation.

## Failure handling

Preserve immutable inputs and external partial state. A source digest/tree mismatch, unsafe symlink, output collision, incompatible checkpoint, or non-graceful runtime cleanup is fail closed. Do not publish sources, rewrite dates, use current time to fill missing legal fields, delete corpus objects, or begin Step 16.
