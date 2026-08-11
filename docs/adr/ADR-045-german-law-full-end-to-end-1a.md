# ADR-045: Compose German Law correction with exact evidence lineage

## Status

Proposed. This ADR becomes accepted only when a Step 38 closure commit is
reachable on `origin/main`. It is based on Step 37 closure
`9888070ab171fd057b17ab3057b3cf868cf704d2`.

The approved OpenRouter `moonshotai/kimi-k2` lane has now returned
`PASS_LIVE_COHERENT_LINEAGE` with `closure_eligible=true` on
`primary-entry-into-force`. The same live observation completed the coherent
disposable-database lineage, Step 39 boundary scan with zero unexpected hits,
and owned-runtime cleanup. The sanitized validation digest is
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
The closure commit is `NOT CREATED`, push is `NOT PERFORMED`, and Step 39 is
`NOT STARTED`.

## Context

Steps 13-37 provide the German Law HAT, published corpus, scoped retrieval,
temporal policy, provider-neutral drafts, claim and correction contracts,
verified output, owner-private Personal Memory, audit, review, UI, credential
separation, and recovery. Step 38 must compose those authorities without
turning fixture code, a model, a semantic signal, Personal Memory, or the UI
into a new authority.

The Step 16 fixture contains one published BMJErnAnO version with three exact
provisions. Provisions II and III are part of the same verified source item:

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten vor.
```

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum selben Gegenstand sind nicht mehr anzuwenden.
```

Its UTF-8 SHA-256 is
`fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1`.
The existing upstream temporal metadata does not itself expose the structured
effective date needed by this bounded proof. The date must therefore be
projected explicitly from verified fixture bytes, not inferred by a model or
silently attributed to pre-existing metadata.

A second integration problem appears when the Step 23 source claim is
`REFUTED`, a Step 24 correction is satisfied, and Draft V2 states the exact
corrected evidence. Relabelling the original Step 23 evidence link as
`SUPPORTS` or repointing it to a Draft V2 claim would corrupt lineage. The
correction needs its own deterministic proof while retaining the original
refutation unchanged.

Finally, the actual provider input is augmented only for Draft V2. The
augmentation needs a receipt so the evidence presented to the provider is
bound to the base request, packet, context, and approved provider identity.

## Decision

1. Use the exact Step 16 BMJErnAnO provisions I, II, and III. The real primary
   case asks the model to complete only the entry-into-force date in provision
   III: `Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am
   [Datum] in Kraft.“` The declared backup is
   `backup-special-case-reservation` and binds provision II. Its exact question
   is: `Vervollständige nach Abschnitt II der BMJErnAnO den Satz, indem du
   „nicht“ einsetzt oder die Lücke leer lässt: „Für besondere Fälle behalte ich
   mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und
   Beamten ___ vor.“` Its correction condition is
   `SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED`. The already-supported
   case asks when the order enters into force.
2. Preserve exact source bytes. A projection may contain the verified
   provision text, but it may not attach a paraphrase to the real source or
   claim source-publication or canonical-evidence authority.
3. Derive `effective_from=2024-01-01T00:00:00Z` through a typed,
   fixture-bound temporal projection receipt. It verifies the provision III
   hash and exact entry sentence, records
   `FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE`, and fixes both
   `preexisting_temporal_metadata_used=false` and
   `model_inference_used=false`.
4. Keep Draft V1 evidence blind. The deterministic primary test double emits
   `Diese Anordnung tritt am 1. Januar 2025 in Kraft.` only to exercise the
   wrong-date correction branch; it is always labelled synthetic. A real
   OpenRouter/Kimi answer is captured unchanged. It must independently expose
   a correctable date defect, otherwise the bounded run uses the declared
   backup rather than manufacturing an error. For that backup, accept only the
   complete exact provision II sentence or its complete single-negation
   counterpart with `nicht` immediately before `vor`. The exact canonical
   sentence is a correct response and therefore creates no defect or
   correction. The exact `nicht vor` sentence is a material polarity defect:
   Step 23 binds the full provision II sentence as `REFUTES`, and Step 24 must
   remove that negation. A placeholder, one-word answer, paraphrase, prefix,
   suffix, quotation, or extra sentence fails the response-shape gate before
   it can be selected as a defect.

   Preserve provision II as one exact claim span. A period after legal Roman
   references `I`, `II`, or `III` is not a sentence boundary only when at
   least one horizontal space or tab and a lowercase continuation follow;
   newlines, `IV` and later numerals, and uppercase continuations remain
   boundaries. Before compound detection, capitalized nominal coordination
   around `und`, `oder`, `sowie`, `and`, or `or` is non-clausal. Thus
   `Ernennung und Entlassung` and `Beamtinnen und Beamten` do not split or
   downgrade the provision II claim, while `Der Anspruch besteht und die
   Frist läuft.` remains compound and fail-closed.
5. Build `EvidenceBoundCorrectionContext` only after reconstructing the
   Packet Input Snapshot, Correction Packet, Step 20 bundles, citations,
   original Step 23 evidence links, bundle items, identities, relations, and
   exact Unicode spans. The context is bounded to 16 items and 16 KiB and
   explicitly has no canonical-evidence or final-answer authority.
6. Derive a typed `DraftV2TargetProjection` solely from the verified
   Correction Packet and `EvidenceBoundCorrectionContext`. It contains one
   atomic citation-bearing line per unique exact `REFUTES` excerpt, records
   omission-safe corrections that have no exact replacement fact, forbids
   headings, prefixes, paraphrases, and additional text, and binds both the
   projection hash and exact expected-output SHA-256. At least one exact
   `REFUTES` segment is required.
7. Give that context and target projection only to a
   `draft-v2-generation-1a` request through
   `EvidenceBoundDraftV2Provider`. Before any provider call, rebuild the
   canonical target from the typed packet and context and require exact object
   equality. Reject a changed provider, packet, target, request hash, wrong
   purpose, or already-augmented request. After generation, require byte-exact
   equality with the whitelisted target output.
8. Emit `EvidenceBoundProviderInputReceipt` for the actual augmented request.
   It binds the base and augmented provider-request hashes, evidence-context
   hash, Correction Packet hash, Draft V2 target-projection hash, provider
   identity digest, augmented user content SHA-256, provider-response hash,
   and Draft V2 purpose.
9. Add a dedicated `CorrectedEvidenceVerifier` port to Step 25. It is
   separate from the ordinary semantic verifier and is called only for a
   claim with original `REFUTED` evidence, a satisfied required correction,
   and cited evidence. Implementing the protocol does not by itself make its
   result trusted.
10. The verifier must return a full, typed, reconstructible
   `CorrectedEvidenceProof`, not only `SUPPORTS` and an opaque proof hash. The
   proof carries its version, request and packet hashes, target claim
   ID/hash/text digest, satisfied correction IDs, packet citation ID/hash,
   evidence-context hash, full original Step 23 `ClaimEvidenceLink`, evidence
   span digest, and proof hash. It persists no additional raw evidence text.
11. Step 25 independently reconstructs the authority-relevant proof against
    its own request, claim and packet. It verifies the nested link hash and
    `REFUTES` relation, packet citation/link identities, every satisfied
    correction's exact `REFUTES` replacement fact, and equality of the
    citation-stripped target digest and original link span digest. The context
    hash remains cryptographically bound audit lineage; it grants no authority
    by itself. Any changed or missing field, detached reference, non-exact
    text, or proof-hash mismatch is invalid.
12. Ordinary/model semantic verification cannot override a refutation. Only
    an independently revalidated corrected-evidence proof may change the
    corrected claim's Step 25 evidence result to `SUPPORTED` and produce
    `CORRECTED_EVIDENCE_SUPPORTS` plus `VERIFIED_SUPPORTED`. Uncertain,
    detached, opaque, or invalid results fail closed.
13. Extend Step 29 with a versioned
    `PersonalMemoryCorrectedClaimEvidenceReference`. It preserves the exact
    original source claim, `REFUTES` relation, evidence-link hash, and
    citation. Separately it binds the required correction and compliance,
    target Draft V2 claim, Step 25 verification, corrected-evidence signal and
    proof hashes, Verified Answer reference, and unchanged Step 20/21
    lineage. The supplied Step 25 result retains the full proof for
    reconstruction.
14. Keep the legacy Step 29 reference and hashes unchanged when
    `step25_result=None`. The versioned corrected path requires complete
    Step 25 reconstruction, one proposal-matching target claim,
    `VERIFIED_SUPPORTED`, a valid corrected-evidence proof, exact owner/scope
    lineage, and the retained source refutation.
15. Continue to use only the Step 28 `KNOWLEDGE_KERNEL` producer. Owner
    approval, Commit Helper commit, activation, audit, review, UI, and
    recovery keep their existing authority boundaries.
16. Run the live closure topology on one owned disposable CockroachDB runtime
    and one database. Primary retrieval, later related retrieval, verified
    upstream lineage, Personal Memory proposal through activation, audit,
    review, UI projection, and the recovery spot check must bind the same
    runtime and database identities. Do not stitch separate component runs
    into a closure claim.
17. Do not claim Step 38 closure from offline fixtures or component-only
    validators. Closure requires a successful approved OpenRouter/Kimi
    observation and the coherent same-database E2E identity.

## Consequences

The primary deterministic lane can prove a genuine transition from the wrong
2025 entry date to the exact provision III 2024 sentence without source
laundering or evidence-link relabelling. The provider input and exact target
projection are hash-bound, provider output outside that whitelist fails
closed, and Step 25 independently reconstructs the complete correction proof
rather than trusting an opaque signal. Step 29 can persist the corrected
statement without pretending the source link always supported the defective
Draft V1 claim.

The declared provision II backup is polarity-bounded rather than
grade-fragment-based. Its strict full-sentence gate distinguishes a correct
answer, which produces no correction, from the exact `nicht vor` defect,
which produces a full-span `REFUTES` relation and an exact correction target.
The Roman-reference and nominal-coordination rules are deliberately narrow;
they do not turn arbitrary abbreviations or clausal coordination into atomic
claims.

Legacy Step 25 and Step 29 behavior remains available when the new optional
ports or result are absent. The additional contracts increase integration
surface, but every new object is immutable, typed, bounded, and non-authority.

The temporal projection proves only the exact fixture date parse. It does not
invent a historical version or make the projection a canonical source.
Historical gaps and conflicts remain fail-closed or review-required cases.

## Rejected alternatives

### Keep the broader prior-orders case as the primary

Rejected. The date-completion question bounds Draft V1 to one natural atomic
claim and provision III supplies the exact replacement date. The prior-orders
sentence remains part of the verified source. The provision II reservation
case is the declared backup if the real primary response has no correctable
defect.

### Keep the provision I A15 grade-only backup

Rejected. A grade fragment can be semantically wrong yet lack the exact
full-span Step 23 evidence binding needed by the strict `REFUTES`-only Draft V2
target. The provision II polarity pair supplies two complete, bounded
sentences: the canonical sentence is safely recognized as no defect, while
the single-negation counterpart is exactly refuted without paraphrasing or
source laundering.

### Trust an internally self-consistent target projection

Rejected. A forged projection could be internally hash-consistent yet detached
from the actual packet or context. The provider wrapper must rebuild the
canonical projection from the typed inputs before the network boundary and
must not call the provider on mismatch.

### Paraphrase source text inside a real Step 20 identity

Rejected as source laundering. Real identities bind only their exact bytes.

### Relabel or repoint the original Step 23 link

Rejected because a link that refuted Draft V1 must remain a refutation of that
source claim. Target support is a separate Step 25 proof.

### Let the ordinary semantic verifier override `REFUTES`

Rejected because model output is not evidence authority. The corrected-
evidence port must return a full proof over exact cited bytes, and Step 25
must independently revalidate every bound identity and hash.

### Treat the temporal projection as pre-existing metadata or model knowledge

Rejected. Its receipt explicitly identifies a fixture-bound parse and records
that neither source was used.

### Send evidence to Draft V1

Rejected because it would invalidate the before/after correction proof.

### Report offline or HTTP `429` validation as closure

Rejected. Offline validation is `PASS_OFFLINE_NOT_CLOSURE`; HTTP `429` is a
blocked availability observation. Neither is a successful real-model E2E.

### Start the Critic production bridge

Rejected because it belongs to Step 39. Step 39 remains `NOT STARTED`.

## Acceptance boundary

The real-provider, coherent-runtime, cleanup, and Step 39 boundary decisions
in this ADR are validated by live digest
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
The ADR remains Proposed until the single Step 38 closure commit is reachable
on `origin/main`; no commit SHA or push reachability is invented here.
