# Step 14 German Law corpus inventory validation 1A

## Preconditions

Use `main` with `HEAD == origin/main`, no interrupted Git operation, and only
intended Step 14 changes. Verify the external volume with the repository Step 8
adapter. The operator supplies the corpus root through `STEP14_SOURCE_ROOT`;
the path is never placed in portable evidence.

The scanner requires:

- approved external-volume identity and marker;
- source and derived root on the same verified filesystem;
- no system-drive fallback or mount-boundary crossing;
- source root that is a real directory, not a symlink;
- an absent run target or an exactly matching resumable checkpoint;
- an explicitly confirmed device, source-root identity, and plan digest.

## Plan and write gate

```bash
python3 scripts/run_german_law_corpus_inventory.py \
  --plan \
  --source-root "$STEP14_SOURCE_ROOT"
```

The plan prints the device reference, source and worktree identities, object
and byte counts, exact output names, no-overwrite/atomic policy, and the plan
digest. Before the first external derived write, compare every displayed fact
with the approved gate. A changed fact requires a new plan.

## Execute or resume

```bash
python3 scripts/run_german_law_corpus_inventory.py \
  --write-bundle \
  --source-root "$STEP14_SOURCE_ROOT" \
  --confirm-device-reference "$STEP14_DEVICE_REFERENCE" \
  --confirm-source-root-identity "$STEP14_SOURCE_ROOT_IDENTITY" \
  --confirm-plan-digest "$STEP14_PLAN_DIGEST"
```

An interrupted run resumes only after rechecking the source snapshot, policy,
device, worktree, and completed object identities. Never remove or edit a
checkpoint to make it pass. The command must end with zero source-tree writes,
modifications, and deletions.

## Verify bundle

```bash
python3 scripts/run_german_law_corpus_inventory.py \
  --verify-bundle \
  --source-root "$STEP14_SOURCE_ROOT" \
  --confirm-device-reference "$STEP14_DEVICE_REFERENCE" \
  --confirm-source-root-identity "$STEP14_SOURCE_ROOT_IDENTITY" \
  --confirm-plan-digest "$STEP14_PLAN_DIGEST"
```

Verification recomputes every generated-file length and SHA-256, checks the
canonical manifest digest, rejects `.part` residue, and does not trust manifest
claims without read-back.

## Controlled registration validation

Use the exact pinned CockroachDB v26.2.4 binary and the completed bundle:

```bash
python3 scripts/run_german_law_corpus_registration_validation.py \
  --bundle-root "$STEP14_BUNDLE_ROOT" \
  --cockroach-binary "$COCKROACH_V26_2_4_BINARY" \
  --evidence-output \
    docs/evidence/corpus/step14-german-law-corpus-inventory-summary.json \
  --confirm-device-reference "$STEP14_DEVICE_REFERENCE" \
  --confirm-manifest-digest "$STEP14_MANIFEST_DIGEST" \
  --confirm-source-tree-digest "$STEP14_SOURCE_TREE_DIGEST" \
  --confirm-candidate-count "$STEP14_CANDIDATE_COUNT" \
  --confirm-declared-provision-count "$STEP14_DECLARED_PROVISION_COUNT"
```

The runtime is loopback-only and in-memory. The command applies and replays
migrations `0001` through `0009`, registers every ready candidate, performs an
exact replay, rejects a digest conflict, exercises synthetic review and
quarantine transitions, verifies cross-tenant isolation and zero publication,
drops the database, and performs the exact bounded drain. Any force kill,
remaining port, remaining owned directory, published source, or evidence
mismatch is a validation failure.

## Failure handling

Preserve the first reason separately from cleanup facts. Do not rewrite the
source, delete the bundle, weaken a digest, or force completion. Exact-PID
emergency cleanup may prevent an orphan but cannot satisfy closure. Do not
commit until the full repository suite, security scan, bundle/evidence digest
verification, source-tree post-check, and graceful cleanup all pass.
