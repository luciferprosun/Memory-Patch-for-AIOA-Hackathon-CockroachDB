# German Law Golden Cases 1A

## Status and purpose

This is the human-readable companion to
`tests/fixtures/step38_german_law_cases.json`, based on exact Step 37 closure
`9888070ab171fd057b17ab3057b3cf868cf704d2`.

It defines bounded regression expectations. It is not legal advice, a new
source of canonical evidence, a provider transcript, or Step 38 closure
evidence. The approved OpenRouter/Kimi live run selected
`primary-entry-into-force` and returned `PASS_LIVE_COHERENT_LINEAGE` with
`closure_eligible=true`; its coherent runtime passed and exact owned resources
were cleaned up. The sanitized validation digest is
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
The closure commit is `NOT CREATED`, push is `NOT PERFORMED`, and Step 39 is
`NOT STARTED`.

Questions remain fixture data. Production routing must not select an answer by
matching a question or case ID.

## Exact published fixture

The real cases use the Step 16 `PUBLISHED` BMJErnAnO
consolidated-reference item. The source is `AUTHORITATIVE_SECONDARY`; registry
publication does not turn the fixture or this document into a promulgation
instrument.

| Identity | Exact value |
| --- | --- |
| HAT | `german-law` version `1.0.0` |
| HAT manifest digest | `ab6ff572596993d63fdfd148207fcb4593f2672583f9d8dbedcc8f7e0f246109` |
| HAT scope | `german-law-global-1a` |
| source ID | `de-federal-gii-bjnr1330a0023` |
| official identifier | `BJNR1330A0023` |
| version identity | `legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95` |
| publication item digest | `14ad0905e935e57106d4445584207e51acc54214e50ab84f6e3e9c117eb861bf` |
| Step 14 manifest | `ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89` |
| Step 15 manifest | `7094358f7c9bb6acf62160484a017074da70361c73e4e5bbd7623f700414b125` |
| Step 16 manifest | `6871562b5b17d632c0e15169fefe7186f3fc7d7b5eb59f4140c367bf2c8a37e8` |

All three exact provisions are part of the bounded fixture:

| Provision | Bound content | UTF-8 SHA-256 |
| --- | --- | --- |
| `I.` | Exact appointment/dismissal delegation text, including `bis einschließlich A 15`. | `323c88960cc5eeca3e2d4b6c3c34630947f85ec82c75e1e398492a319bd13147` |
| `II.` | Exact special-case reservation text beginning `Für besondere Fälle`. | `6a12a5f19d7a4b61d71be5c5583d0a3a41b3111fcf00803892200fc42260d99e` |
| `III.` | Exact entry-into-force and prior-orders text shown below. | `fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1` |

Exact provision III bytes:

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum selben Gegenstand sind nicht mehr anzuwenden.
```

Exact provision II bytes used by the declared backup:

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten vor.
```

The Step 38 projection accepts only exact I/II/III bytes after source,
version, manifest, and provision hashes verify. It carries
`canonical_evidence_authority=false` and
`source_publication_authority=false`. No paraphrase may be stored under these
real identities.

## Typed temporal projection

Provision III contains the exact entry sentence, but the bounded upstream
fixture does not supply the structured `effective_from` field used by the
Step 38 integration proof. The adapter parses `1. Januar 2024` only after the
exact provision III hash verifies and emits a typed receipt with:

- `effective_from=2024-01-01T00:00:00Z`;
- `projection_method=FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE`;
- `fixture_bound=true`;
- `preexisting_temporal_metadata_used=false`;
- `model_inference_used=false`; and
- `canonical_evidence_authority=false`.

This does not manufacture a historical version. Questions before the proven
range remain synthetic fail-closed tests.

## Golden-case matrix

### Real corpus cases

| Case ID | Question | Expected result |
| --- | --- | --- |
| `primary-entry-into-force` | `Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am [Datum] in Kraft.“` | Route through German Law HAT, bind exact provision `III.`, derive the fixture-bound 2024 date, detect and correct a wrong entry date when the unchanged real Draft V1 exposes one, then emit only a fully verified answer. This is the Personal Memory branch. |
| `backup-special-case-reservation` | `Vervollständige nach Abschnitt II der BMJErnAnO den Satz, indem du „nicht“ einsetzt oder die Lücke leer lässt: „Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten ___ vor.“` | Bind exact provision `II.`. Accept only one complete filled sentence. The exact canonical sentence is correct and creates no defect; the exact sentence with `nicht` before `vor` is `REFUTES` and requires removal of the negation. Every other response shape fails closed. |
| `supported-entry-into-force-clean` | `Wann tritt diese Anordnung in Kraft?` | Bind exact provision `III.`, preserve the 2024 date, and avoid forcing an unnecessary correction or Personal Memory proposal. |

The deterministic primary lane deliberately uses this evidence-blind Draft V1
claim:

```text
Diese Anordnung tritt am 1. Januar 2025 in Kraft.
```

Its material defect is the wrong effective date. The accepted corrected claim
is the exact source sentence:

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft.
```

The correction condition is
`WRONG_EFFECTIVE_DATE_MUST_BE_REPLACED_WITH_EXACT_SOURCE_DATE`. The
deterministic Draft V1 is a synthetic test double and must never be presented
as a real model observation. The real OpenRouter/Kimi Draft V1 is captured
unchanged. If it contains no correctable date defect, the run uses the declared
backup rather than manufacturing an error.

### Declared provision II backup

The backup correction condition is
`SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED`. Its evidence-blind
provider prompt requires the entire filled sentence but does not disclose
which polarity is canonical. The unchanged Draft V1 must be exactly one of:

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten vor.
```

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten nicht vor.
```

The first is the correct full provision II sentence. It receives exact
`SUPPORTS`, produces no RequiredCorrection, and must not be relabelled as a
defect merely to keep the demo moving. The second is a material polarity
error. It receives one full-sentence `REFUTES` link, and the exact canonical
sentence is the only permitted correction target. A blank, `nicht` alone,
quotation marks, explanation, heading, paraphrase, prefix, suffix, or extra
sentence is `INVALID_FAIL_CLOSED`, not a selectable model defect.

| Exact Draft V1 shape | Classification | Consequence |
| --- | --- | --- |
| Complete canonical provision II sentence | `CORRECT_EXACT_NO_MATERIAL_DEFECT` | `SUPPORTED`; no correction or Personal Memory proposal is manufactured. |
| Complete sentence with `nicht` immediately before `vor` | `WRONG_EXACT_MATERIAL_DEFECT` | Full-span `REFUTES`; continue through the exact correction path. |
| Any other bytes | `INVALID_FAIL_CLOSED` | Stop with `STEP38_BACKUP_RESPONSE_SHAPE_INVALID` before Step 23 selection. |

The full-sentence proof depends on two bounded Step 23 language rules. In
`unter I. genannten`, Roman legal references `I` through `III` followed by
horizontal whitespace and lowercase continuation are not sentence endings;
newlines, `IV` and later numerals, and uppercase continuation remain endings.
Capitalized nominal coordination around `und`, `oder`, `sowie`, `and`, or
`or` is not clausal, so `Ernennung und Entlassung` and `Beamtinnen und
Beamten` remain within one atomic claim. A genuinely clausal sentence such as
`Der Anspruch besteht und die Frist läuft.` remains compound.

The related later question for this selected backup is:

```text
Ist die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten nach Abschnitt II der BMJErnAnO für besondere Fälle vorbehalten?
```

### Synthetic and negative cases

| Case ID | Classification | Required behavior |
| --- | --- | --- |
| `temporal-unavailable-edge` | `SYNTHETIC_EDGE_CASE` | An as-of question for 1900 returns `INSUFFICIENT` and `HUMAN_REVIEW_REQUIRED`; current text must not leak into a historical answer. |
| `conflicting-ceiling-edge` | `SYNTHETIC_EDGE_CASE` | Equal-rank synthetic A14/A15 evidence remains `CONFLICTING`; neither a model nor Personal Memory chooses a winner. |
| `non-german-law-route` | `SYNTHETIC_EDGE_CASE` | The weather query remains `PASS_THROUGH`; neither the German Law HAT nor a memory patch widens the route. |

Synthetic cases prove policy behavior only. They are never inserted into the
Step 16 corpus or counted as German Law evidence.

## Primary before/after proof

The deterministic correction path must preserve this lineage:

1. Draft V1 receives the original question only, with tools, web, function
   calls, and code execution disabled.
2. Step 23 identifies the exact defective claim. The exact provision III
   sentence retains its original `REFUTES` link to that Draft V1 claim.
3. Step 24 creates the immutable required correction, citation, packet, and
   integrity receipt.
4. Step 38 reconstructs `EvidenceBoundCorrectionContext` from the packet
   citation through the original Step 23 link, Step 20 item, and exact source
   span. No source paraphrase is introduced.
5. Step 38 derives `DraftV2TargetProjection` solely from that verified packet
   and context. It renders one atomic citation-bearing line for each unique
   exact `REFUTES` excerpt, records omission-safe corrections without exact
   facts, and binds the exact expected output. Headings, prefixes, paraphrases,
   extra text, and prohibited claims are outside the whitelist.
6. Only Draft V2 receives that context and target. The provider wrapper
   canonically rebuilds the projection from the typed packet and context before
   any call and requires byte-exact provider output. An
   `EvidenceBoundProviderInputReceipt` binds the base request, actual
   augmented request, context, packet, target-projection hash, provider
   identity, content digest, response hash, and purpose.
7. The corrected-evidence verifier produces a full reconstructible proof over
   the original link and its exact span digest, packet citation,
   evidence-context hash, target claim, and satisfied correction. It persists
   no extra raw evidence text. Step 25 independently revalidates the nested
   link, packet/correction facts, and target/span digest equality; a protocol
   response or opaque proof hash alone is insufficient.
8. Only then may Step 25 mark the exact corrected claim
   `VERIFIED_SUPPORTED`, and Step 26 may return `VERIFIED_ANSWER`.

The committed trace, if later authorized by a passing run, should retain
hashes and bounded status fields rather than raw provider text or private
Personal Memory content.

## Personal Memory branch

Only a genuinely verified correction proceeds:

1. Step 28 `KNOWLEDGE_KERNEL` submits a `DETECTED` candidate.
2. Step 29 proposes the exact corrected Draft V2 statement.
3. The versioned corrected-claim evidence reference preserves the original
   Draft V1 source claim, `REFUTES` relation, evidence link, citation, and
   exact span lineage. It separately binds the satisfied correction, target
   Draft V2 claim, Step 25 signal/proof hashes, and Verified Answer reference;
   the supplied Step 25 result contains the full proof that is revalidated
   before the reference is built.
4. The proposal cannot reach beyond `AWAITING_APPROVAL` without an
   authenticated owner action.
5. Step 30 produces separate approval, commit, and activation receipts.
6. Step 31 may return the same ACTIVE patch hash for two allowed model
   identities, but must deny another owner or model and suppress memory under
   a canonical conflict.
7. Audit, review, and UI expose only their existing scoped capabilities.

The original `REFUTES` link is never repointed to the Draft V2 claim or
relabeled as `SUPPORTS`. Personal Memory remains private, provider-neutral,
and non-canonical.

## Provider and current validation status

The only approved hosted identity for Step 38 is:

- provider `openrouter`;
- adapter `openrouter-chat-completions-step38-1a`;
- model and declared version `moonshotai/kimi-k2`;
- endpoint class `openrouter-public-chat-completions-v1`;
- API origin `https://openrouter.ai` and path
  `/api/v1/chat/completions`;
- configuration digest
  `52e163ebef09076c135bc7c0783917bc1515666456253a2a62b4a8822630e15e`;
- provider identity digest is taken from the sanitized controlled-validation
  result rather than predicted in this document;
- immutable model revision `false`; and
- tools, web, function calling, and code execution disabled.

The offline lane is only `PASS_OFFLINE_NOT_CLOSURE` and has no real-model or
closure authority. The observed live lane owned one disposable CockroachDB
runtime and one database for initial retrieval, Steps 22-26, later retrieval,
Personal Memory, audit, review, UI, and recovery lineage. It did not fall back
to a fake or stitch separate component outputs: it selected the real primary
case, returned `PASS_LIVE_COHERENT_LINEAGE` with `closure_eligible=true`,
reported the coherent proof as `PASS`, completed cleanup, and passed the Step
39 boundary scan with zero unexpected hits. The result is bound by digest
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.

This document remains a non-authoritative companion to the sanitized evidence.
It does not preclaim a closure commit or push: those remain `NOT CREATED` and
`NOT PERFORMED`, respectively.
