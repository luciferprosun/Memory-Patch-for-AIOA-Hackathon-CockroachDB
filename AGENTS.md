# Memory Patch for AIOA — repository instructions

## Canonical production line

Before any production implementation, audit, or release task, read
`docs/roadmap/PRODUCTION_ROADMAP.md` completely. It is the authoritative
production order and scope for this repository. If another roadmap or an older
desktop copy differs, the repository copy controls unless the user explicitly
replaces it.

Execute one roadmap step per task. Do not start the next step until the current
step is either `COMPLETE AND PUSHED` or the user explicitly defers it. Do not
silently combine, skip, renumber, or reinterpret steps.

Include a canonical-roadmap checkpoint update only in the intended step
commit. Treat that update as completion evidence only after validation,
commit, push to `origin/main`, and the closing report all succeed. A checked
box without a reachable pushed commit is not completion evidence.

## Repository guard

The production repository is this repository. Its expected branch is `main`
and its expected remote is
`https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB.git`.

At the start of every production task, verify:

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git diff --name-only
git rev-list --left-right --count main...origin/main
```

Stop safely if the branch is wrong, unrelated worktree changes exist, an
unexpected Git operation is active, or the task's required starting commit
does not match.

## Verified implementation checkpoints

At roadmap adoption and in subsequent audited closure records:

- Step 0A is complete and pushed at
  `b3d555ec230a894b541e3570347fcf086511df2a`.
- Step 0B is complete and pushed at
  `870145c78d9e6bf02e318bdca2327eb808f381b7`.
- Step 0C is an accepted architecture baseline from the source roadmap.
- Step 1 is complete and pushed at
  `3f6d341bc3ceb964a2b25d4913a0695595dbd7d0`.
- Step 2 is complete and pushed at
  `807b459b3d0270bd84c5590df6e7abf3e4f9842b`.
- Step 3 is complete and pushed at
  `3e8c499fbcb2bb905fce451a163f913030ecacce`.
- Step 4 is complete and pushed at
  `ba825353d1a3df2e455f60061477cfa87cab08f9`.
- Step 5 implementation and live validation are complete in its intended
  closure commit; the operator closure report records that commit SHA.
- Step 6 implementation and live validation are complete in its intended
  closure commit; the operator closure report records that commit SHA.
- Step 7 implementation, CloudFormation deployment, Object Lock validation,
  and exact-version read-back are complete in its intended closure commit.
  Step 7 was completed after Step 9 because it had previously been explicitly
  deferred. It is completion evidence only after that commit is reachable on
  `origin/main`.
- Step 8 external-volume runtime integration, fail-closed policy, and approved
  exact-byte live validation are complete in its intended closure commit. It
  is completion evidence only after that commit is reachable on `origin/main`.
- Step 9 implementation and live validation are complete in its intended
  closure commit. It is completion evidence only after that commit is
  reachable on `origin/main`; the operator closure report records its SHA.
- Step 10 durable ingestion orchestration, migration `0007`, exact external
  evidence recovery, and graceful disposable-runtime validation are complete
  in its intended closure commit. It is completion evidence only after that
  commit is reachable on `origin/main`.
- Step 11 deterministic parsing, Unicode NFC normalization, structural
  validation, migration `0008`, real Step 10 parser/validator ports, and
  zero-external-write live validation are complete in its intended closure
  commit. It is completion evidence only after that commit is reachable on
  `origin/main`.
- Step 12 trusted HAT registry, strict manifest validation, migration `0009`,
  explicit system-installed catalog, capability gate, and controlled disposable
  validation are complete in its intended closure commit. It is completion
  evidence only after that commit is reachable on `origin/main`.
- Step 13 German Law HAT manifest, request/scope contracts, typed source
  authority and temporal policy, fixed metadata adapters, and controlled
  disposable validation are complete in its intended closure commit. It is
  completion evidence only after that commit is reachable on `origin/main`.
- Step 14 bounded corpus inventory, non-destructive duplicate evidence,
  license/privacy/quarantine classification, external canonical bundle, and
  idempotent zero-publication Step 9 registration validation are complete in
  its intended closure commit. It is completion evidence only after that
  commit is reachable on `origin/main`.
- Step 15 temporal and jurisdictional normalization, digest-bound external
  artifacts, document/version and supersession candidates, conflict
  preservation, review-only Step 9 proposals, and controlled disposable
  CockroachDB validation are complete in its intended closure commit. It is
  completion evidence only after that commit is reachable on `origin/main`.
- `Step 16: Step 16 trusted publication complete and pushed at actual closure
  commit` after deterministic publication, evidence-bound snapshot binding,
  parser/chunk coverage, and controlled cleanup.
- Step 17 deterministic Axis A routing, independent Axis B knowledge and
  execution policy, evidence/answer separation, isolation, and offline
  validation are complete in its intended closure commit. It is completion
  evidence only after that commit is reachable on `origin/main`.
- Step 18 exact identifiers, structured statute/section lookup, German lexical
  retrieval, hard pre-candidate tenant/HAT/scope/authority filtering, and
  controlled disposable validation are complete in its intended closure
  commit. It is completion evidence only after that commit is reachable on
  `origin/main`.
- Step 19 immutable local-model embeddings, verified external derived cache,
  lineage-bound `VECTOR(384)` persistence, L2 vector retrieval, shared Step 18
  hard scope, RLS/FORCE RLS, and controlled real-model/disposable CockroachDB
  validation are complete in its intended closure commit. It is completion
  evidence only after that commit is reachable on `origin/main`.
- Step 20 verified Step 18/19 input binding, exact lineage deduplication,
  fixed-integer fusion, deterministic diversity, provider-neutral byte budget,
  immutable/hash-bound Evidence Bundle, authority/isolation revalidation, and
  controlled real-input/disposable validation are complete in its intended
  closure commit. It is completion evidence only after that commit is
  reachable on `origin/main`.
- Step 21 verified Step 20 bundle binding, explicit current/historical/future
  applicability, deterministic supersession/conflict preservation, separate
  policy-driven freshness, canonical evidence statuses, bounded same-scope
  completeness fallback, and offline real/synthetic validation are complete
  in its intended closure commit. It is completion evidence only after that
  commit is reachable on `origin/main`.
- Step 22 provider-neutral original-query-only Draft V1 generation, pinned
  hosted provider/model identity, evidence-leakage prevention, tool and
  credential isolation, bounded timeout/retry, exact Draft V1 byte/hash
  identity, existing Step 4 draft persistence, and controlled validation are
  complete in its intended closure commit. It is completion evidence only
  after that commit is reachable on `origin/main`.
- Step 23 exact-span deterministic claim extraction, stable claim identity,
  verified Step 20/21 evidence binding, conservative
  `SUPPORTED`/`REFUTED`/`UNVERIFIED` candidate semantics, conflict
  preservation, and immutable packet-input snapshot validation are complete
  in its intended closure commit. It is completion evidence only after that
  commit is reachable on `origin/main`.
- Step 24 verified frozen Step 23 input binding, deterministic correction and
  prohibition derivation, citation/conflict preservation, canonical packet
  JSON/hash, separate HMAC-SHA-256 integrity receipts, replay/tamper
  validation, and explicit no-migration persistence deferral are complete in
  its intended closure commit. It is completion evidence only after that
  commit is reachable on `origin/main`.
- Step 25 verified Correction Packet integrity gating, exact Draft V2
  generation through the Step 22 tool-less provider boundary, existing Step 4
  stage-2 persistence, reused Step 23 exact-span extraction, deterministic
  packet/fact/date/source/citation/evidence layers, non-authoritative semantic
  signals, hash-bound claim verdicts and summary, and controlled offline
  validation are complete in its intended closure commit. It is completion
  evidence only after that commit is reachable on `origin/main`.
- Step 26 complete upstream integrity binding, exact verified-Draft-V2 answer
  assembly, final policy/evidence ceilings, HAT_ENFORCE no-Draft-V1-fallback,
  one same-packet retry with full Step 25 re-verification, typed review and
  bounded-failure outputs, and controlled offline validation are complete in
  its intended closure commit. It is completion evidence only after that
  commit is reachable on `origin/main`.
- Step 27 owner-private empty Personal Memory HAT slots, versioned
  configuration/lifecycle, hard quotas, provider-neutral model bindings,
  Step 4/5/6 durable idempotency, RLS/FORCE RLS owner isolation, canonical
  configuration export, two-stage logical delete, and controlled disposable
  CockroachDB validation are complete in its intended closure commit. It is
  completion evidence only after that commit is reachable on `origin/main`.
- Step 28 owner- and slot-bound Correction Candidate Envelopes, exact Kernel
  and Critic Prompt Loop producer boundaries, `DETECTED`-only durable intake,
  hash-bound lineage, hard candidate quotas, idempotent exact replay,
  deterministic exact deduplication, RLS/FORCE RLS isolation, and controlled
  disposable CockroachDB validation are complete in its intended closure
  commit. It is completion evidence only after that commit is reachable on
  `origin/main`.
- Step 29 owner-scoped Personal Memory Patch Proposals, exact
  `DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL`
  transitions, immutable canonical-evidence binding, deterministic dedup,
  conflict, freshness and temporal gates, quota/model-binding revalidation,
  migration 0013, RLS/FORCE RLS owner isolation, and controlled disposable
  CockroachDB validation are complete in its intended closure commit. It is
  completion evidence only after that commit is reachable on `origin/main`.
- `Step 29: COMPLETE AND PUSHED at actual closure commit`.
- Step 30 exact owner-human approval, hash-bound approval/commit/activation
  receipts, independent replay protection, separate least-privileged Commit
  Helper, precommit/preactivation TOCTOU revalidation, no-skip
  `AWAITING_APPROVAL -> APPROVED -> COMMITTED -> ACTIVE` transitions,
  RLS/FORCE RLS owner isolation, and controlled disposable CockroachDB
  validation are complete in its intended closure commit. It is completion
  evidence only after that commit is reachable on `origin/main`.
- `Step 30: COMPLETE AND PUSHED at actual closure commit`.
- Step 31 exact Step 30 ACTIVE-only retrieval, full lifecycle receipt/hash
  reconstruction, owner/tenant/slot RLS isolation, route-scope, temporal and
  provider-neutral model-binding gates, bounded deterministic context,
  two-model same-patch reuse, canonical-evidence conflict suppression and
  controlled disposable CockroachDB validation are complete in its intended
  closure commit. It is completion evidence only after that commit is
  reachable on `origin/main`.
- `Step 31: COMPLETE AND PUSHED at actual closure commit`.
- `Step 32: NOT STARTED`. Step 31 completion does not authorize Step 32.

The repository HEAD may advance after this adoption record. Confirm completion
through Git history and the canonical roadmap rather than assuming this
adoption-time hash remains HEAD.

## Authority boundary

Provider or model output is never authority. HATs, models, Critic Loop,
previews, registry state, policy state, risk flags, and sandbox eligibility do
not grant approval, commit, execution, external-action, or Control Write
authority. Preserve human approval, hash binding, tenant isolation, gates,
fail-closed behavior, and audit evidence throughout the roadmap.
