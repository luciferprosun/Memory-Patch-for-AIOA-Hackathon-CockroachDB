# Memory Patch 5–8 Minute Video Script 1A

## Before recording

Use the [Step 43 runbook](../operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md)
and [Golden Path](MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md). Keep the terminal and
browser free of credentials. Display the RC SHA, not environment values.

## 0:00–0:30 — the problem

“A model can give a confident answer from memory and still get a material fact
wrong. Asking the same model to check itself does not create independent
evidence.” Show the primary BMJErnAnO question.

## 0:30–1:00 — the Memory Patch idea

Show the architecture overview. Say: “The original model answer is evidence
blind. A separate path retrieves authoritative evidence, binds claims, creates
a deterministic correction packet, and a verifier decides what may be
returned.” Put this line on screen:

```text
MODEL OUTPUT != CANONICAL EVIDENCE != PERSONAL MEMORY
             != CRITIC CANDIDATE != HUMAN APPROVAL
```

## 1:00–2:00 — Draft V1

Run replay, or the explicitly authorized live command. Point out the pinned
OpenRouter/Kimi identity and disabled tools/web/code. If using replay, say
“This is a validated hash-only replay, not a new provider call.” Explain that
the canonical live RC run observed the wrong-date defect.

## 2:00–3:15 — evidence and correction

Show route `HAT_ASSIST`, German Law HAT, official source `BJNR1330A0023`,
provision III, evidence status, temporal result, and Evidence Bundle hash. Read
the short official effective-date sentence. Show the `REFUTES` link and
Correction Packet hash.

## 3:15–4:15 — Verified Answer

Show Draft V2, the layered-verifier status, verification-summary hash, and
`VERIFIED_ANSWER`. Use the before/evidence/correction/after table. Say: “If any
binding fails, Memory Patch does not return the known-bad Draft V1.”

## 4:15–5:15 — owner-controlled Personal Memory

Show proposal reaching `AWAITING_APPROVAL`, then a separate owner approval,
technical commit, and activation receipt. Emphasize that the owner approves;
the model and Commit Helper do not. Keep
`canonical_evidence_authority=false` visible.

## 5:15–6:00 — later reuse

Show the later related question and the same ACTIVE patch hash retrieved for
two compatible model identities. Explain that canonical conflict suppresses
memory and another owner cannot see it.

## 6:00–6:30 — trust model

Show Step 41’s zero counters. Mention RLS/FORCE RLS, purpose-bound credentials,
append-only audit, and candidate-only optional Critic. Do not imply an external
security certification.

## 6:30–7:00 — CockroachDB and recoverability

Show CockroachDB’s implemented roles: durable source/version/chunk lineage,
retrieval state, Personal Memory lifecycle, idempotency, audit, review, and
tenant/owner isolation. Mention the native backup and isolated restore proof.
Label the demo single-node/non-HA.

## 7:00–7:30 — close

“Memory Patch shows a truthful chain: the model can be wrong, evidence is
independent, correction is bounded, verification controls release, and the
owner controls private memory.” Point to the submission index and reproducible
replay command.

## Backup branch when the live primary answer is correct

Do not ask repeatedly for a mistake. Say: “The fresh model answered this case
correctly, so Memory Patch correctly creates no correction.” Switch once to
`backup-special-case-reservation`. If that exact binary case is also correct or
malformed, stop live calls and use the committed replay while labeling it.
Never edit, truncate, or fabricate the model response.
