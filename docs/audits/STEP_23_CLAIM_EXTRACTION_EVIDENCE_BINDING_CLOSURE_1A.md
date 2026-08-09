# Step 23 - Claim Extraction and Evidence Binding 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 24: NOT STARTED.

## Starting identity

- Exact Step 22 baseline: `a24e1439d5d3971182dbf79c5e317f390065e712`
- Baseline subject: `feat(modeling): add provider-neutral draft v1 adapter 1a`
- Branch: `main`
- Baseline tests: 1,499 passed
- Baseline focused Step 17-22/authority/tenant/persistence/serialization: 521 passed
- Baseline contract validator and compileall: PASS

The final closure identity is the commit containing this record. No future
commit SHA is embedded before that commit exists.

## Implemented contracts

Step 23 adds immutable/hash-bound `ClaimRecord`, `ClaimEvidenceLink`,
`ClaimEvidenceAssessment`, `ClaimBindingRequest`, and `PacketInputSnapshot`
contracts. Every upstream Draft V1, Step 20 bundle/item, and Step 21
result/assessment hash is verified before extraction or binding. Tenant, user,
route, HAT, manifest, effective scope, original query, and source lineage must
match exactly.

Draft spans use NFC Unicode code-point offsets with inclusive start and
exclusive end. Claim IDs bind the Draft hash, offsets, and exact text. Claims
are typed and classified as atomic, compound, or non-factual without rewriting
the authoritative Draft span.

## Binding and candidate semantics

Evidence comes only from the verified Step 20/21 universe. Exact normalized
assertion equality creates support; one explicit negation counterpart creates
refutation; bounded token overlap is related-only diagnostic metadata. No
retrieval, web access, model self-verification, vector-score truth, or rank
override exists.

Step 21 temporal/freshness/conflict status and Step 20 source authority remain
hard ceilings. Future/non-applicable evidence cannot support; stale evidence
remains inspectable but unverified; source-authority claims require exact
official metadata; material supporting/refuting conflicts preserve both sides
and remain `UNVERIFIED`.

`SUPPORTED`, `REFUTED`, and `UNVERIFIED` are Step 23 candidate statuses only.
They grant no answer, approval, execution, publication, or memory authority.

## Snapshot and persistence decision

The packet-input snapshot freezes canonical ordered claims, links,
assessments, policy, and upstream identities. Any semantic change yields a new
snapshot hash. It is not a Correction Packet and contains no correction,
prohibition, packet HMAC, Draft V2, or final-verifier field.

No migration or persistence repository is added. Existing Step 4 final
claim-verdict storage is not overloaded with preliminary Step 23 candidate
semantics. The immutable snapshot is the canonical Step 24 input.

## Validation

- Step 23 focused suite: 33/33 PASS.
- Full repository suite: 1,532/1,532 PASS.
- Step 17-22, authority, tenant, persistence, and serialization regressions:
  PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled Step 23 validation: PASS.

The controlled fixture produces one supported, one refuted, and four
unverified claims, including non-factual and compound segments. Separate
synthetic cases prove future-effective rejection and material conflict
preservation. The committed Step 22 Draft identity is verified from sanitized
evidence; its hosted response text was intentionally not committed, so the
typed text fixtures are explicitly synthetic rather than falsely described as
real provider output.

Sanitized evidence is committed at
`docs/evidence/modeling/step23-claim-evidence-binding-validation.json`.

## Authority, isolation, effects, and limitations

Hash tampering, cross-tenant/user/route binding, weak authority, and
unpublished evidence fail closed. There are no provider/model, retrieval,
network, database, AWS/S3, approval, execution, or Personal Memory effects.

The conservative exact V1 policy intentionally does not attempt semantic
entailment, arbitrary paraphrase recognition, legal reasoning, or final claim
verification. Compound claims remain intact and unverified when safe splitting
is not deterministic.

## Step 24 handoff

Step 24 may consume `PacketInputSnapshot`, exact Draft V1 hash, ordered
`ClaimRecord`, `ClaimEvidenceLink`, and `ClaimEvidenceAssessment` values. It
may construct required corrections, prohibited claims, citations, policy and
integrity material. Draft V2 generation remains later.

Step 24 remains NOT STARTED in this closure.
