# Step 40 4 GB runtime validation 1A

## Purpose and safety boundary

This runbook validates the versioned constrained profile without contacting a
production database, provider, AWS, or S3. It uses fixed provider-free test
scenarios and the already-installed, offline, verified Step 19 E5 runtime. It
does not start Step 41, perform production deployment, or rotate a secret.

The expected start base is
`90c2563556fea96ee120b264166640f277677acd`. Run from the repository root on
`main`. Do not paste a provider key or database locator into an argument,
terminal transcript, evidence file, or documentation.

## Preflight

Verify the repository before any measurement:

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
git remote -v
```

At Step 40 start, require a clean `main`, the expected remote, no active Git
operation, exact local/remote Step 39 SHA equality, Step 39 complete, and Steps
40 and 41 not started.

Verify physical memory without treating swap as RAM:

```bash
awk '/MemTotal|MemAvailable|SwapTotal|SwapFree/ {print}' /proc/meminfo
```

The profile requires at least 3,700 MiB detected physical memory. The observed
host has 3,751 MiB. If the gate fails, stop with
`STEP40_HOST_MEMORY_BELOW_PROFILE_MINIMUM`.

The controlled runner reads `.local/external-data.env` only through the
existing strict external-volume loader. It emits no configured path. External
mount identity, marker identity, filesystem separation, and reserve must pass.

## Profile validation

The canonical profile is `config/runtime/4gb-demo-1a.json`. Validate its
strict schema and digest:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest tests.test_step40_runtime_profile -q
```

Expected profile facts:

- one web worker and no second frontend process;
- remote required CockroachDB with zero local core processes;
- application/Commit Helper/audit/review pools 4/1/1/1;
- exact E5, lazy singleton, batch 8;
- Critic and ingestion disabled by default;
- audit enabled and review request driven;
- all queues and threads bounded;
- cache external, derived, non-authoritative, and rebuildable;
- all authority override flags false.

## Resource-bound and optional-service tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest tests.test_step40_resource_bounds -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest tests.test_step40_optional_services -q
```

These tests prove deterministic pressure decisions, zero authority, queue
backpressure, no E5 load at idle, one instance under concurrent first use,
batch enforcement, Critic-disabled readiness, Critic-failure core readiness,
prepared-corpus ingestion-off readiness, required dependency failure, cheap
liveness, and the unchanged Step 38 core evidence anchor.

## Fixed scenario measurements

The measurement helper accepts no arbitrary command. Run all allowlisted
provider-free scenarios:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/measure_step40_runtime_resources.py --scenario all
```

It reports scenario ID, return code, duration, peak process-tree RSS, process
and thread count, CPU time, bounded output sizes, and output hashes. It never
records the child command or raw output. Failure, timeout, or output above 256
KiB makes the scenario fail.

The committed run observed:

| Scenario | Peak RSS | Duration |
|---|---:|---:|
| idle core | 65 MiB | 4.461 s |
| retrieval-only | 76 MiB | 8.293 s |
| German Law core | 78 MiB | 11.136 s |
| Personal Memory | 40 MiB | 18.810 s |
| Critic disabled | 23 MiB | 1.190 s |
| Critic enabled conformance | 159 MiB | 29.795 s |
| owner UI | 71 MiB | 6.451 s |

## Controlled resource validation

Run the complete gate:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/run_step40_4gb_resource_validation.py
```

This performs the base/evidence gate, host and external-volume preflight,
fixed scenarios, real offline E5 lazy-load/reuse measurement, conservative
peak calculation, pressure/backpressure proof, optional-component readiness,
security spot checks, and cleanup. It starts no CockroachDB and makes no
provider call because the constrained profile uses a remote required database
and the Step 38 live proof is already committed.

The exact closure evidence is materialized only from the chosen passing
pre-commit run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/run_step40_4gb_resource_validation.py \
  --write-evidence
```

Expected:

- `status=PASS_4GB_CONTROLLED`;
- `closure_eligible=true`;
- profile digest
  `eb205843f34039418bd49663ab47390777cbdc861f671172c8f025d588a55ad8`;
- conservative core peak at or below 3,000 MiB;
- one E5 instance, dimension 384, batch 8, repeated-use equality;
- German Law and Personal Memory scenarios pass;
- Critic-disabled core pass;
- pressure backpressure with zero partial/duplicate effects;
- audit, owner/tenant isolation, and Commit Helper separation true;
- secret leakage, authority violations, production mutations, and Step 41
  start all zero/false.

The committed evidence path is
`docs/evidence/performance/step40-4gb-resource-validation.json`, canonical
digest
`22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017`.

## Startup and readiness for the constrained profile

Production assembly must follow the profile order:

1. validate profile/digest;
2. validate external volume;
3. connect to remote CockroachDB;
4. verify schema;
5. verify prepared corpus/publication state;
6. start one Kernel/UI worker;
7. leave E5 unloaded;
8. leave Critic disabled;
9. leave ingestion disabled.

Readiness is false if database/schema, corpus, Personal Memory persistence,
audit append, external volume, provider configuration, or owner UI is not
ready. Critic disabled, E5 unloaded, ingestion disabled, and request-driven
review are not errors. Liveness checks responsiveness only and must not call a
model, run an E2E, or verify the whole audit chain.

## Database and connection inspection

The core profile starts zero local CockroachDB processes. Inspect only local
process absence and the typed pool limits; do not connect to production merely
to obtain a count:

```bash
pgrep -af 'cockroach.*start-single-node' || true
```

An unexpected local process is a topology violation. The maximum configured
client total is seven, with separate application, Commit Helper, audit, and
review purposes. Never combine their credentials. A local single-node test is
non-HA and outside the core memory acceptance result.

## Queue, thread, and pressure inspection

Review the canonical profile and run the resource-bound suite. Under injected
hard pressure, expected outcomes are:

- Critic: `OPTIONAL_CRITIC_SUPPRESSED`;
- ingestion: `OPTIONAL_INGESTION_PAUSED`;
- embedding: `EMBEDDING_BACKPRESSURE` before factory invocation;
- export: `EXPORT_BACKPRESSURE`;
- required core: `CORE_REQUEST_FAILED_CLOSED` only at hard pressure.

Verifier, audit, RLS, route, source authority, canonical evidence, and Personal
Memory approval flags must remain false in every decision.

## Focused and full validation

Run:

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest \
  tests.test_step40_runtime_profile \
  tests.test_step40_resource_bounds \
  tests.test_step40_optional_services -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest discover -s tests -p 'test*.py' -q

.venv/bin/python scripts/validate_contracts.py
node scripts/check_step35_ui_assets.mjs
```

Also run the Step 38, Step 39, Step 37, Step 36, Step 35, Step 33, Step 31,
Step 30, Step 5, and Step 6 focused modules listed in the closure record. All
failures and errors must be zero.

## Diagnosis

- `STEP40_4GB_BUDGET_NOT_MET`: reduce concurrency or batch within existing
  semantics, remove a duplicate optional process, or stop without commit. Do
  not exclude a required component from measurement.
- `STEP40_EXTERNAL_VOLUME_PREFLIGHT_FAILED`: repair the verified mount/cache
  configuration; never fall back to the system drive.
- `STEP40_EMBEDDING_RUNTIME_UNAVAILABLE`: restore the exact offline Step 19
  runtime/model; do not download or substitute a model during closure.
- `STEP40_PRESSURE_GUARD_FAILED`: stop. Resource pressure must not enter a
  semantic or authority path.
- Remote database unavailable: readiness remains false. Do not start an
  unapproved local database fallback.
- Critic unavailable: show optional unavailable/disabled status and continue
  the core path.

## Shutdown and cleanup

The controlled runner owns only short-lived child processes and temporary
anonymous capture files. It terminates the exact process group on timeout,
closes captures, and starts no database. After a run, require no validation
child or unexpected local CockroachDB process, no temporary evidence staging
file, and no production resource touch.

Step 41 remains `NOT STARTED`. Do not extend this runbook into the full security
campaign.
