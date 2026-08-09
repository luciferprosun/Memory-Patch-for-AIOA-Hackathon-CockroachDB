# Provider-Neutral Model Adapter and Draft V1 1A

## Scope and upstream boundary

Step 22 consumes a verified `TemporalResolutionResult` from Step 21 and the
exact original user question. The temporal result is verified before request
construction. Its request, tenant, user, route, evidence-status, and result
hash are retained as out-of-band audit lineage. They are not model context.

The provider-facing `ProviderCallRequest` contains only the pinned provider
identity, a minimal generic prompt, the exact original query, and bounded
generation parameters. It has no Step 18 candidate, Step 20 Evidence Bundle,
Step 21 assessment, source authority, correction requirement, or answer hint.
The unique sentinel negative proves this projection in tests and controlled
validation.

## Approved provider and model decision

The checked-in Step 22 V1 decision is:

- provider: `moonshot-ai`;
- adapter: `moonshot-chat-completions-1a`;
- model: `moonshot-v1-8k`;
- declared provider model version: `moonshot-v1-8k`;
- endpoint class: `moonshot-public-chat-completions-v1`;
- context window reported by the live model registry: 8,192 tokens.

The exact configuration and its digest are stored in
`config/modeling/moonshot-v1-8k-step22-1a.json`. The live provider registry
confirms the exact model ID, owner, and context window. The provider does not
publish an immutable weight or deployment revision for this hosted model;
`immutable_model_revision=false` records that limitation rather than claiming
stronger identity. A later provider revision requires a new checked-in config
and digest.

The Kernel-facing `DraftV1Provider` protocol is provider-neutral. The one
approved Moonshot adapter is isolated below `modeling/providers`; requests
cannot select a provider, model, endpoint, Python callable, or arbitrary
provider JSON.

## True uncorrected Draft V1

The fixed prompt template tells the provider only to answer the user's
question directly, avoid claims of hidden capabilities, and return inert
text. The template ID, version, content digest, parameter digest, timeout
policy digest, and attempt-policy digest are hash-bound.

Draft V1 is the exact successful provider response. It is validated as NFC
UTF-8, rejects NUL and prohibited controls, and is limited to 65,536 bytes.
The text is not rewritten, corrected, summarized, executed, or interpreted as
policy. Its exact byte length and SHA-256 are bound into `DraftV1.draft_hash`.
Latency and usage metadata are safe observability facts, not answer identity
or authority.

## Tool and credential ceiling

The adapter sends a non-streaming text-only chat request without tools,
functions, browsing, or code-execution fields. It rejects any response that
contains a tool call or function call. Model text that resembles shell, SQL,
approval, routing, or memory instructions remains inert text.

Only `MOONSHOT_API_KEY` is read at the provider adapter boundary. The key,
Authorization header, provider error body, cookies, and endpoint secrets are
never included in contracts, logs, evidence, persistence, or identity hashes.
The provider adapter imports no persistence, CockroachDB, AWS/S3, Git,
approval, commit, or Personal Memory capability.

## Timeout, retry, and transaction separation

Each attempt has an explicit 45-second timeout. The policy permits at most two
attempts with one bounded retry. Only transient network, timeout, capacity, or
server failures are retryable. Authentication, invalid-request, policy,
identity, tooling, and response-contract failures fail immediately. A timeout
may represent unknown provider completion and is never described as
exactly-once execution.

`assert_no_open_persistence_transaction()` runs immediately before every
provider attempt. Reads used for replay finish first. Model generation then
finishes without a database transaction. Only afterward may the Kernel open a
short transaction to persist the immutable Draft V1.

## Draft persistence

No migration is added. Step 22 reuses `memory_patch.drafts` from Step 4 and its
Step 5 RLS/FORCE RLS policies. The route request ID is the existing Kernel run
identity, `draft_stage=1`, and the row binds the exact content SHA-256 plus a
bounded immutable Draft V1 envelope. The envelope is verified on every read.

The deterministic draft ID binds the generation-request hash and stage. Exact
replay returns the existing row without a second provider call. Reusing the
same identity with different text, provider, prompt, lineage, or membership
fails closed. Tenant and user access continue through the existing
`kernel_run_context_matches` RLS policy.

## Authority boundary

Provider output cannot modify route, policy, evidence status, temporal state,
source authority, tenant/user scope, approval, execution, or memory. Step 21
status is lineage only and is not used to correct or bias Draft V1.

## Validation and limitations

Ordinary tests use a deterministic capturing provider and need no network or
credential. Controlled validation proves the live provider registry identity,
the evidence-leakage negative, text/hash binding, retry ceiling, persistence
replay, and transaction separation. The approved account's bounded live text
generation currently returns provider quota exhaustion, recorded honestly as
`UNAVAILABLE`; no real response is fabricated. The canonical roadmap does not
require a successful hosted call for this adapter-contract baseline.

## Step 23 boundary

Step 23 is NOT STARTED. Claim extraction, claim IDs, span mapping, evidence
binding, `SUPPORTED`/`REFUTED`/`UNVERIFIED`, Correction Packet, Draft V2, and
final verification do not exist in Step 22.
