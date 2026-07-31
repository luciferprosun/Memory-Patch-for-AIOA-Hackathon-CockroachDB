# Step 12 HAT registry controlled validation 1A

Run only with the pinned CockroachDB v26.2.4 binary:

```bash
PYTHONPATH=src python3 scripts/run_hat_registry_validation.py \
  --write-validation \
  --cockroach-binary <verified-v26.2.4-binary>
```

The command creates one loopback-only, in-memory disposable database, applies
`0001–0009`, validates the two committed synthetic manifests, enables them
through trusted synthetic receipts and bindings, invokes fixed capabilities,
and exercises negative decisions. It performs zero AWS, S3, external-volume,
network, package, dynamic-import, model, or German-law operations.

Success requires exact migration replay, two enabled synthetic HATs, valid
event chains, rejection of unapproved/disabled/mismatched/private-memory
execution, graceful drain, no force kill, closed ports, removed temporary
store, and canonical evidence at
`docs/evidence/hats/step12-hat-registry-validation.json`.
