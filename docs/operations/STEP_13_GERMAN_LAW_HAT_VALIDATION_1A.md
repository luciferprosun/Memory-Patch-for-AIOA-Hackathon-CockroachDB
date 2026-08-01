# Step 13 controlled validation 1A

The validation performs only local manifest/fixture reads, disposable
loopback CockroachDB writes, and a sanitized repository evidence write.

```text
AWS writes: 0
S3 writes: 0
external-volume writes: 0
corpus reads: 0
corpus writes: 0
paid-source access: 0
model calls: 0
persistent database: 0
Step 14 work: 0
```

Run with the verified CockroachDB v26.2.4 binary:

```bash
PYTHONPATH=src python3 scripts/run_german_law_hat_validation.py \
  --write-validation \
  --cockroach-binary <verified-v26.2.4-binary>
```

The script applies and replays migrations `0001–0009`, enables the trusted
manifest, exercises federal/state/EU requests and all four capabilities,
records authority distinctions and negative probes, then drops the isolated
database and gracefully drains the exact owned runtime. Force kill fails the
validation.
