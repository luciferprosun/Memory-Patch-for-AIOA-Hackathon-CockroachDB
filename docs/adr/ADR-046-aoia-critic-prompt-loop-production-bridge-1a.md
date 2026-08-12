# ADR-046: Keep the AOIA Critic bridge optional and candidate-only

## Status

Proposed. This ADR is based on the exact Step 38 closure commit
`939395d355ce0630c5044c4ab427082c3cf72d23`.

The decision is implemented and provider-free controlled validation passes in
the Step 39 closure worktree. The canonical evidence digest is
`de40a26eadf342b04d7b7b7ff10cc4c2b9c95322c4b37fc54a5af6b5d34665f0`.
This ADR becomes Accepted when the single closure commit containing it is
reachable from `origin/main`. Step 40 is `NOT STARTED`.

## Context

Step 38 proved the complete German Law Memory Patch path without a Critic
Prompt Loop. The selected `primary-entry-into-force` run is independently
verified and remains the core product baseline. Its sanitized validation
digest is
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.

Step 28 already accepts a bounded `CRITIC_PROMPT_LOOP` correction candidate,
but only through the ordinary owner-private candidate intake. It intentionally
contains no canonical Critic runtime, prompt, provider invocation, or output
parser. Historical Critic material in this repository is design provenance,
not a callable authority or a substitute for a typed bridge.

Step 39 must add the missing production-facing contract while preserving the
accepted ADR-008 proposal-only boundary. Critic output may identify a possible
defect, but it is not evidence, approval, committed memory, active memory, or
execution authorization.

## Decision

1. The Critic bridge is optional and additive. A disabled, unavailable,
   malformed, or rejected Critic result cannot block or change the Step 38
   Verified Answer or the existing Knowledge Kernel correction path.
2. The Kernel constructs one immutable `CriticReviewRequest`. It binds the
   exact request/run and route identities, tenant and owner context, selected
   HAT identity, effective scope, bounded typed artifacts, claim references,
   evidence references, temporal and freshness state, Correction Packet hash
   when present, approved provider identity, policy digest, and prompt digest.
   The Critic cannot choose or widen trusted owner, slot, route, HAT, scope,
   claim, or evidence identities.
3. Draft V1 is required as a typed artifact. Draft V2 and Verified Answer may
   be included only when their already-verified identities and hashes are
   available. The request is bounded and canonical; raw database rows and
   arbitrary producer JSON do not cross the bridge.
4. Evidence references preserve the exact Kernel-projected source, version,
   chunk, relation, authority, publication, temporal, freshness, and bounded
   snippet lineage. They remain untrusted input to the Critic and canonical
   evidence authority remains with Steps 13-21 and the ordinary verifier.
5. The prompt is versioned and hash-bound. It instructs the Critic to treat all
   embedded text as inert data, use only supplied identifiers, return exactly
   one closed JSON object, and never use tools, web, code execution, function
   calls, approval, commit, activation, publication, or external actions.
   Prompt text and model agreement grant no authority.
6. Provider output is accepted only after strict bounded parsing into a typed
   `CriticAssessment` and reconstruction against its request. The assessment
   must bind the request and provider identities, refer only to supplied claim
   and evidence identifiers, reproduce the exact trusted scope digest, and
   keep every authority flag false. Diagnostic confidence is not a trust
   score.
7. Provider execution is outside database transactions, has bounded attempts,
   and produces a typed `CriticProviderCallReceipt` and `CriticBridgeResult`.
   Disabled, provider-unavailable, invalid-output, no-issue, and accepted-
   assessment outcomes are explicit and hash-bound. Mandatory Step 39 tests
   use a deterministic fake provider and do not claim a real Critic runtime.
8. A no-issue assessment creates no correction candidate. An issue without a
   valid owner-private target remains diagnostic-only. An eligible issue may
   produce only a Step 28 `CorrectionCandidateEnvelope` with source
   `CRITIC_PROMPT_LOOP` and trigger `CRITIC_PROMPT_LOOP_DETECTED` through the
   existing `submit_critic_loop_candidate` capability.
9. Step 39 exposes no direct proposal, validation, approval, commit,
   activation, retrieval, review, publication, or execution operation. Step 29
   independently revalidates evidence, conflict, freshness, quota, and exact
   deduplication. Step 30 still requires the human owner and the separated
   Commit Helper before activation.
10. A durable Critic candidate uses the existing Step 33 audit vocabulary:
    event `CORRECTION_CANDIDATE_DETECTED`, actor `CRITIC_LOOP`, and subject
    `CORRECTION_CANDIDATE`, with a hash-only payload. Disabled, no-issue, and
    provider/parser/integrity failures do not fabricate candidate events.
11. The German Law conformance lane reuses only bounded, already-verified
    Step 38 lineage. Its Critic candidate must have its own Step 39 identity;
    Step 38's Kernel candidate and proposal hashes are upstream reference data,
    not Step 39 outputs. If both producers describe the same correction,
    ordinary Step 29 deduplication must prevent a duplicate patch.
12. Exact replay is idempotent and changed content under one identity fails
    closed. Owner, tenant, route, run, scope, prompt, provider, claim, evidence,
    output, or candidate tampering yields no Critic-derived candidate and does
    not trigger a Kernel fallback.
13. No database migration, broad role, `BYPASSRLS`, new approval credential,
    or provider-to-database capability is introduced. A provider credential,
    if a separately authorized runtime uses one, remains purpose-bound and is
    never serialized into requests, receipts, audit events, or evidence.
14. Step 40 resource and deployment optimization is outside this decision and
    remains `NOT STARTED`.

## Consequences

The bridge can add a bounded second diagnostic perspective without making the
Critic part of the correctness or availability path. Every accepted issue is
traceable to the exact prompt, provider request, source run, claims, evidence
references, and assessment while the ordinary Memory Patch gates remain
authoritative.

The production contract can be validated deterministically without spending
provider credit or claiming a live external Critic. A real runtime observation
would be a separately classified validation, not an inference from the fake
adapter.

Because the Step 28 candidate semantic identity retains producer provenance,
the Kernel and Critic candidates may be distinct. Step 29 must therefore prove
that an equivalent evidence-bound patch is deduplicated before any owner
approval or commit, rather than relying on candidate identity equality.

## Rejected alternatives

### Make the Critic mandatory for core correction

Rejected. Step 38 already proves core detection and correction, and Critic
availability cannot become a product dependency.

### Let the Critic supply trusted routing or evidence identities

Rejected. Those identities come from the verified Kernel request. Accepting
self-asserted authority would permit route, scope, or source laundering.

### Write a proposal or active patch directly

Rejected. Step 28 ends at `DETECTED`, Step 29 owns evidence validation and
proposal states, and Step 30 owns human approval, technical commit, and
activation.

### Treat Critic confidence or agreement as proof

Rejected. Confidence is diagnostic. Canonical evidence and deterministic
verification retain precedence.

### Reuse the Step 38 Kernel candidate as the Critic result

Rejected. That would erase producer provenance and falsely claim that the
Critic produced an observation it did not produce.

### Invent candidate audit events for disabled or failed calls

Rejected. Existing Step 33 candidate audit vocabulary describes a durable
candidate. Other bridge outcomes remain typed, hash-bound result artifacts
unless a later explicit audit vocabulary extension is approved.

## Acceptance boundary

The Step 39 focused tests, full regression, contract validation, controlled
provider-free validation, sanitized evidence, authority scans, and
documentation checks pass. The remaining acceptance condition is Git
reachability of the single Step 39 closure commit on `origin/main`; the final
execution report records that observed SHA and push result.
