# Step 20 Hybrid Evidence Validation 1A

## Constraints

The focused and full suites are offline and require neither a model runtime
nor a database. Controlled validation uses only the verified local Step 19
model/cache, a read-only Step 16 source bundle, and one owned loopback
CockroachDB process. It performs zero provider, AWS, or S3 mutation.

Machine paths and external-volume identity stay in the ignored
`.local/external-data.env` file.

## Baseline and ordinary validation

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest discover -s tests -p 'test*.py' -q
python3 scripts/validate_contracts.py
python3 -m compileall -q src scripts tests
```

## Focused Step 20 suite

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest tests.test_step20_hybrid_evidence_bundle -q
```

The suite verifies hash/binding failures, exact lineage deduplication,
metadata conflicts, integer fusion, authority/isolation negatives,
deterministic diversity, UTF-8 byte budgeting, deep immutability, honest
coverage, non-persistence safety, and the Step 21 boundary.

## Controlled validation

```bash
python3 scripts/run_step20_hybrid_evidence_validation.py
```

The runner:

1. verifies the Step 8 external-volume boundary and exact Step 19 runtime;
2. verifies the pinned local E5 model and switches to offline mode;
3. verifies one bounded real Step 16 German-law publication fixture;
4. starts one owned loopback CockroachDB v26.2.4 node;
5. applies and replays the existing ten migrations without a Step 20
   migration;
6. obtains actual exact, statute/section, full-text, and keyword results from
   the Step 18 service;
7. generates/replays bounded embeddings and obtains an actual Step 19 vector
   result;
8. proves shared binding, hash verification, deduplication, exact priority,
   fixed-point fusion, input-order independence, metadata-conflict rejection,
   byte budgeting, and deterministic bundle replay;
9. verifies SQL hard-filter negatives and Step 20 wrong-model rejection;
10. stops the exact owned PID, closes its ports, and removes its temporary
    store.

Expected final JSON field: `"status":"PASS"`.

## Static boundaries

```bash
rg -n \
  "openai|anthropic|gemini|nvidia|InferenceClient|remote.?model|cross.?encoder|llm.?rerank|model.?rerank" \
  src/aioa_memory_kernel/evidence || true

rg -n \
  "temporal.?resolver|freshness.?policy|repeal|supersed.*resolve|future.?effective|question.?time.?applic|stale.?source.?decision" \
  src/aioa_memory_kernel/evidence || true
```

Documentation strings that state a prohibited feature is deferred are
expected. Runtime network/model reranking and Step 21 decisions are forbidden.

Step 21: NOT STARTED.

## Failure and cleanup

Any input/hash/authority mismatch fails closed with a stable Step 20 reason.
The validator's `finally` path owns only its prefixed disposable runtime. It
does not force-kill unrelated processes and preserves verified Step 19 model
and embedding caches.
