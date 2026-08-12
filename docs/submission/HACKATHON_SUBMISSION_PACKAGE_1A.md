# Hackathon Submission Package 1A

## Project title

Memory Patch for AIOA

## One-line description

An evidence-first, fail-closed correction layer that verifies model answers and
lets owners retain approved corrections as private, non-canonical memory.

## Problem

Models can return fluent but unsupported or outdated claims. Their internal
memory is not evidence, and asking the same model to self-correct does not
create source authority. Persisting an unverified correction can amplify the
mistake across later conversations and models.

## Solution

Memory Patch generates an evidence-blind Draft V1, independently routes and
retrieves authoritative evidence, resolves temporal/conflict rules, binds
claims to exact source spans, freezes a deterministic Correction Packet, and
produces an evidence-bound Draft V2. A layered verifier decides whether a
Verified Answer may be returned; otherwise the system fails closed. With
explicit owner approval, the verified correction can become private Personal
Memory for later compatible-model reuse without becoming canonical evidence.

## What is technically novel

- Independent evidence-blind generation and authoritative retrieval paths.
- Exact claim/span/evidence lineage rather than prose-only self-critique.
- Immutable Correction Packet and integrity receipt between analysis and
  regeneration.
- Layered deterministic/evidence verification controlling final answer
  eligibility.
- Owner-approved private memory with model bindings, lifecycle, audit, and
  canonical-conflict suppression.
- Cross-model private reuse without granting the model approval or evidence
  authority.
- Optional typed Critic bridge limited to an untrusted correction candidate.
- Failure injection, 4 GB constrained profile, full security regression, and
  native backup/isolated restore evidence.

## CockroachDB usage

The implementation uses CockroachDB v26.2.4 for durable source registry,
publication/provenance, German Law source/version/chunk state, retrieval
foundations, idempotent workflow records, Personal Memory slots/proposals/
approvals/commits/lifecycle, review state, and append-only audit events/chain
heads. RLS and `FORCE ROW LEVEL SECURITY` enforce tenant/owner isolation.
Purpose-bound roles separate application, migration, publication, Commit
Helper, reviewer, and audit capabilities. Forward-only migrations, transient
failure recovery, native backup, integrity checking, and isolated restore were
validated in disposable single-node environments.

We do not claim production HA, multi-region scale, or an SLA from the
single-node demo proof.

## Security and trust model

- Provider credentials stay server-side; browser privileged-secret count is
  zero.
- No missing purpose-specific credential falls back to admin/master authority.
- Models, HATs, Critic, reviewers, UI fields, and Commit Helper cannot create
  owner approval.
- Canonical sources require registry/publication/authority/lineage/scope/time
  eligibility.
- Personal Memory is owner-scoped and non-canonical.
- Audit is append-only and hash-chained; tested tampering is detected.
- Step 41 recorded zero tested secret leaks, cross-tenant/cross-owner successes,
  authority escalations, unauthorized evidence inclusions, known-bad
  fail-open returns, and undetected audit tampering.

This is repository engineering validation, not an external security or legal
certification.

## Demo flow

```text
Question -> evidence-blind Draft V1
         -> independent German Law retrieval and temporal resolution
         -> claim REFUTES/SUPPORTS binding
         -> Correction Packet
         -> evidence-bound Draft V2
         -> layered verification
         -> Verified Answer or fail closed
         -> optional owner approval -> ACTIVE private Personal Memory
         -> later compatible-model reuse
```

Primary: `primary-entry-into-force`. Backup:
`backup-special-case-reservation`. Supported/no-correction and temporal/conflict
fail-closed cases are included. The [Golden Path](../demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md)
contains exact questions and operator rules.

## Setup and demo

Safe replay:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py --mode replay --pretty
```

Live mode is explicitly cost-authorized, uses the pinned provider/model, has
at most two short calls, and never writes the fresh model text to committed
evidence. See the [operator runbook](../operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md)
and [video script](../demo/YOUTUBE_DEMO_SCRIPT_1A.md).

## Evidence

The [submission index](../SUBMISSION_INDEX_1A.md) links the architecture,
German Law E2E, Critic optionality, 4 GB performance, full security regression,
RC/backup/restore, and final demo evidence.

## Limitations

- Bounded German federal-law fixture; not legal advice.
- Fresh live generation depends on the approved hosted provider and a
  server-side credential.
- Deterministic replay is presentation/rehearsal evidence, not a new live call.
- Constrained single-node demo/non-HA profile.
- Personal Memory requires authenticated explicit owner approval.
- No automatic private-to-shared or private-to-canonical promotion.
- Critic is optional and candidate-only.
- Production AWS deployment, public unmetered inference, production HA/DR, and
  production SLA claims are outside this validated submission.
