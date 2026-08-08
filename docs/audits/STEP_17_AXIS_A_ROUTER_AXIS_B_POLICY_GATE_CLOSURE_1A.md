# Step 17 — Axis A Router, Axis B Policy Gate and Evidence Status 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 18: NOT STARTED.

## Starting identity

- Repository baseline: `c51b32373dba5437f027268d1806a3fcdc1b3a91`
- Branch: `main`
- Baseline tests: 1,154 passed
- Baseline contract validation: PASS

The final closure identity is the commit containing this record. No unverified
future commit SHA is embedded in the record.

## Implemented boundary

Step 17 adds the immutable `aioa_memory_kernel.routing` package:

- `RoutingInput` and a hash-bound snapshot of existing Step 12 registry
  entries;
- deterministic Axis A routing with `PASS_THROUGH`, `HAT_ASSIST`,
  `HAT_ENFORCE`, and `AMBIGUOUS`;
- independent Axis B `KnowledgePolicyDecision` and
  `ExecutionAuthorizationDecision`;
- canonical evidence status plus separate coverage and answer status;
- immutable route, policy, and HAT-to-Kernel result envelopes;
- stable reason codes and canonical SHA-256 bindings.

The German Law HAT manifest is the real domain fixture, but no routing rule is
hard-coded to German law. The existing trusted HAT registry remains the sole
source of installed identity and enablement state.

## Authority and isolation invariants

- Model authority added: NO.
- Provider authority added: NO.
- HAT execution authority added: NO.
- Router or policy execution capability added: NO.
- Human approval created: NO.
- Cross-tenant or cross-user routing added: NO.
- Database migration added: NO.
- AWS, network, provider, subprocess, and filesystem-target effects: NO.

Private candidates bind exact tenant/user identities. Global candidates still
require trusted registry, manifest, domain, and scope evidence. Candidate
ordering is canonical, and corrupted hashes fail closed.

## Validation

Focused tests cover decision contracts, deterministic hashes, all Axis A and
Axis B states, disabled/untrusted/quarantined/revoked candidates, manifest and
scope mismatch, model/provider non-authority, tenant/user isolation, evidence
and answer separation, execution non-authority, static inertness, and the Step
18 boundary.

The offline validator reproduces the complete decision matrices and records
zero external effects. Sanitized evidence is committed at
`docs/evidence/routing/step17-routing-policy-validation.json`.

Full validation requires:

- Step 17 focused tests: PASS;
- complete repository test discovery: PASS;
- Step 10 and Step 16 regressions: PASS;
- HAT, German Law, authority, tenant, and serialization regressions: PASS;
- contract validator: PASS;
- compileall: PASS;
- offline Step 17 validator: PASS.

## Known limitations and handoff

Step 17 does not retrieve or read corpus content. It implements no exact,
alias, phrase, full-text, metadata, vector, hybrid, or reranking engine and no
retrieval SQL. Step 18 must consume the selected HAT identity, route hash,
effective scope, evidence state, and trusted Step 16 corpus boundary, applying
hard tenant/HAT/scope filters before candidate generation.

Step 18 remains NOT STARTED in this closure.
