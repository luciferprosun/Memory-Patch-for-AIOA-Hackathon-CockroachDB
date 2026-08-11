# German Law Full End-to-End 1A

## Status, base, and scope

This document describes the Step 38 integration architecture based on exact
Step 37 closure `9888070ab171fd057b17ab3057b3cf868cf704d2`.

The Step 38 implementation and required live validation are complete in the
closure worktree. The approved OpenRouter/Kimi run returned
`PASS_LIVE_COHERENT_LINEAGE` with `closure_eligible=true`, selected
`primary-entry-into-force`, completed the coherent downstream proof and exact
owned-runtime cleanup, and produced validation digest
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
This architecture does not invent Git reachability: the closure commit is
`NOT CREATED`, push is `NOT PERFORMED`, and Step 39 is `NOT STARTED`.

Step 38 is a thin composition over Steps 13-37. It adds no legal, source,
model, Personal Memory, approval, commit, reviewer, audit, UI, execution, or
external-action authority.

## Integrated flow

```text
user question
  -> Step 13/17 German Law HAT route and policy
  -> Step 18 exact/full-text retrieval and Step 19 vector retrieval
  -> Step 20 immutable Evidence Bundle
  -> Step 21 temporal/conflict/freshness result
  -> Step 22 evidence-blind Draft V1
  -> Step 23 exact-span claims and original evidence relations
  -> Step 24 Correction Packet
  -> Step 38 exact EvidenceBoundCorrectionContext
  -> Step 38 exact DraftV2TargetProjection
  -> Step 25 Draft V2 plus layered and corrected-evidence verification
  -> Step 26 Verified Answer or fail-closed/review result
  -> Step 28 KNOWLEDGE_KERNEL correction candidate
  -> Step 29 versioned corrected-claim proposal evidence
  -> Step 30 owner approval, technical commit, activation
  -> Step 31 ACTIVE-only, owner-private, cross-model retrieval
  -> Steps 33-35 audit, review, owner UI
  -> Step 37 bounded retry/recovery spot check
```

Every edge retains tenant, owner where applicable, route, HAT scope,
source/version, state, and immutable hash lineage. Provider calls remain
outside retried CockroachDB transactions.

## Exact German Law fixture

The real fixture is the Step 16 `PUBLISHED` BMJErnAnO consolidated-reference
item. `PUBLISHED` is the internal registry state; the source remains
`AUTHORITATIVE_SECONDARY`, not a promulgation instrument.

| Identity | Exact value |
| --- | --- |
| HAT | `german-law` version `1.0.0` |
| HAT manifest digest | `ab6ff572596993d63fdfd148207fcb4593f2672583f9d8dbedcc8f7e0f246109` |
| HAT scope | `german-law-global-1a` |
| source | `de-federal-gii-bjnr1330a0023` |
| official identifier | `BJNR1330A0023` |
| version | `legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95` |
| provision I SHA-256 | `323c88960cc5eeca3e2d4b6c3c34630947f85ec82c75e1e398492a319bd13147` |
| provision II SHA-256 | `6a12a5f19d7a4b61d71be5c5583d0a3a41b3111fcf00803892200fc42260d99e` |
| provision III SHA-256 | `fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1` |
| Step 14 manifest | `ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89` |
| Step 15 manifest | `7094358f7c9bb6acf62160484a017074da70361c73e4e5bbd7623f700414b125` |
| Step 16 manifest | `6871562b5b17d632c0e15169fefe7186f3fc7d7b5eb59f4140c367bf2c8a37e8` |

The exact provision II UTF-8 text is:

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten vor.
```

The exact provision III UTF-8 text is:

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum selben Gegenstand sind nicht mehr anzuwenden.
```

`project_verified_bmjernano_evidence(...)` requires all three provision keys
and exact hashes. Its output contains the exact provision bytes only; it does
not substitute a paraphrase and fixes
`canonical_evidence_authority=false` and
`source_publication_authority=false`.

## Golden cases

The primary case asks the model to complete the date in the bounded sentence
`Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am [Datum]
in Kraft.“` The deterministic evidence-blind Draft V1 uses the wrong date
`1. Januar 2025`; the only accepted correction is the exact provision III
sentence with `1. Januar 2024`. This is the Personal Memory branch.

The declared backup is `backup-special-case-reservation`. It asks exactly:

```text
Vervollständige nach Abschnitt II der BMJErnAnO den Satz, indem du „nicht“ einsetzt oder die Lücke leer lässt: „Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten ___ vor.“
```

It binds exact provision II and correction condition
`SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED`. A provider response is
eligible only if it is the entire canonical sentence or the entire
single-negation counterpart with `nicht` immediately before `vor`. The
canonical sentence is correct and creates no material defect. The exact
`nicht vor` sentence is a polarity defect and binds provision II as
`REFUTES`. A placeholder, one-word response, paraphrase, prefix, suffix,
quotation, or additional sentence fails closed before claim selection.

The already-supported case asks when the order enters into force and must not
force a correction. Historical-unavailable, equal-rank-conflict, and
non-German-law routing cases are explicitly synthetic and cannot be
represented as real-corpus facts.

Cases are typed data in
`tests/fixtures/step38_german_law_cases.json`. Production routing does not
compare a request to a fixture string or select a hardcoded answer.

## Routing and retrieval

Step 17 selects the German Law HAT through Axis A and separately applies Axis
B policy. A non-law query remains `PASS_THROUGH`; Personal Memory cannot widen
the route.

Steps 18 and 19 enforce tenant, user, HAT, scope, publication, and source
authority before candidate generation. Step 20 freezes the exact source
bytes, identity, rank, and order. Step 38 may build test-bound Step 20 objects
from the exact provision bytes, but it may not attach a paraphrase to their
real source/version identity. Step 21 remains the evidence-status and temporal
authority.

## Fixture-bound temporal projection

The bounded upstream fixture does not expose a ready-to-use structured
`effective_from` value for provision III. Step 38 therefore adds
`GermanLawTemporalProjectionReceipt`, produced only after the exact provision
III hash and entry sentence verify.

The receipt binds:

- source, version, provision identifier, and provision content hash;
- exact entry sentence and its SHA-256;
- parsed German date text `1. Januar 2024`;
- `effective_from=2024-01-01T00:00:00Z`;
- method `FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE`;
- `fixture_bound=true`;
- `preexisting_temporal_metadata_used=false`;
- `model_inference_used=false`; and
- `canonical_evidence_authority=false`.

This is a typed adapter for the frozen fixture, not new source metadata, a
model conclusion, or evidence publication. It does not supply an unavailable
historical version. Historical gaps still fail closed.

## Draft V1 evidence blindness

Step 22 receives only the original query through the approved tool-less
provider request. `EvidenceBlindnessProof` verifies the exact query digest,
provider-call request hash, projected field names, and disabled tools,
function calls, web, and code execution. Evidence Bundles, temporal output,
Correction Packet, Draft V2, and Verified Answer are absent.

The deterministic test provider that creates the wrong-2025-date defect is
labelled synthetic. It does not stand in for a real provider run. The approved
OpenRouter/Kimi response is captured unchanged; a correct primary answer
causes the declared backup path rather than a manufactured error.

For the backup, the evidence-blind prompt controls only response shape. It
requires the complete filled sentence and discloses neither which completion
is canonical nor any Step 20/21 evidence. The post-generation shape gate
accepts exactly the correct full sentence or its exact `nicht vor`
counterpart; it never rewrites provider output into either form.

## Provision II exact-span and atomicity boundary

Provision II contains the legal cross-reference `unter I. genannten` and two
nominal coordinations. Step 23 preserves the whole provision as one exact
claim under two deliberately narrow rules:

- `I.`, `II.`, or `III.` followed by at least one horizontal space or tab and
  a lowercase continuation is a legal reference, not a sentence boundary;
- a newline or carriage return, `IV` or a later Roman numeral, or an uppercase
  continuation remains a boundary; and
- adjacent capitalized nominal tokens joined by `und`, `oder`, `sowie`,
  `and`, or `or` do not by themselves create clausal compoundness.

Consequently, `Ernennung und Entlassung` and `Beamtinnen und Beamten` remain
inside one atomic provision II claim. Genuine clausal coordination, such as
`Der Anspruch besteht und die Frist läuft.`, remains `COMPOUND` and cannot
receive a binary Step 23 verdict merely because one clause matches.

With those rules, the complete canonical provision II response receives one
full-span `SUPPORTS` link and no correction. The complete `nicht vor`
counterpart receives one full-span `REFUTES` link, one required correction,
and an exact full-sentence target. No derived paraphrase or grade fragment is
promoted into evidence.

## Exact evidence context, target projection, and Draft V2 input receipt

`build_evidence_bound_correction_context(...)` reconstructs and verifies:

- Packet Input Snapshot and Correction Packet hashes and mutual binding;
- one or two exact Step 20 bundles and all bundle item hashes;
- every packet citation and its original Step 23 evidence link;
- bundle ordinal, item and candidate identity, source, version, chunk,
  content hash, and relation; and
- the exact Unicode span and span hash selected by the link.

It returns an immutable `EvidenceBoundCorrectionContext` with at most 16
items and 16 KiB of exact excerpts. Items retain their original relation. In
the primary case, the provision III citation remains `REFUTES` because it
refutes the defective Draft V1 claim. If the provision II backup exposes the
exact `nicht vor` defect, its full-sentence citation likewise remains
`REFUTES`; the correct provision II completion produces no correction path.

`build_draft_v2_target_projection(...)` derives a typed target solely from the
verified Correction Packet and context. It selects unique exact `REFUTES`
excerpts in canonical order and renders one atomic line per excerpt, with the
packet citation marker before terminal punctuation. Corrections without an
exact replacement fact are recorded as omission-safe rather than being
invented. The projection requires at least one exact segment and forbids
headings, prefixes, paraphrases, additional text, and prohibited claims. It
binds its ordered segments, exact rendered output, expected-output SHA-256,
packet hash, and context hash in one projection hash.

`EvidenceBoundDraftV2Provider` accepts only the approved provider identity and
`draft-v2-generation-1a` purpose. Before any underlying provider call it
rebuilds the canonical target from the typed packet and context and requires
exact equality with the supplied projection. It verifies the base request and
canonical Correction Packet document, appends the bounded context and target,
and rejects changed or already-augmented input. After generation it requires
byte-exact equality with the expected whitelisted output; prefixed,
paraphrased, extended, or otherwise changed output fails closed.

For each forwarded request it creates an
`EvidenceBoundProviderInputReceipt` binding:

- base provider-request hash;
- actual augmented provider-request hash;
- evidence-context and Correction Packet hashes;
- Draft V2 target-projection hash;
- provider identity digest;
- augmented user-content SHA-256; and
- provider-response hash; and
- Draft V2 purpose.

The before/after trace binds both the target-projection and receipt hashes. The
original Step 25 request hash alone is never presented as proof of the
augmented bytes or generated output.

## Reconstructible corrected-evidence verification

Step 25 retains its ordinary semantic verifier as a non-authoritative signal.
That verifier cannot turn original `REFUTES` evidence into support.

A separate optional `CorrectedEvidenceVerifier` port is considered only when
all three conditions hold: deterministic evidence currently refutes the
claim, the claim satisfies a required correction, and it cites packet
evidence. Its typed request binds the target claim and Draft V2 hashes,
Correction Packet hash, exact claim text, satisfied correction IDs, and cited
citation IDs. Implementing that protocol is not proof authority.

The Step 38 `CanonicalEvidenceExactVerifier` validates that request against
`EvidenceBoundCorrectionContext`. It can construct a
`CorrectedEvidenceProof` only when the claim text without citation markers
equals an exact sentence within a cited `REFUTES` excerpt.

The proof is reconstructible rather than opaque. Its stable fields are:

- proof version, request hash, and Correction Packet hash;
- target claim ID, claim hash, and citation-stripped text SHA-256;
- satisfied correction IDs;
- packet citation ID and hash;
- evidence-context hash;
- the full original Step 23 `ClaimEvidenceLink`;
- evidence-span text SHA-256; and
- proof hash.

No additional raw evidence text is persisted in the proof. The nested link
retains offsets, source/version/chunk/content identities, original relation,
and span digest; the exact bytes remain in the bounded context that produced
the proof.

Step 25 independently revalidates the authority-relevant proof against its
own request, claim, and packet. It verifies the nested link hash and `REFUTES`
relation, exact packet citation/link identities, every satisfied correction's
exact `REFUTES` replacement fact, and equality of the citation-stripped target
SHA-256 and link span SHA-256. The evidence-context hash is cryptographically
bound audit lineage, not independent evidence authority. A `SUPPORTS` enum,
signal hash, protocol type, or proof SHA without the complete matching proof
cannot change the evidence result.

Only an independently revalidated proof can yield:

```text
evidence_binding_result=SUPPORTED
reason=CORRECTED_EVIDENCE_SUPPORTS
final_step25_verdict=VERIFIED_SUPPORTED
```

An unavailable port preserves legacy behavior. An uncertain, detached,
incomplete, tampered, non-reconstructible, or proof-less result fails closed
and cannot be rescued by ordinary semantic verification.

## Versioned Step 29 corrected-claim bridge

The legacy `PersonalMemoryPatchEvidenceReference` remains SUPPORTS-only and
its serialized form and established hashes remain unchanged.

When `step25_result` is supplied,
`build_personal_memory_patch_evidence_binding(...)` reconstructs the full
Draft V2 result, VERIFIED summary, Verified Answer references, correction
compliance, proposal text, owner/scope identity, citations, Step 20 items, and
Step 21 resolved-item lineage. It requires exactly one matching target claim,
`VERIFIED_SUPPORTED`, `evidence_binding_result=SUPPORTED`, and a complete
corrected-evidence proof that Step 25 independently revalidated. The
`LayeredClaimVerification` retains the full proof plus proof and signal hashes.

The resulting versioned
`PersonalMemoryCorrectedClaimEvidenceReference` keeps two kinds of lineage
explicit:

- source: original Draft V1 claim and assessment, original `REFUTES` relation,
  evidence-link hash, citation, and required correction; and
- target: corrected Draft V2 claim, Step 25 verification and result,
  corrected-evidence signal/proof hashes, Verified Answer claim reference,
  and target verdict. The full proof is reconstructed and validated from the
  supplied Step 25 result before this reference is accepted.

The original evidence-link and citation identities are retained unchanged;
they are not repointed to the Draft V2 claim or relabelled as `SUPPORTS`.
Unknown discriminators, changed relations, detached proofs, tampered hashes,
cross-owner data, or false target support are rejected during construction and
JSON round-trip verification.

## Personal Memory and downstream authorities

Step 38 uses Step 28 `KNOWLEDGE_KERNEL` candidate intake, then the unchanged
Step 29/30 lifecycle:

```text
DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL
         -> APPROVED -> COMMITTED -> ACTIVE
```

Only an authenticated owner action approves. Commit Helper cannot approve;
the browser cannot commit; the provider cannot mutate Personal Memory. Step
31 exposes only applicable ACTIVE patches and fixes
`canonical_evidence_authority=false`. Canonical evidence suppresses a
conflicting memory patch. The same patch may be selected for two allowed
provider-neutral model identities, but memory remains in Kernel persistence.

Steps 33-35 preserve their typed audit, reviewer, and owner-UI boundaries.
Step 37 recovery may replay the same authorized action but cannot widen
credentials, tenant/owner scope, or state-machine authority.

## Validation classification and closure evidence

The checked-in runner has two classifications:

- `--offline` runs deterministic fixtures and returns
  `PASS_OFFLINE_NOT_CLOSURE`, `closure_eligible=false`, and
  `real_model_run=false` semantics; and
- default live mode verifies the real Step 14-16 fixture and calls only the
  approved OpenRouter `moonshotai/kimi-k2` path, with tools, web, function
  calling, and code execution disabled.

The live runner owns one disposable CockroachDB v26.2.4 runtime and one
database. It binds primary Step 18-21 retrieval, the real Steps 22-26 model
lineage, later related retrieval, Steps 27-35 Personal Memory/audit/review/UI,
and the Step 37 recovery spot check to the same runtime and database digests.
Provider calls remain outside retried database transactions. It refuses
closure if any lineage detaches, cleanup is incomplete, or the Step 39
boundary scan detects an unexpected production bridge.

If the real primary Draft V1 has no exact correction, the same owned database
is reused for the provision II backup retrieval. A correct full-sentence
backup remains a truthful no-defect outcome; only the exact `nicht vor`
full-sentence response may continue as the declared backup correction.

No fake provider fallback or stitched component result is allowed. The
observed live result used the real primary case, returned
`PASS_LIVE_COHERENT_LINEAGE` with `closure_eligible=true`, reported the
coherent Personal Memory lineage as `PASS`, completed cleanup of the exact
owned disposable runtime, and passed the Step 39 boundary scan with zero
unexpected production-bridge hits. Its sanitized validation digest is
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
Final repository regression, evidence-file freeze, commit, push, and upstream
reachability remain mechanical closure-workflow gates. The closure commit is
`NOT CREATED`, push is `NOT PERFORMED`, and Step 39 remains `NOT STARTED`.
