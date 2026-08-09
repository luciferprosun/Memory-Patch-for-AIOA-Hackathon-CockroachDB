# Verified Answer Assembly and Fail-Closed Output 1A

## Boundary

Step 26 is the final answer-output policy boundary. It consumes the exact
Step 17 route and final policy result, every Step 20 outcome used by Step 21,
the Step 21 temporal result, Draft V1, the Step 24 Correction Packet and HMAC
receipt, and the complete Step 25 Draft V2 pipeline result. It performs no
retrieval and does not change any upstream fact, scope, policy, or identity.

`FinalAnswerRequest` re-verifies every public canonical hash and the exact
request, tenant, user, route, selected HAT/version/manifest, effective scope,
Evidence Bundle, temporal, Draft V1, packet, receipt, Draft V2, claim, and
summary bindings. The service additionally verifies the packet's
HMAC-SHA-256 receipt with an injected Kernel-side authenticator before an
answer or retry can proceed. A mismatch produces no answer.

The final Step 17 policy result is a separate hash-bound decision evaluated
against Step 21's final evidence status. Step 20's earlier policy-result hash
remains preserved inside its immutable bundle; Step 26 does not rewrite it.
Execution authorization is copied as metadata only and remains independent:
`ALLOW_ANSWER` never implies `ALLOW_EXECUTION`.

## Eligibility policy

The fixed `verified-answer-fail-closed-output-1a/1` policy allows a normal
answer only when all of these are true:

- route, policy, packet, evidence, temporal, Draft V2, claims, and summary
  integrity is valid;
- the knowledge policy is `ALLOW_ANSWER`;
- final evidence is `SUFFICIENT`;
- Step 25 summary is `VERIFIED`;
- all claims are `VERIFIED_SUPPORTED`;
- no required correction is unsatisfied;
- no prohibited claim, invalid citation, deterministic contradiction,
  unverified claim, invalid claim, or unresolved conflict remains.

`BLOCK_ANSWER` always produces a bounded failure. `REQUIRE_CONFIRMATION`
produces a typed review handoff and is never upgraded. `AMBIGUOUS` and
non-authorized `PASS_THROUGH` paths do not invent an answer. `INSUFFICIENT`,
`STALE`, `UNAVAILABLE`, and `INVALID` evidence fail closed. Material conflict
produces review rather than a silent winner.

## Verified Answer

`VerifiedAnswer` is deeply immutable and binds:

- request, tenant, user, route, HAT/version/manifest, and effective scope;
- primary and optional fallback Evidence Bundle hashes;
- Step 21, Correction Packet, HMAC receipt, Draft V2, and verification summary
  hashes;
- the exact Draft V2 text, UTF-8 byte length, and SHA-256;
- deterministic packet citations and claim-verification references;
- material temporal, freshness, source-authority, and evidence limitations;
- evidence, knowledge-policy, answer, output, and copied execution-policy
  states as separate typed fields.

The answer body is exactly the verified Draft V2. There is no
post-verification model rewrite. Changing its text, citation, limitation, or
verification reference changes or invalidates the answer hash.

## HAT_ENFORCE and failure outputs

For `HAT_ENFORCE`, failure can never return Draft V1, a known-bad Draft V2, or
model-prior text with a warning. The only outputs are:

- a `VerifiedAnswer` from a fully eligible Draft V2;
- `HumanReviewRequired` / `CONFIRMATION_REQUIRED`; or
- a sanitized `BoundedAnswerFailure`.

Review and failure records are immutable and hash-bound. They contain lineage
hashes and closed reason codes, not an answer body, provider errors, stack
traces, credentials, approval, or execution capability.

## One bounded retry

Only correctable Step 25 failures can trigger retry: an unsatisfied required
correction, prohibited-claim repetition, generation-caused invalid citation,
or an otherwise correctable unverified claim while evidence remains
`SUFFICIENT`. Integrity failures, policy blocks, unavailable/insufficient/
stale evidence, and material conflict never retry.

The maximum is exactly one. The retry uses the same verified Draft V1,
Correction Packet, receipt, route, scope, and evidence universe. Its fixed
prompt receives the failed Draft V2 plus a bounded deterministic failure
summary. Tools, browsing, code execution, retrieval, and new evidence remain
disabled. Provider-level retries are disabled inside this final attempt.

The retry Draft V2 has a distinct identity and does not overwrite the first.
The entire Step 25 layered verifier runs again over every claim. No prior
claim verdict is carried forward as trust. Success assembles the retry Draft
V2; failure ends in review with both immutable Draft V2 hashes and no second
retry.

## Persistence and authority

Step 4 has no final-answer table, and its unique `(run, stage)` Draft record
cannot safely represent a second stage-2 revision. Step 26 therefore adds no
migration and does not overload drafts, verdicts, or packet tables. Final
answer/review/failure and retry-lineage values are immutable runtime/audit
contracts under
`STEP26_IMMUTABLE_RUNTIME_OUTPUT_NO_SAFE_STEP4_TABLE`. Coordinated durable
storage requires a later explicit schema decision.

No Step 26 component can approve, execute, commit, activate memory, browse,
query retrieval, or mutate AWS/S3. The model and verifier remain
non-authoritative. Step 27 owns Personal Memory persistence, quotas, and model
bindings and is NOT STARTED.
