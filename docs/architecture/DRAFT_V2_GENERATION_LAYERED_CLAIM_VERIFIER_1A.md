# Draft V2 Generation and Layered Claim Verifier 1A

## Scope and integrity gate

Step 25 consumes the exact Step 22 `DraftV1`, Step 24
`CorrectionPacketV1A`, and its `CorrectionPacketIntegrityReceipt`. Before a
provider-capable request exists, the Kernel recomputes the packet and receipt
hashes, verifies the domain-separated HMAC-SHA-256 receipt with the injected
Kernel authenticator, and checks request, tenant, user, route, query, Draft V1,
Step 21, and packet lineage. A failed public hash, HMAC, key ID, or lineage
check fails closed and produces no model call.

The HMAC capability remains Kernel-side. Key material is neither serialized
nor passed to the provider. The Step 25 request binds only the receipt hash,
not secret material.

## Draft V2 generation

The additive `ProviderTextRequest` reuses the exact pinned Step 22 provider
identity and adapter. It does not alter the genuine evidence-blind
`ProviderCallRequest` used by Draft V1. The Draft V2 projection contains:

- exact Draft V1 text and hash;
- exact repository-canonical Correction Packet JSON;
- one fixed instruction to return corrected inert text.

The system template is versioned as
`draft-v2-correction-packet-only-1a/1`. It requires supported-claim
preservation, required corrections, prohibited-claim avoidance, explicit
conflict and uncertainty preservation, temporal and source-authority limits,
and packet-only citation IDs. Tools, function calling, browsing, code
execution, retrieval, database access, approval, and external action remain
disabled.

The provider input is bounded to 24 KiB. A packet that does not fit fails
closed rather than being silently truncated. Output is bounded to 64 KiB and
1,024 requested output tokens. The fixed retry policy permits two attempts
with one bounded transient retry. Authentication, invalid request, policy,
identity, and response-contract failures are not converted into an unbounded
retry.

`DraftV2GenerationRequest`, `DraftV2GenerationResult`, and `DraftV2` are
immutable and hash-bound. Exact UTF-8 output text is preserved. Its byte
length and SHA-256 are semantic identity; latency and wall-clock creation time
are operational metadata and do not change the semantic Draft V2 hash.

## Transaction and persistence boundary

The required sequence is:

```text
verify immutable inputs and HMAC
-> close reads
-> assert no open persistence transaction
-> bounded provider call
-> short immutable Draft V2 write
-> pure layered verification
```

No provider call can occur inside the repository persistence transaction
context. Step 4 already defined `memory_patch.drafts` with stages 1 and 2, so
Step 25 adds no migration and uses stage 2 through `CockroachDraftV2Store`.
The Step 5 user-private RLS context and exact tenant/user/run identity are
reused. Exact replay verifies and reuses the immutable row; conflicting text
or lineage fails closed.

The existing Step 4 claim-verdict vocabulary represents an earlier final
verdict family and cannot safely encode the richer Step 25 layer results.
Layered-verification persistence is therefore explicitly deferred instead of
overloading that table or creating a competing schema. Draft V2 persistence
is implemented; verification output remains an immutable in-memory/audit
artifact for this step.

## Claim extraction and alignment

Draft V2 never becomes trusted merely because it was generated. Step 25
reuses Step 23's deterministic Unicode-NFC exact-span parser and closed claim
classification. Offsets remain Unicode code-point offsets, start-inclusive
and end-exclusive. Each `DraftV2ClaimRecord` binds the Draft V2 hash, exact
span and text, separate normalized matching form, type, atomicity, packet
scope, cited packet IDs, and exact normalized alignment to Draft V1 claims.

Citation markup uses `[citation:CITATION_ID]`. Removing this markup is allowed
only for deterministic matching; it never rewrites the exact authoritative
Draft V2 span.

## Verification layers

Every factual claim passes ordered, typed layers.

### Layer 0: contract and schema

Draft, packet, receipt, lineage, text bounds, claim spans, IDs, hashes, and
scope are verified. Integrity failure yields `INVALID` and cannot reach a
provider or later trusted output.

### Layer 1: packet compliance

Fixed rules check whether each required correction is satisfied, partially
satisfied, absent, or not applicable. Exact prohibited claims are detected
without a model. Removal, qualification, required citation use, temporal
qualification, and source-authority qualification use closed Step 24 action
types. The model's assertion that it complied has no effect.

### Layer 2: deterministic facts, dates, and sources

Identifiers and explicit dates in Draft V2 are compared with the bounded
packet text/metadata universe. Unknown dates or statute/section identities
fail. Temporal assertions reuse Step 21 limitations and Step 24 temporal
prohibitions. Current-certainty language cannot override `CONFLICTING`,
`STALE`, `INSUFFICIENT`, or `UNAVAILABLE` evidence. Claims that call a source
official or primary require packet citations whose immutable authority is
`OFFICIAL_PRIMARY`.

Operational retrieval or verification times never substitute for legal
effect time. Source authority is a hard fact, not a score.

### Layer 3: citations and evidence binding

Every cited ID must occur in the verified packet. Its candidate,
source/version/chunk, content, publication, authority, temporal-assessment,
and relation identities were already frozen by Steps 20-24. Unknown or
detached citation IDs fail. Supporting and refuting relations are kept
separate; both sides or an upstream conflict produce `CONFLICTING`. Vector
similarity and Step 20 rank never establish truth.

### Layer 4: semantic candidate signal

Only a claim that deterministic rules cannot settle needs semantic checking.
`SemanticVerifierRequest` contains one bounded claim, allowed packet citation
IDs, and bounded evidence-identity context. The verifier returns strict typed
JSON: `SUPPORTS`, `REFUTES`, or `UNCERTAIN` plus a subset of supplied citation
IDs. Invented citations, malformed output, timeout, or unavailable runtime
become `INVALID`, `UNAVAILABLE`, or uncertainty, never success.

The provider-backed verifier reuses the Step 22 text-only adapter and fixed
timeout/retry limits. The offline deterministic fake is used by ordinary
tests and controlled validation. In both cases the signal is candidate data
only.

### Layer 5: deterministic aggregation

The fixed precedence is:

1. schema or citation failure is `INVALID`;
2. deterministic fact, date, source, packet, or evidence contradiction wins
   over any semantic support signal;
3. material conflict remains `CONFLICTING`;
4. exact packet-supported alignment or packet support plus bounded semantic
   support can be `VERIFIED_SUPPORTED`;
5. unresolved or unavailable semantic support is `UNVERIFIED`.

Non-factual text is retained as non-evidence-requiring inert content. No
generation-model self-certification or agreement between generation and
verification roles changes the rules.

## Summary and status

`LayeredClaimVerification` records every layer, semantic signal, final Step 25
claim verdict, reasons, limitations, and hash. The final closed claim family
is `VERIFIED_SUPPORTED`, `VERIFIED_REFUTED`, `UNVERIFIED`, `CONFLICTING`, and
`INVALID`.

`DraftV2VerificationSummary` binds ordered verification hashes, correction
compliance, prohibition violations, citation failures, counts, and one of:

- `VERIFIED`: all required corrections are satisfied and all required factual
  claims are supported;
- `FAILED`: a required correction, prohibition, deterministic contradiction,
  invalid citation, or integrity requirement failed;
- `INCOMPLETE`: required semantic support remains unavailable or uncertain;
- `CONFLICTING`: a material conflict remains preserved.

These are Step 25 verification states, not an answer decision.

## Authority, limitations, and Step 26 boundary

Step 25 performs no new retrieval, web access, AWS/S3 mutation, approval,
commit, execution, or memory activation. Provider/model output cannot alter
tenant, user, route, HAT, scope, evidence status, temporal facts, source
authority, citation identity, or packet policy.

The canonical hosted Moonshot model remains a declared-version rather than an
immutable revision, as documented by Step 22. Controlled Step 25 validation
uses deterministic fake generation and verification. A paid real-provider
call was unavailable at the established Step 22 quota boundary and is not
fabricated as successful.

Step 26 is NOT STARTED. Step 25 does not define a `VerifiedAnswer`, assemble a
user-facing final response, retry final output, create human-review workflow,
or propose Personal Memory. Step 26 may consume the immutable Draft V2,
ordered claim verifications, summary, packet hash, evidence status, and
HAT-enforcement policy to make that separate fail-closed output decision.
