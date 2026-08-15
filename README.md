# Memory Patch for AIOA

**Memory Patch turns a model answer into an evidence-checked, fail-closed
answer and lets an owner explicitly retain a verified correction as private,
non-canonical memory.**

Large language models can sound certain while recalling the wrong fact.
Memory Patch keeps the original generation separate from authoritative
retrieval, binds each material claim to evidence, builds an immutable
Correction Packet, and releases only a verified result or an explicit
fail-closed outcome.

```text
Draft V1 (evidence blind)
  -> independent route + authoritative retrieval + time/conflict policy
  -> claim/evidence binding -> Correction Packet
  -> Draft V2 -> layered verifier -> Verified Answer or fail closed
```

The core trust rule is visible throughout the implementation and demo:

```text
MODEL OUTPUT != CANONICAL EVIDENCE != PERSONAL MEMORY
             != CRITIC CANDIDATE != HUMAN APPROVAL
```

## Why this is different

- Draft V1 cannot see retrieval results, databases, tools, or the Correction
  Packet.
- German Law evidence is independently retrieved with source, publication,
  scope, lineage, temporal, freshness, and conflict checks.
- The Correction Packet and layered verifier bind the exact claim, evidence,
  correction, model/prompt identities, and final eligibility decision.
- A known-bad Draft V1 is never returned on the verified route.
- Personal Memory requires explicit owner approval and remains private context,
  never canonical evidence.
- The optional Critic may submit only an untrusted Step 28 candidate. It cannot
  route, approve, commit, activate, review, execute, or create evidence.

## CockroachDB in the working system

The validated CockroachDB v26.2.5 integration stores source/version/chunk
lineage, retrieval state, Personal Memory lifecycle state, idempotency and
recovery records, review state, and the append-only audit chain. Tenant and
owner isolation uses RLS and `FORCE ROW LEVEL SECURITY`; migrations, role
separation, native backup, isolated restore, and post-restore integrity were
tested in disposable single-node environments. This is a constrained demo
topology, not a claim of production HA.

## Hackathon sponsor components

The project meaningfully uses both required CockroachDB tools. Distributed
Vector Indexing stores lineage-bound `VECTOR(384)` embeddings and performs
HAT-scoped L2 retrieval before deterministic hybrid and temporal resolution.
The AI-assisted release workflow uses the `ccloud` CLI as an authenticated,
fail-closed control-plane gate for the exact hosted jury cluster; its JSON
metadata must match the approved provider, region, plan, state, version, and
endpoint identity before later release operations are allowed.

The public workload runs on Amazon ECS Express Mode with Fargate from an
immutable ECR digest. Amazon S3 Versioning and Object Lock preserve exact
evidence snapshots, while Cognito, Lambda, Secrets Manager, KMS, CloudWatch
Logs, CloudFormation, and IAM provide bounded identity, secrets, observability,
and deployment controls. See the
[Devpost component integration](docs/submission/DEVPOST_COMPONENT_INTEGRATION_1A.md)
and the sanitized
[ccloud live receipt](docs/evidence/cockroachdb-cloud/ccloud-control-plane-gate-1a.json).

## Run the judge demo

Deterministic replay is the safe default. It checks the frozen Step 42 RC,
every referenced digest, the full 12-stage trace, security counters, docs,
and submission package without network or provider cost:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py --mode replay --pretty
```

The explicitly authorized live mode makes at most two short evidence-blind
Draft V1 observations with the pinned OpenRouter/Kimi identity. It then shows
the independently verified frozen-RC trace; it never fabricates a model error
or presents replay as a fresh live E2E run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py \
  --mode live --allow-live-provider-cost --pretty
```

The provider credential is loaded server-side only. Do not expose it in a
browser, recording, shell history, or committed configuration. Full setup,
preflight, case selection, 4 GB profile, and cleanup instructions are in the
[Step 43 demo runbook](docs/operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md).

## Start here

- [Judge-facing Golden Path](docs/demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md)
- [5–8 minute video script](docs/demo/YOUTUBE_DEMO_SCRIPT_1A.md)
- [Submission package](docs/submission/HACKATHON_SUBMISSION_PACKAGE_1A.md)
- [System architecture](docs/architecture/MEMORY_PATCH_SYSTEM_OVERVIEW_1A.md)
- [Evidence and documentation index](docs/SUBMISSION_INDEX_1A.md)
- [Canonical production roadmap](docs/roadmap/PRODUCTION_ROADMAP.md)

## Security and operating profile

Step 41 recorded zero tested secret leaks, cross-tenant/cross-owner successes,
authority escalations, unauthorized evidence inclusions, known-bad fail-open
returns, and undetected audit tampering. Step 40 validated a single-worker,
bounded 4 GB demo profile with lazy local E5 loading, hosted generation,
Critic and ingestion disabled by default, explicit queues/pools, and pressure
backpressure that grants no authority. Step 42 froze and restored the RC from
a native CockroachDB backup, then revalidated provenance, retrieval, Personal
Memory, audit, security boundaries, and the German Law path.

## Limitations and non-goals

- The validated legal scope is a bounded German federal-law fixture and is not
  legal advice.
- The hosted generation provider is required for a fresh live Draft V1; replay
  is explicitly labeled and uses committed hash-only evidence.
- The constrained single-node demo does not prove production HA, geographic
  scale, or a production SLA.
- Personal Memory never auto-activates and never becomes shared or canonical
  evidence without a separate authorized process.
- The repository demonstrates an optional candidate-only Critic bridge, not an
  autonomous reviewer or execution agent.

Source and import provenance are indexed in [the documentation
catalog](docs/README.md). The final package describes only behavior supported
by committed engineering evidence.
