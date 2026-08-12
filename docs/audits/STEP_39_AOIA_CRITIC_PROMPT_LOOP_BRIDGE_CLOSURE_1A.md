# Step 39 AOIA Critic Prompt Loop Bridge Closure 1A

## Closure-worktree verdict

`PASS_PROVIDER_FREE_CONTROLLED / CLOSURE ELIGIBLE`

The optional Step 39 bridge and its controlled conformance validation are
complete. The bridge is additive, preserves the Step 38 core result, maps a
validated assessment only to Step 28, proves the independent Step 29 gate,
and performs zero Step 30 calls. The final execution report records the SHA
and push reachability of the single commit containing this record. Step 40 is
`NOT STARTED`.

## Starting identity

- Exact Step 38 closure base:
  `939395d355ce0630c5044c4ab427082c3cf72d23`
- Baseline subject:
  `test(e2e): validate German Law full memory patch flow 1a`
- Step 38 live validation digest:
  `b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`
- Step 38 verified upstream lineage:
  `b3175f1b3476aa88453bca4623475c0ce7835488af623f51a43b0b7b8a793a23`
- Step 39 validation evidence:
  `docs/evidence/critic/step39-critic-prompt-loop-bridge-validation.json`
- Step 39 validation digest:
  `de40a26eadf342b04d7b7b7ff10cc4c2b9c95322c4b37fc54a5af6b5d34665f0`

No final Step 39 commit SHA is embedded before that commit exists.

## Prior Critic artifacts reviewed

The closure audit inventoried ADR-008, the Step 28 ADR-035 contracts,
migration, validation evidence and Critic intake service, the Step 29
evidence-validation gate, and the Step 30 human approval/commit boundary.
Critic material under `docs/history/` and `docs/provenance/` was classified as
non-authoritative design history. The Step 38 base contained no callable
Critic runtime, authenticated transport, frozen Critic prompt, or real Critic
fixture; none was invented or imported for this closure.

## Closed implementation scope

Step 39 adds an optional, typed, hash-bound Critic Prompt Loop bridge over
existing Kernel artifacts. Its closed implementation surface is:

- bounded `CriticReviewRequest` projection with shared run, route, HAT,
  effective scope, artifact, claim, evidence, temporal, policy, prompt, and
  provider lineage;
- immutable versioned prompt and strict closed JSON parser;
- typed `CriticAssessment`, provider call receipt, bridge result, and Step 28
  candidate mapping result;
- explicit disabled, no-issue, provider-unavailable, invalid-output, accepted,
  replay, and tamper behavior;
- the existing Step 28 Critic candidate intake as the only durable write;
- ordinary Step 29 evidence validation and duplicate suppression;
- unchanged Step 30 human owner approval and separated commit/activation;
- one hash-only Step 33 accepted-candidate audit path; and
- deterministic provider-free German Law validation.

The canonical evidence and focused tests independently reconstruct the hashes,
reject raw private text, and verify every no-authority flag.

## Core independence

The Step 38 German Law core remains complete independently of the Critic. The
Step 39 bridge must prove that the following states do not change the existing
Verified Answer, Kernel candidate, or active-memory behavior:

- Critic disabled;
- provider unavailable or retry exhausted;
- response rejected by the closed parser;
- request/output integrity failure;
- valid no-issue response; and
- a valid issue denied by Step 28 or Step 29.

`core_memory_patch_unaffected=true` and `critic_optional=true` are required
contract invariants, not post-hoc status labels.

## Request, prompt, output, and evidence boundary

The Kernel owns trusted identity projection. The Critic request must bind the
exact owner/tenant context, request/run, route, HAT, effective scope, bounded
artifacts, claims, evidence references, temporal/freshness state, Correction
Packet hash when present, prompt/policy digests, and approved provider
identity.

The prompt treats every embedded instruction as inert and disallows tools,
web, code, functions, publication, approval, commit, activation, and external
action. The parser accepts only the closed assessment schema. A detected issue
may reference only supplied claims and evidence and must reproduce the exact
trusted scope. Raw provider JSON is never a durable candidate.

Evidence references remain bounded non-authoritative context. Critic output
cannot create evidence, change source authority or publication state, override
temporal resolution, or certify its own correction.

## Step 28, Step 29, and Step 30 boundary

The only allowed durable path is:

```text
typed accepted Critic assessment
  -> existing Step 28 submit_critic_loop_candidate
  -> DETECTED owner-private candidate
  -> ordinary Step 29 evidence/conflict/freshness/dedup gates
  -> AWAITING_APPROVAL only after Step 29 validation
  -> human owner approval and separated Step 30 commit/activation
```

Direct proposal, direct validation, model approval, Critic approval, commit,
activation, retrieval, shared promotion, review decision, or execution is
forbidden. A Step 39 candidate carries independent Critic provenance and does
not copy the Step 38 Kernel candidate identity.

## German Law validation fixture

The controlled lane uses
`REAL_STEP38_HASH_LINEAGE + SYNTHETIC_CRITIC_ADAPTER`:

- selected case: `primary-entry-into-force`;
- golden suite:
  `b9c424e2b00ed13317f73d4fd86bbd2c96b1f2e606765f98dc35cadc0663cd50`;
- question digest:
  `f33243aa0b47a12cf7e86bae77c079d20d573f43ccea740c0dafc91d738dfa0b`;
- route hash:
  `e6b1912195e8c3cecb2a79cab235592c21b9fe6cab0ce5c0fa8c2e7a18794d0c`;
- Draft V1 hash:
  `17513ff270ad13cab959bec4e80856cf3f9e1b69658b2c0040d3023cefba5139`;
- defect claim:
  `claim-2398f6bb65cd2b61bc60c03021338e6dfe8f21a285431c18fde2d95bf00261e2`;
- evidence bundle:
  `563401ac507d0eef448b0b189bbfde3a8ab117c17eedc490ba92a2d977ed291e`;
- Correction Packet:
  `5af8baa1a3708dc2fd6e6f951392b7799ef85a7a2065c91e5b01367e02b26bb6`;
- Verified Answer:
  `21b3e8fd4f9c38eddcb5a545fe7d6b1631310357d3260ea3b31015fe0168cdea`.

The fake adapter deterministically detects the bounded already-proven
wrong-2025 claim and return only supplied claim/evidence identities plus the
bounded 2024 correction. No real provider call or real Critic runtime is
claimed. Equivalent Kernel and Critic correction content must not create two
patches after Step 29 deduplication.

## Audit and failure behavior

An accepted durable candidate uses the existing Step 33 vocabulary:

```text
CORRECTION_CANDIDATE_DETECTED / CRITIC_LOOP / CORRECTION_CANDIDATE / HASH_ONLY
```

Raw question, draft, prompt, evidence snippet, correction, provider response,
credential, and Personal Memory text are excluded. Disabled, no-issue,
provider/parser, and request-integrity outcomes do not fabricate candidate
events.

Every Critic failure is closed for the Critic-derived candidate and open for
the already-verified core path: no candidate is written, no Kernel substitute
is manufactured, no scope is widened, and no later lifecycle authority is
granted. Exact replay must be idempotent; changed replay must conflict.

## Authority matrix

| Capability | Required Step 39 result |
| --- | --- |
| canonical evidence authority | `false` |
| route authority | `false` |
| source/publication authority | `false` |
| proposal or validation authority | `false` |
| reviewer authority | `false` |
| approval authority | `false` |
| commit authority | `false` |
| activation authority | `false` |
| execution authority | `false` |
| external-action authority | `false` |
| cross-user access | denied |
| cross-tenant access | denied |
| direct Step 29/30 transition | absent |
| broad `BYPASSRLS` or admin fallback | absent |

These values are acceptance requirements. The canonical evidence and the
observed validation ledger below prove them; the table alone grants no
authority.

## Validation ledger

| Gate | Current state |
| --- | --- |
| compileall | `PASS` |
| focused Step 39 suites | `51/51 PASS in 16.254s` |
| disabled/enabled/no-issue/failure matrix | `PASS` |
| replay and adversarial tamper matrix | `PASS` |
| Step 28-only mapping and exact replay | `PASS` |
| Step 29 validation and duplicate suppression | `PASS; AWAITING_APPROVAL` |
| Step 30 human/credential separation | `PASS; calls=0` |
| Step 33 hash-only audit chain | `PASS; one verified event` |
| owner/tenant isolation | `PASS` |
| controlled provider-free validation | `PASS_PROVIDER_FREE_CONTROLLED` |
| real Critic provider validation | `UNAVAILABLE_NOT_REQUIRED` |
| sanitized evidence digest | `de40a26e...34665f0` |
| cross-step focused regressions | `542/542 PASS` |
| full repository regression before checkpoint flip | `2104/2104 PASS` |
| final post-checkpoint full regression | `2112/2112 PASS in 125.167s` |
| contract validator | `PASS` |
| secret and authority scans | `PASS` |
| changeset scope and whitespace | `PASS` |

## Retained limitations and boundaries

- The repository does not contain a canonical external AOIA-Core Critic
  runtime or authenticated cross-process transport.
- The required validation uses a deterministic synthetic provider adapter and
  proves the local bridge contract only.
- No provider availability, model quality, or permanent model learning claim
  follows from the provider-free lane.
- Disabled and failed calls have no dedicated Step 33 Critic event vocabulary;
  they remain bounded result artifacts and must not be misreported as durable
  candidates.
- The evidence truthfully classifies real Critic provider validation as
  `UNAVAILABLE_NOT_REQUIRED`; no real provider availability or model-quality
  claim is inferred from the conformance adapter.

## Commit and push convention

- Closure commit: the single commit containing this record
- Commit subject: `feat(critic): add optional production candidate bridge 1a`
- Push and local/remote equality: verified and reported after the commit exists
- Clean final worktree: verified and reported after push

Embedding the commit's own SHA in this file would change that SHA. The final
execution report is therefore the authoritative observation of the exact
closure SHA, remote reachability, `0 0` divergence, and clean worktree.

## Step 40 boundary

Step 40 resource and deployment optimization is not implemented or started by
this closure draft. Step 39 may hand off only after it is complete and pushed.
Current Step 40 state: `NOT STARTED`.
