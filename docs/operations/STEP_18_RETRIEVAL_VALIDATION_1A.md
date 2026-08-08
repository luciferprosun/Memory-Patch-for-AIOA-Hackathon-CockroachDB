# Step 18 exact/full-text retrieval validation 1A

## Preconditions

Run from the canonical repository on `main`. The accepted Step 18 baseline is
`e1895e533c5f97bd06ffa2348cbdc1ee6419e42f`. The worktree may contain only
the intended Step 18 changes during implementation and must be clean after the
closure commit.

No AWS or S3 mutation, provider/model call, corpus acquisition, external
database, or network service is permitted. The integration validator reads a
bounded verified Step 16 local bundle and uses one owned loopback-only,
in-memory CockroachDB runtime under `/tmp`.

## Offline validation

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest tests.test_step18_retrieval -q

PYTHONPYCACHEPREFIX=/tmp/memory-patch-step18-pycache \
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
```

The focused suite verifies Step 17 binding, all four closed retrieval modes,
hard pre-candidate filters, access/owner isolation, SQL parameterization,
hash integrity, deterministic bounds, and the Step 19/20 boundary.

## Disposable CockroachDB validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step18_retrieval_validation.py
```

The default paths name the locally verified Step 14, Step 15, and Step 16
bundles and the pinned CockroachDB v26.2.4 binary. Optional path arguments may
point only to equivalent locally available inputs; every expected bundle and
binary digest is reverified before use.

The runner:

1. verifies the bounded real Step 16 publication item and provision bytes;
2. starts an owned loopback-only in-memory CockroachDB process;
3. applies the existing migrations and proves replay is a no-op;
4. seeds a bounded complete lineage plus cross-tenant, cross-HAT,
   unpublished, and rejected-authority negatives;
5. exercises exact source/official identifiers, structured statute/section,
   German full text, and keywords through the production read-only service;
6. records index inventory and sanitized `EXPLAIN` evidence;
7. drops the disposable database, requests shutdown of the exact owned
   process, verifies PID exit and closed ports, and removes its exact
   temporary runtime.

Success is one canonical JSON object with `status=PASS`, all retrieval and
hard-filter matrix entries passing, `force_kill_used=false`, and a
`validation_digest`. The committed sanitized copy is
`docs/evidence/retrieval/step18-exact-fulltext-retrieval-validation.json`.

## Static boundaries

```bash
rg -n \
  "requests|urllib|httpx|socket|subprocess|Popen|os\\.system|shell=True|boto|aws|psycopg|provider_call|model_call" \
  src/aioa_memory_kernel/retrieval || true

rg -n \
  "embedding|vector_search|vector similarity|cosine|semantic_retriev|sentence.?transformer|openai.*embedding|nvidia.*embedding" \
  src/aioa_memory_kernel/retrieval || true

rg -n \
  "hybrid|rerank|evidence.?bundle|diversity.?rank|context.?budget|reciprocal.?rank|rrf" \
  src/aioa_memory_kernel/retrieval || true
```

Documentation-only deferral language is acceptable. Production effect,
embedding, vector, hybrid, reranking, or final Evidence Bundle code is not.

## Failure semantics and cleanup

Route tampering, mismatched identity/scope, ambiguous route, unsupported scope,
invalid selector, oversized input, database error, unpublished or weak source,
and candidate-integrity failure are denied with a stable sanitized reason.
There is no liberal fallback and no SQL text or credential in returned errors.

If controlled validation fails after startup, stop only the owned process and
remove only its exact temporary runtime. Do not kill unrelated processes or
reuse a failed database as evidence. A force kill, surviving owned PID, open
owned port, or remaining temporary store makes cleanup validation fail. Drain
completion is reported separately and is never fabricated when the local CLI
path is unavailable.
