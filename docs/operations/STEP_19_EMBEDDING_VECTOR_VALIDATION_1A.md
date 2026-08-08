# Step 19 Embedding and Vector Validation 1A

## Purpose

This runbook reproduces the bounded Step 19 unit, migration, local-model,
external-cache, and disposable CockroachDB validation. It must be run from a
clean repository checkout. It performs no AWS, S3, provider, or remote
inference operation.

## Prerequisites

- Python 3.12 and the repository's existing tooling;
- the pinned CockroachDB v26.2.4 binary already prepared by the repository;
- a verified Step 8 external data volume;
- ignored `.local/external-data.env` containing machine-local volume identity;
- network only for the first immutable Hugging Face revision bootstrap and Git
  push;
- enough external-volume free space for the isolated runtime and model cache.

Do not commit the environment file, absolute mount path, model files, virtual
environment, Hugging Face cache, embedding cache, or machine-local model
installation manifest.

## Offline tests

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step19_embedding_vector -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
python3 -m compileall -q src scripts tests
```

Ordinary tests use a deterministic fake backend and require neither Torch nor
the model cache.

## Controlled model and database validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step19_embedding_vector_validation.py
```

On first run the validator:

1. verifies the external volume and reserve;
2. builds an ignored exact-version Python runtime below external cache;
3. downloads only the pinned Hugging Face revision if absent;
4. verifies required regular files and exact safetensors SHA-256;
5. disables network access for model loading and inference;
6. proves dimension, finite values, normalization, prefixes, repeat inference,
   and a bounded German semantic fixture;
7. starts one owned loopback-only CockroachDB v26.2.4 process with an in-memory
   disposable store;
8. enables the already-proven vector-index capability on that owned node;
9. applies all migrations and proves replay is a no-op;
10. seeds a tiny trusted Step 16 fixture plus hard-filter negatives;
11. proves cache replay, embedding persistence/idempotency, vector retrieval,
    index inventory/EXPLAIN, and ordinary-role RLS isolation;
12. stops the exact owned PID, verifies owned ports are closed, and removes the
    in-memory runtime directory.

Subsequent runs verify and reuse the immutable model and passage cache. They do
not re-download valid assets. A hash, file-type, revision, runtime, or external
volume mismatch fails closed.

## Expected capability matrix

- exact model/revision/weight identity: PASS;
- offline reload and local-only inference: PASS;
- dimension 384 and unit normalization: PASS;
- E5 query/passage policy: PASS;
- verified external cache and replay: PASS;
- migration 0010 and replay: PASS;
- `VECTOR(384)`, L2 index, and model lookup index: PASS;
- RLS and FORCE RLS: PASS;
- bounded generation, persistence, and vector query: PASS;
- tenant, HAT, owner, publication, authority, and model isolation: PASS;
- Step 20 hybrid/ranking/final bundle implementation: absent.

## Failure and cleanup semantics

Errors are reported as stable sanitized codes; SQL, credentials, and absolute
machine cache paths are not placed in committed evidence. The validation node
uses a unique owned prefix, loopback ports, and an in-memory store. Cleanup
targets only the exact child PID and exact runtime directory. It does not kill
unrelated processes and does not delete the approved immutable model or valid
active passage cache.

If cleanup evidence is incomplete, do not treat the run as PASS. Inspect only
the owned runtime and rerun after resolving the local resource condition.
