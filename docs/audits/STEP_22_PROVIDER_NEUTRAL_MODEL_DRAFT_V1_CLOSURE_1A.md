# Step 22 - Provider-Neutral Model Adapter and Draft V1 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 23: NOT STARTED.

## Starting identity

- Exact Step 21 baseline: `c70fe73ddedb20e4b57186fbd336568090f90018`
- Baseline subject: `feat(retrieval): add temporal conflict and freshness policy 1a`
- Branch: `main`
- Baseline tests: 1,462 passed
- Baseline contract validation and compileall: PASS

The final closure identity is the commit containing this record. No future
commit SHA is embedded before the commit exists.

## Provider/model decision

Step 22 freezes one approved hosted adapter configuration:
`moonshot-ai`, adapter `moonshot-chat-completions-1a`, model
`moonshot-v1-8k`, and declared version `moonshot-v1-8k`. Its live registry
identity, owner, and 8,192-token context window were verified. The hosted
provider exposes no immutable weight/deployment revision, so the contract
records `immutable_model_revision=false` rather than inventing one.

The Kernel core depends only on the typed `DraftV1Provider` protocol. A caller
cannot select a provider, model, endpoint, module, callable, or provider JSON.

## True Draft V1 boundary

The provider call contains the exact original question and a minimal generic
instruction only. Step 18 candidates, Step 20 Evidence Bundle and ranking,
Step 21 temporal/conflict/freshness facts, authority metadata, required
corrections, prohibited claims, and answer hints are excluded.

Tests and validation place the unique
`CORRECTION_EVIDENCE_SENTINEL_DO_NOT_SEND` string in verified evidence,
capture the exact provider request, and prove the sentinel and all evidence
fields are absent. Step 21 route/result/status remain out-of-band hash-bound
lineage on the Draft V1 record and do not influence the prompt.

The returned Draft V1 is the exact provider response text. UTF-8 byte length,
SHA-256, generation-result hash, and immutable draft hash are preserved. Code,
SQL, links, route instructions, approval claims, and memory instructions in
the output remain inert text.

## Credential, tool, and authority isolation

The provider-specific module receives only the approved API credential and
text-generation request. It has no persistence/DB, AWS/S3, Git, approval,
commit, execution, or Personal Memory import/capability. Credentials and raw
provider errors are excluded from logs, hashes, persistence, and evidence.

Tools, function calling, browsing, and code execution are disabled by request
shape and provider identity. Any tool/function-call response fails closed.
Model output cannot change route, policy, evidence status, source authority,
tenant/user scope, approval, execution, or memory.

## Timeout, retry, and persistence

Timeout is fixed at 45 seconds per attempt. At most two attempts are allowed;
only transient/timeout/provider-capacity failures retry. Authentication,
invalid request, policy, identity, tooling, and response-contract failures do
not retry indefinitely. Attempt outputs are never merged.

No database transaction is open during a provider call. Step 22 reuses the
existing `memory_patch.drafts` table with `draft_stage=1`, its Kernel-run FK,
and Step 5 tenant/user RLS/FORCE RLS. No migration is added. Exact replay is
idempotent and avoids a second provider call; conflicting replay fails closed.

## Validation

- Step 22 focused suite: 37/37 PASS.
- Full repository suite: 1,499/1,499 PASS.
- Step 17-21, authority, tenant, CockroachDB/parsing persistence, and contract
  serialization regressions: 484/484 PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled Step 22 validation: PASS.

The controlled validator proves the live provider registry identity and the
complete deterministic fake-provider boundary, including exact request/text
hashes, sentinel exclusion, tools disabled, short-transaction separation,
and idempotent replay.

One approved live text-generation attempt was executed under the two-attempt
ceiling. Provider capacity returned quota exhaustion and therefore
`REAL_PROVIDER_VALIDATION=UNAVAILABLE`; no response or success was fabricated,
and no alternate provider was substituted. The canonical Step 22 roadmap
requires the provider-neutral adapter and Draft V1 boundary, not a paid hosted
generation success, so this operational limitation does not weaken closure.

Sanitized evidence is committed at
`docs/evidence/modeling/step22-provider-neutral-draft-v1-validation.json`.

## Effects and changeset

Step 22 performs no AWS/S3 mutation, source publication, retrieval change,
approval, commit-helper action, memory write, or external tool execution. The
only network capability is the exact approved provider adapter and controlled
provider registry/generation validation. No dependency or database migration
is added.

## Step 23 handoff

Step 23 may consume immutable `DraftV1` and its exact text/hash together with
the unchanged Step 20 Evidence Bundle and Step 21 temporal result. It may add
claim spans, stable claim IDs, and evidence mappings. It must not allow model
text to alter route, policy, evidence authority, tenant/user scope, approval,
execution, or memory.

Step 23 remains NOT STARTED in this closure.
