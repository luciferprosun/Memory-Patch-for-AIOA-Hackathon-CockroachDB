# Step 25 - Draft V2 Generation and Layered Claim Verifier 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 26: NOT STARTED.

## Starting identity

- Exact Step 24 baseline: `92e723d8e2770c9862f82c725c3418aed31b1f1b`
- Baseline subject: `feat(corrections): add correction packet integrity 1a`
- Branch: `main`
- Baseline tests: 1,562 passed
- Baseline focused Step 17-24/authority/tenant/persistence/serialization:
  568 passed
- Baseline contract validator and compileall: PASS

The final closure identity is the commit containing this record. No future
commit SHA is embedded before that commit exists.

## Packet gate and Draft V2 generation

Step 25 verifies the public Correction Packet hash, receipt hash,
domain-separated HMAC-SHA-256, key ID, exact Draft V1, request, tenant, user,
route, query, Step 21, and packet lineage before building any provider
request. Tampering fails before a model call.

The fixed `draft-v2-correction-packet-only-1a/1` template sends exactly Draft
V1 and the canonical verified packet through the existing pinned Step 22
text-only adapter. Tools, function calling, browsing, code execution,
retrieval, DB access, approval, and external action remain disabled. The
provider request is 24-KiB bounded, output is 64-KiB bounded, and at most one
transient retry is allowed. Exact response bytes, SHA-256, model/provider,
prompt, generation, timeout, retry, Draft V1, packet, and receipt identities
are bound into immutable Draft V2 contracts.

No database transaction spans the provider call. Draft V2 reuses the existing
Step 4 `drafts` table at stage 2, with Step 5 tenant/user RLS and immutable
replay. No migration is added. Existing claim-verdict persistence cannot
safely represent the richer layered vocabulary, so verification persistence
is explicitly deferred rather than overloaded.

## Layered verification

Step 23 exact Unicode code-point span extraction is reused for Draft V2.
Claim IDs bind exact spans and Draft V2 hash. Exact normalized alignment never
replaces the original text.

The verifier executes separate layers for:

1. schema, hashes, spans, lineage, and bounds;
2. required-correction and prohibited-claim compliance;
3. deterministic identifier, date, temporal, and source-authority facts;
4. exact packet citation and evidence relations;
5. bounded typed semantic candidate signals;
6. fixed final Step 25 claim-verdict aggregation.

Deterministic contradiction wins over semantic support. Unknown citations
fail. Conflicts remain `CONFLICTING`; semantic timeout or malformed output
remains unverified. Generation-model self-certification, model agreement,
Step 20 rank, modality count, and vector similarity create no authority.

Final claim verdicts are `VERIFIED_SUPPORTED`, `VERIFIED_REFUTED`,
`UNVERIFIED`, `CONFLICTING`, and `INVALID`. The hash-bound summary reports
`VERIFIED`, `FAILED`, `INCOMPLETE`, or `CONFLICTING`. It is not a final-answer
decision.

## Validation

- Step 25 focused suite: 27/27 PASS.
- Full repository suite: 1,589/1,589 PASS.
- Step 17-25, authority, tenant, CockroachDB persistence and serialization
  focused regressions: 576/576 PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled offline Step 25 validation and committed-evidence replay: PASS.

Sanitized evidence is committed at
`docs/evidence/modeling/step25-draft-v2-layered-verifier-validation.json`.
It binds the exact Step 24 validation digest, Draft V1, packet and receipt,
Draft V2, provider/prompt identities, layer matrix, summary hashes, replay,
tamper negatives, and resource/effect bounds without secret values or raw
corpus content.

The canonical real provider remains the Step 22 Moonshot declared-version
model. The established paid endpoint quota boundary made a real Step 25 run
unavailable and closure does not fabricate one. Deterministic fake generation
and verification prove the provider-neutral contract, exact bytes, failure
precedence, and authority boundary.

## Authority, isolation, and limitations

Tenant, user, route, HAT/scope through the packet, Draft V1, packet, receipt,
citations, source authority, and temporal facts fail closed when detached. A
model or semantic verifier cannot approve, execute, activate Personal Memory,
change route/policy, select a HAT, upgrade authority, or rewrite temporal
facts.

Controlled validation performs zero network, retrieval, web, database, AWS,
S3, approval, execution, and Personal Memory operations. It starts no external
runtime, so cleanup is `NOT_REQUIRED`.

Known limitations are explicit: the hosted model has a declared version, not
an immutable revision; large packets over the fixed provider-input bound fail
closed; the packet contains immutable evidence identities rather than full
corpus text; and durable layered-verification storage awaits a coordinated
schema review.

## Step 26 handoff

Step 26 may consume exact `DraftV2`, its hash, ordered
`LayeredClaimVerification` records, `DraftV2VerificationSummary`, Correction
Packet hash, upstream evidence status, temporal/conflict state, and the
HAT-enforcement policy. It may decide whether verified Draft V2 can be
returned, whether canonical retry is allowed, or whether output must fail
closed.

Step 26 remains NOT STARTED. No `VerifiedAnswer`, user-facing final assembly,
retry-once output policy, human-review flow, Personal Memory proposal,
approval, or commit helper is present in Step 25.
