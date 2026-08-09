# Claim Extraction and Evidence Binding 1A

## Scope and immutable upstream boundary

Step 23 consumes exactly one verified Step 22 `DraftV1`, the frozen Step 20
Evidence Bundle universe used by Step 21, and the corresponding verified Step
21 `TemporalResolutionResult`. `ClaimBindingRequest` revalidates every nested
Draft, bundle, bundle-item, temporal-assessment, and result hash before claim
extraction begins. Request, tenant, user, route, selected HAT, manifest,
effective scope, original-query digest, Draft V1 hash, bundle hashes, and Step
21 result hash must agree exactly.

The service has no retrieval port. It cannot browse, query a source registry,
call a model, open a database, or add evidence. It binds only the immutable
evidence already admitted by Steps 20 and 21.

## Exact-span extraction

`exact_text_spans` performs bounded deterministic sentence, line, bullet, and
semicolon segmentation without rewriting Draft V1. Offsets use Unicode code
points, with an inclusive start and exclusive end, over the NFC Draft V1 text.
For every `ClaimRecord`:

```text
draft_text[start_offset:end_offset] == exact_claim_text
```

The exact text remains authoritative. A separate case-folded, whitespace-
canonical `normalized_match_text` exists only for fixed matching rules. Claim
IDs bind Draft V1 hash, offsets, and exact text. Consequently repeated words
at different locations remain different claims and any Draft or span change
creates a different claim ID and hash.

The closed V1 claim types are factual, temporal, legal norm, source assertion,
quantitative, relational, and non-factual. Atomicity is `ATOMIC`, `COMPOUND`,
or `NON_FACTUAL`. A safely indivisible conjunction is retained as exact text
and marked compound; Step 23 never invents propositions while splitting it.

## Evidence binding and candidate status

The deterministic policy has three deliberately conservative matching rules:

- normalized whole-assertion equality may create `SUPPORTS`;
- one explicit German/English negation with otherwise identical tokens may
  create `REFUTES`;
- bounded significant-token overlap creates diagnostic `RELATED_ONLY` only.

Similarity, Step 20 rank, vector distance, modality count, and model output do
not prove truth. Absence of evidence is not refutation. When no exact evidence
span is safe, no fabricated span is emitted. Every created link binds the
Step 20 bundle/item/candidate hashes, exact source/version/chunk/content
identity, authority and publication state, effective scope, citation, exact
excerpt span digest, and Step 21 temporal assessment hash.

Step 21 is a ceiling. Non-applicable or non-selected evidence becomes
`INSUFFICIENT`; stale evidence remains inspectable but cannot establish
unqualified support; source-authority assertions require official-primary
metadata. A material conflict preserves both supporting and refuting link
identities and yields `UNVERIFIED`, never an arbitrarily selected winner.
Compound and non-factual claims are also `UNVERIFIED` candidates.

`SUPPORTED`, `REFUTED`, and `UNVERIFIED` are explicitly Step 23 candidate
statuses. They are not the final Step 25 verifier verdict and create no answer,
approval, execution, publication, or memory authority.

## Frozen Step 24 input

`PacketInputSnapshot` canonically freezes:

- exact Draft V1 and upstream Step 20/21 identities;
- ordered `ClaimRecord` values;
- ordered `ClaimEvidenceLink` values;
- one `ClaimEvidenceAssessment` for every claim;
- the fixed processing-policy identity and snapshot hash.

Claims order by exact Draft span. Links order by claim, relation, authority,
temporal state, Step 20 ordinal, and immutable identity. Assessments order by
claim ID. Exact replay is byte/hash stable; changing any claim, link,
assessment, policy, or upstream identity produces a new snapshot hash.

The snapshot is not a Correction Packet. It has no required corrections,
prohibited claims, packet HMAC, correction prompt, or Draft V2 field.

## Persistence decision

Step 4 already has claim and final-verdict persistence slots, but its verdict
vocabulary represents later durable verification semantics. Step 23 does not
overload that table with candidate verdicts. The canonical output is the
immutable hash-bound packet-input snapshot, consumed by Step 24. Therefore no
database migration and no Step 23 persistence repository are added.

## Bounds and effects

The fixed policy permits at most 256 claims, 4,096 evidence links, and a 2 MiB
canonical snapshot. The ordinary suite and controlled validator are offline,
deterministic, and make zero network, provider, database, AWS, S3, approval,
execution, or source mutations.

## Step 24 boundary

Step 24 is NOT STARTED. It may derive canonical corrections, prohibited claims,
citations, policy fields, integrity/HMAC material, and Correction Packet
serialization from the frozen snapshot. Step 23 does not implement those
outputs and does not generate Draft V2.
