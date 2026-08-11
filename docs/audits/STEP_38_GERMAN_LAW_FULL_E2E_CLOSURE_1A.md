# Step 38 German Law Full End-to-End Closure 1A

## Record status

`PRE-COMMIT WORKTREE CLOSURE RECORD`.

This document records the completed Step 38 implementation surface and the
observed live worktree validation. It is based on exact Step 37 closure
`9888070ab171fd057b17ab3057b3cf868cf704d2`. It does not invent a Git closure
SHA or assert `COMPLETE AND PUSHED` before the approved commit and push exist
and are verified reachable from `origin/main`.

- Final coherent controlled validation: `PASS_LIVE_COHERENT_LINEAGE`.
- Closure eligibility: `true`.
- Selected real case: `primary-entry-into-force`.
- Coherent Personal Memory runtime: `PASS`.
- Step 39 boundary scan: `PASS`; unexpected production-bridge hits: `0`.
- Exact owned-runtime cleanup: `COMPLETE`.
- Sanitized validation digest:
  `b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
- Sanitized validation evidence target:
  `docs/evidence/e2e/step38-german-law-full-e2e-validation.json`.
- Demo trace target:
  `docs/evidence/e2e/step38-german-law-demo-trace.json`.
- Final post-edit repository regression: `2061/2061 PASS in 122.057s`.
- Closure commit: `NOT CREATED`.
- Push/fetch reachability: `NOT PERFORMED`.
- Step 39: `NOT STARTED`.

No final commit SHA or push result is invented in this record.

## Intended Step 38 scope

Step 38 composes the Step 13-37 German Law path: routing, scoped retrieval,
immutable exact evidence, temporal/conflict policy, evidence-blind Draft V1,
claim binding, Correction Packet, evidence-bound Draft V2, layered
verification, Verified Answer or review, owner-private Personal Memory,
approval/commit/activation, cross-model reuse, audit, UI, review, and bounded
recovery.

It creates no source-publication, canonical-evidence, approval, commit,
reviewer, audit, model, execution, external-action, or Control Write
authority. The Critic production bridge remains Step 39.

## Implementation inventory to review

The final changeset must be reviewed for at least:

- typed golden cases with real and synthetic classifications;
- exact BMJErnAnO I/II/III fixture binding;
- provision II Roman legal-reference splitting and nominal-coordination
  atomicity boundaries;
- strict full-sentence classification for the provision II backup, including
  correct/no-defect, exact-negation/`REFUTES`, and invalid/fail-closed paths;
- fixture-bound provision III temporal projection receipt;
- Draft V1 evidence-blindness proof;
- exact `EvidenceBoundCorrectionContext` construction;
- typed `DraftV2TargetProjection` derived only from the verified packet and
  context, with canonical rebuild and an exact-output whitelist;
- Draft V2-only provider wrapper and
  `EvidenceBoundProviderInputReceipt`;
- a full reconstructible corrected-evidence proof and independent Step 25
  revalidation, not an opaque signal hash;
- the versioned Step 29 corrected-claim evidence reference that retains the
  original `REFUTES` link and citation;
- legacy Step 25/29 compatibility;
- focused tamper, scope, owner, model, authority, and recovery tests;
- a controlled runner that labels offline results honestly and binds the live
  upstream/downstream proof to one owned runtime and database; and
- ADR, architecture, golden-case, operations, and this pre-commit audit
  record.

The final file list is established only by the Step 38-only staged changeset
review immediately before the closure commit. No unrelated file is accepted.

## Exact fixture identity

| Field | Required value |
| --- | --- |
| HAT | `german-law` version `1.0.0` |
| manifest digest | `ab6ff572596993d63fdfd148207fcb4593f2672583f9d8dbedcc8f7e0f246109` |
| HAT scope | `german-law-global-1a` |
| source ID | `de-federal-gii-bjnr1330a0023` |
| official identifier | `BJNR1330A0023` |
| version | `legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95` |
| provision I | `323c88960cc5eeca3e2d4b6c3c34630947f85ec82c75e1e398492a319bd13147` |
| provision II | `6a12a5f19d7a4b61d71be5c5583d0a3a41b3111fcf00803892200fc42260d99e` |
| provision III | `fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1` |
| Step 14 manifest | `ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89` |
| Step 15 manifest | `7094358f7c9bb6acf62160484a017074da70361c73e4e5bbd7623f700414b125` |
| Step 16 manifest | `6871562b5b17d632c0e15169fefe7186f3fc7d7b5eb59f4140c367bf2c8a37e8` |

Exact provision II is:

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten vor.
```

Exact provision III is:

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum selben Gegenstand sind nicht mehr anzuwenden.
```

The primary real case is the bounded provision III date-completion question:
`Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am [Datum]
in Kraft.“` The deterministic test double uses the wrong sentence `Diese
Anordnung tritt am 1. Januar 2025 in Kraft.`; the accepted correction is
`Diese Anordnung tritt am 1. Januar 2024 in Kraft.` A real provider response
is never altered to force that defect. The declared backup is
`backup-special-case-reservation`, bound to exact provision II. Its exact
question is:

```text
Vervollständige nach Abschnitt II der BMJErnAnO den Satz, indem du „nicht“ einsetzt oder die Lücke leer lässt: „Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten ___ vor.“
```

Its correction condition is
`SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED`. The only accepted Draft
V1 shapes are the complete canonical provision II sentence and its complete
single-negation counterpart with `nicht` immediately before `vor`. The first
is correct and must produce no defect or correction. The second must produce
one full-span `REFUTES` link and an exact correction removing the negation.
All other shapes fail closed before claim selection. The supported case asks
when the order enters into force.

Historical-unavailable, equal-rank conflict, and route-negative cases remain
explicitly synthetic. No closure may describe them as facts from the real
corpus.

## Temporal projection audit

The final audit must reconstruct the provision III temporal receipt and prove:

- exact provision hash and exact entry sentence verified;
- exact German date text `1. Januar 2024` parsed to
  `2024-01-01T00:00:00Z`;
- method `FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE`;
- `fixture_bound=true`;
- `preexisting_temporal_metadata_used=false`;
- `model_inference_used=false`; and
- `canonical_evidence_authority=false`.

The projection is not source publication, pre-existing Step 18 metadata, a
model inference, or proof of an unavailable historical version.

## Draft and evidence proof audit

Draft V1 must receive only the original question. The audit must then follow
every Step 24 citation through its unchanged Step 23 evidence link, Step 20
bundle/item, source/version/chunk/content identity, relation, offsets, exact
Unicode span, and span hash into `EvidenceBoundCorrectionContext`.

If the provision II backup runs, the audit must additionally prove that the
whole sentence remained one exact claim. Only Roman `I`, `II`, or `III`
followed by horizontal whitespace and lowercase continuation avoids a
sentence boundary; newline/CR, `IV+`, and uppercase continuation do not.
Capitalized nominal coordination around `und`, `oder`, `sowie`, `and`, or
`or` is non-clausal, while true clause coordination remains `COMPOUND`.
Consequently the canonical sentence is one atomic `SUPPORTED` claim with no
RequiredCorrection, while the exact `nicht vor` counterpart is one atomic
`REFUTED` claim with a full-sentence evidence link. The provider output may
not be repaired, completed, or paraphrased to reach either state.

The Draft V2 wrapper must receive a `DraftV2TargetProjection` derived only from
the verified packet and context. The projection must use unique exact
`REFUTES` excerpts, one atomic citation-bearing line per excerpt, record
omission-safe corrections without exact facts, require at least one exact
segment, and reject headings, prefixes, paraphrases, additional text, and
prohibited claims. The wrapper must canonically rebuild that projection from
the typed packet and context before any provider call and must require exact
generated-output equality.

The wrapper must produce a provider-input receipt binding the base and actual
augmented request, context, packet, target-projection hash, approved provider
identity, augmented content digest, provider-response hash, and purpose. The
before/after trace must bind the same projection hash.

For a corrected target, a `CorrectedEvidenceProof` must be a full
reconstructible object. At minimum it must bind:

- proof version, request and Correction Packet hashes;
- target Draft V2 claim ID/hash/citation-stripped text SHA-256;
- satisfied correction IDs;
- packet citation ID/hash;
- evidence-context hash as audit lineage;
- full original Step 23 `ClaimEvidenceLink`, including original `REFUTES`
  relation, exact offsets, Step 20 and source/version/chunk/content identities,
  and evidence-span text SHA-256; and
- a deterministic proof hash derived from the complete typed object.

The proof persists no additional raw evidence text. Step 25 must independently
validate request/target binding, the nested link hash and `REFUTES` relation,
packet citation/link identities, every satisfied correction's exact
`REFUTES` replacement fact, and target-text/link-span digest equality before
emitting `CORRECTED_EVIDENCE_SUPPORTS`. The bound context hash is audit
lineage, not authority on its own. Trusting a protocol implementation,
`SUPPORTS` enum, signal hash, or opaque proof SHA is insufficient. Any
detached, changed, incomplete, or uncertain proof must fail closed. The
ordinary semantic verifier cannot override `REFUTES`.

## Step 29 corrected-claim audit

The versioned corrected-claim reference must retain the original source claim
and assessment, `REFUTES` relation, evidence-link and citation identities,
required correction, exact evidence lineage, target Draft V2 claim,
Step 25 signal/proof hashes, VERIFIED summary, and Verified Answer claim
reference. The supplied Step 25 result must retain the full proof, and Step 29
must revalidate that result before accepting its proof hash into the reference.

It must never relabel the original link as `SUPPORTS` or repoint that link to
the Draft V2 claim. Construction, serialization, parsing, service, and
persistence round trips must reject an unknown discriminator, changed source
relation/link/citation, false target support, missing proof, tampered Step 25
result, or owner/scope mismatch.

When `step25_result=None`, the legacy SUPPORTS-only Step 29 path and its
established hashes/JSON must remain unchanged.

## Personal Memory and authority proof required

Required lifecycle evidence is:

```text
DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL
         -> APPROVED -> COMMITTED -> ACTIVE
```

No edge may be skipped. Owner approval, technical commit, and activation are
separate hash-bound authorities. The same ACTIVE patch hash may be returned to
two allowed model identities, must be denied to another owner or tenant, and
must be suppressed when canonical evidence conflicts.

The patch remains private and non-canonical. Model, provider, HAT, Kernel,
Critic, UI, audit, reviewer, and recovery code cannot approve or commit. Step
39 Critic intake is excluded.

## Current validation ledger

The live rows below come from the exact sanitized observation with digest
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
Post-edit regression rows below were observed against the reconciled closure
worktree.

| Gate | Current record |
| --- | --- |
| Documentation reconciliation | `PASS - five Step 38 documents bound to the live digest` |
| Step 38 focused suites | `88/88 PASS in 33.468s` |
| Steps 25-26 plus corrected bridge | `66/66 PASS in 21.191s` |
| Steps 29-31 plus corrected bridge | `72/72 PASS in 29.743s` |
| Compileall | `PASS` |
| Contract validator | `PASS - 5 schemas, 4 fixtures, 2 unrelated HAT manifests and public/state/authority invariants` |
| Frontend/asset checks | `PASS - pinned asset sha256 22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313` |
| Offline controlled runner | `DEVELOPMENT_ONLY; NOT CLOSURE EVIDENCE` |
| Approved `openrouter` / `moonshotai/kimi-k2` | `PASS_REAL_VERIFIED_LINEAGE` |
| Selected real case | `primary-entry-into-force` |
| One coherent real-model/retrieval/Personal Memory/audit runtime and database | `PASS_LIVE_COHERENT_LINEAGE; closure_eligible=true` |
| Owned CockroachDB v26.2.4 migrations and replay | `PASS in the owned live runtime` |
| Tenant/owner/RLS/quota/idempotency negatives | `PASS through coherent closure proof` |
| Approval/commit/activation/reuse | `PASS through coherent closure proof` |
| Audit/review/UI/recovery integration | `PASS through coherent closure proof` |
| Step 39 boundary scan | `PASS; unexpected production-bridge hits=0` |
| Owned-runtime cleanup | `COMPLETE` |
| Full repository regression | `2061/2061 PASS in 122.057s` |
| Sanitized validation digest | `b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042` |
| Closure commit and push reachability | `NOT CREATED / NOT PERFORMED` |

The offline lane may report `PASS_OFFLINE_NOT_CLOSURE`, but it has no
real-model or closure authority. The observed live topology bound primary and
later retrieval, Steps 22-26, Personal Memory, audit, review, UI, and recovery
to the same owned CockroachDB runtime and database identities. No separate
component outputs were stitched into the E2E claim.

## Sanitized evidence handoff

The exact sanitized live result must be frozen at
`docs/evidence/e2e/step38-german-law-full-e2e-validation.json`. It binds safe
hashes for the exact fixture, question, route, retrieval, Evidence Bundle,
temporal receipt and result, Draft V1, claim snapshot, Correction Packet,
correction context, Draft V2 target projection, provider-input receipt,
reconstructible corrected-evidence proof, Draft V2, verification, Verified
Answer, candidate, proposal, approval, commit, activation, later retrieval,
cross-model reuse, audit export, review fallback, UI view, and recovery spot
check. Its canonical digest is
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.

It must also record fixture/provider classifications, database identity,
migration/replay counts, isolation and authority negatives, cleanup, zero
duplicates, and a canonical validation digest. It must not contain a secret,
authorization header, opaque session, machine path, raw provider response,
private Personal Memory text, or unbounded corpus extract.

The compact demo trace is a non-authoritative projection of that same result;
it cannot replace the validation artifact or canonical German Law evidence.

## Known limitations and retained boundaries

- The approved live provider was OpenRouter with `moonshotai/kimi-k2`; the
  successful observation selected `primary-entry-into-force`. A future rerun
  cannot replace a provider failure with a fake.
- The coherent runner topology uses one owned disposable CockroachDB runtime
  and one database. A detached upstream, later-retrieval, Personal Memory,
  audit, review, UI, or recovery identity must fail closure.
- The declared backup is usable only with an exact complete provision II
  response. A correct completion remains a no-defect result; only the exact
  `nicht vor` completion creates the bounded correction path. Any other shape
  fails closed rather than being normalized.
- The full reconstructible corrected-evidence proof and independent Step 25
  revalidation remain mandatory security boundaries in every future rerun.
- The fixture-bound date projection proves only its exact parse and does not
  establish an unavailable historical corpus version.
- Personal Memory remains private and non-canonical.
- Final post-edit regression, evidence-file freeze, commit, push, and fetch
  verification remain mechanical closure-workflow gates.
- Step 39 is explicitly outside scope and `NOT STARTED`.

## Safety and Git closure boundary

The live result is closure eligible only because its coherent proof records
zero source/AWS/S3/production-database mutation, secret leakage, cross-owner
or cross-tenant access, authority escalation, duplicate semantic effects, and
canonical conflict override; provider work stayed outside retried database
transactions and exact owned-runtime cleanup completed. The Step 39 boundary
scan passed with zero unexpected production-bridge hits.

The implementation and observed worktree validation do not themselves create
a pushed checkpoint. After the evidence files, checkpoint reconciliation,
final post-edit regression, and Step 38-only staged review pass, the closer may
create the single approved closure commit and push `main` without force. Until
that operation is verified:

```text
WORKTREE_VALIDATION=PASS_LIVE_COHERENT_LINEAGE
CLOSURE_ELIGIBLE=true
GIT_CLOSURE_COMMIT=NOT CREATED
PUSH_RESULT=NOT PERFORMED
STEP39_STATUS=NOT STARTED
```
