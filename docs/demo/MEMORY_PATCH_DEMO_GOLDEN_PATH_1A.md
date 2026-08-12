# Memory Patch Judge Demo Golden Path 1A

## Demo contract

This is the canonical judge-facing story for the Step 42 frozen RC at base
`f99057c601bfa41115185f52141ea327f3ef1aa1`. The source of truth is the
[Step 38 Golden Cases](GERMAN_LAW_GOLDEN_CASES_1A.md), not an improvised legal
question.

The invariant shown on screen should remain visible:

```text
MODEL OUTPUT != CANONICAL EVIDENCE != PERSONAL MEMORY
             != CRITIC CANDIDATE != HUMAN APPROVAL
```

Replay is always labeled **REPLAY — NOT A NEW LIVE PROVIDER VALIDATION**.
Neither live nor replay fabricates an incorrect answer.

## Phase A — original question

Primary case `primary-entry-into-force`, exact UTF-8 question:

```text
Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am [Datum] in Kraft.“
```

The runner loads these exact bytes from
[`tests/fixtures/step38_german_law_cases.json`](../../tests/fixtures/step38_german_law_cases.json).
It does not paraphrase them.

## Phase B — evidence-blind Draft V1

Step 22 sends only the original question and the pinned evidence-blind system
instruction to `openrouter/moonshotai-kimi-k2`. Draft V1 cannot access
retrieval results, canonical evidence, the Correction Packet, web/tools/code,
CockroachDB, provider credentials, or Commit Helper credentials.

The canonical Step 38 and restored Step 42 live runs observed the primary
material defect and produced `PASS_REAL_VERIFIED_LINEAGE`. Raw live model text
was deliberately not persisted; its digest and typed defect lineage were.
For a readable deterministic rehearsal, the Step 38 synthetic test lane uses
this explicitly labeled fixture claim:

```text
Diese Anordnung tritt am 1. Januar 2025 in Kraft.
```

That sentence is **not represented as a verbatim live-provider transcript**.
If a fresh live model supplies the correct 2024 date, the operator switches
once to the prevalidated backup case. If neither case yields an exact selectable
defect, use truthful replay rather than retrying for drama.

## Phase C — independent authoritative retrieval

Display the bounded projection:

| Field | Value |
| --- | --- |
| Route | `HAT_ASSIST` |
| HAT | `german-law` |
| Source ID | `de-federal-gii-bjnr1330a0023` |
| Official identifier | `BJNR1330A0023` |
| Provision | `III.` |
| Evidence status | `SUFFICIENT` |
| Retrieval | exact + full-text + vector + hybrid, then hard filters |

Canonical provision III:

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum selben Gegenstand sind nicht mehr anzuwenden.
```

The display includes the Evidence Bundle and temporal-result hashes, but no DB
credential, private memory content, or privileged configuration.

## Phase D — claim analysis and Correction Packet

The incorrect-date claim is linked as `REFUTES` to the exact official source
span. Step 24 freezes the required correction: replace the wrong effective date
with **1 January 2024**. The demo displays the immutable Correction Packet hash
and integrity identity. It does not let model prose alter the packet.

## Phase E — Draft V2 and verification

Only Draft V2 receives the bounded correction projection. The provider remains
tool-less. The layered verifier reconstructs the claim/span/evidence/citation,
packet integrity, exact correction, model/prompt binding, and final eligibility.
The demo shows the Draft V2 hash and Step 25 verification-summary hash.

## Phase F — Verified Answer

Step 26 returns `VERIFIED_ANSWER` only after every typed gate succeeds. The
known-bad Draft V1 return count is zero. Any incomplete or conflicting lineage
returns the established fail-closed result instead.

## Phase G — before/after delta

| Before | Independent evidence | Required correction | After |
| --- | --- | --- | --- |
| Model claim has the wrong effective date. | BMJErnAnO III says 1 January 2024. | Replace the date with 1 January 2024. | The corrected claim is evidence-bound and verified. |

This table summarizes a typed lineage; it does not substitute the table for
canonical evidence or the verifier.

## Phase H — optional Personal Memory

The verified correction may continue only through:

```text
DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL
         -> explicit owner APPROVED -> COMMITTED -> ACTIVE
```

Show the distinct owner-approval, commit, and activation receipts. Then ask the
validated later question and show Step 31 returning the same ACTIVE patch.
Always display:

```text
canonical_evidence_authority=false
```

Canonical conflict suppresses the patch. Another owner, tenant, or unapproved
model cannot retrieve it.

## Phase I — cross-model reuse

The committed trace contains two approved compatible model-identity digests
which retrieve the same active patch hash. This demonstrates provider-neutral
private reuse, not a grant of model or evidence authority.

## Phase J — optional Critic

The 4 GB profile keeps Critic `DISABLED_INTENTIONAL` by default. The optional
secondary replay shows that a schema-valid Critic assessment may create only a
Step 28 candidate. It cannot approve, commit, activate, route, publish a
source, create evidence, review, or execute. Core hashes are identical when
Critic is disabled, enabled, or unavailable.

## Primary and fallback cases

| Case | Purpose | Operator rule |
| --- | --- | --- |
| `primary-entry-into-force` | Judge-facing incorrect-date correction | Use first. Do not retry if fresh Draft V1 is correct. |
| `backup-special-case-reservation` | Prevalidated exact polarity defect | Try once only when primary is correct. Invalid shapes fail closed. |
| `supported-entry-into-force-clean` | No unnecessary correction | Show `NO_MATERIAL_CORRECTION_REQUIRED`. |
| `temporal-unavailable-edge` | Historical evidence unavailable | Show `HUMAN_REVIEW_REQUIRED`. |
| `conflicting-ceiling-edge` | Equal-rank conflict | Show `HUMAN_REVIEW_REQUIRED`; no invented winner. |

## Commands

Safe, deterministic judge replay:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py --mode replay --pretty
```

Explicitly cost-authorized live Draft V1 observation plus verified RC replay:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py \
  --mode live --allow-live-provider-cost --pretty
```

Live mode permits at most two 96-token responses, one per primary/backup case,
with a 45-second per-call timeout and no automatic retries. Critic and
ingestion remain off. The full operator checklist and cleanup are in the
[Step 43 runbook](../operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md).
