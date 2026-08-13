# Post-Roadmap R6 Health and Golden Path Runtime Proof 1A

## Outcome

R6 assembled the existing repository-native ASGI application on the
`memory-patch-4gb-demo-1a` profile and completed one closure-eligible live
German Law lineage. The controlled runtime used a disposable loopback
CockroachDB v26.2.4 database, the durable Cockroach-backed owner session store,
the controlled authentication harness with server-derived owner identity, and
the approved `openrouter` / `moonshotai/kimi-k2` provider identity.

The machine-readable proof is
[`post-roadmap-r6-golden-path-runtime-proof-1a.json`](../evidence/demo/post-roadmap-r6-golden-path-runtime-proof-1a.json).

## Health contract

- `GET /health/live` returned HTTP 200 with only `{"status":"LIVE"}`.
- `GET /health/ready` returned HTTP 200 with only `{"status":"READY"}` after
  configuration, migration state, application DB pool, durable sessions,
  judge auth, provider identity, provider guard, and mandatory services were
  initialized.
- Health checks made zero paid provider calls and exposed zero privileged
  browser secrets.
- Shutdown moved readiness to `STOPPING` before closing owned resources.

## Live Golden Path

- Primary case: `primary-entry-into-force`.
- Backup case used: no.
- The evidence-blind Draft V1 contained one material defect.
- Real exact/full-text/vector/hybrid retrieval and temporal resolution produced
  the canonical German Law Evidence Bundle.
- The Correction Packet, Draft V2, layered verification, and Step26 Verified
  Answer all completed with hash-bound lineage.
- The live guard reserved and completed two provider calls. Failed, unknown,
  and budget-denied calls were all zero. Accounting is a call-count ceiling,
  not a claim about exact billed currency.

## Personal Memory and authority

The verified correction continued through candidate, proposal,
`AWAITING_APPROVAL`, explicit owner approval, Commit Helper, activation,
durable audit, later ACTIVE retrieval, and provider-neutral cross-model reuse.
Personal Memory remained private and non-canonical. Canonical evidence
suppressed a conflicting private patch. Cross-owner, cross-tenant, cross-user
approval, cross-user export, disallowed-model, and ordinary-review-role probes
all failed closed. Critic authority was not widened.

## Cleanup and limitations

The owned database was dropped, the CockroachDB process exited, ports closed,
and the temporary store was removed without force kill. Production resources
touched: zero.

This was a hosted-style loopback proof, not a public deployment. It used the
explicit repository-approved local disposable DB TLS exception and a
controlled authentication harness rather than a real external OIDC provider.
The local E5 artifacts were pre-staged and verified; no model download or
embedding network access occurred. R7 still owns the full post-roadmap gate,
the single final runtime commit/push, and the deployment handoff.

## Resource and validation summary

The frozen Step40 resource bounds remain active: one web worker, application
DB pool `1..4`, one concurrent provider call with two queued callers, and at
most 64 durable session records (four per owner, 16 pending flows, 2048-byte
payload maximum). The existing Step40 measurement records a 784 MiB
conservative core peak against the 3000 MiB configured peak budget. R6 did not
repeat the dedicated peak-memory campaign; it revalidated the exact profile
and bounds through the assembled runtime tests.

The final deterministic R6 campaign completed 404 tests with zero failures:

- 101 R2-R6 runtime composition, database, session, health, auth, launcher,
  and provider-guard tests;
- 105 Step35-Step37 UI, credential, authority, failure, and recovery tests;
- 92 Step38 coherent runtime, correction, German Law, provider, and retrieval
  tests;
- 51 Step39 Critic boundary tests;
- 55 Step40-Step41 resource and security campaign tests.

The contract validator, Step35 UI asset validator, Python compilation,
machine-readable evidence parse and secret scan, changed non-test file
credential-pattern scan, and `git diff --check` all passed. The test run emitted
only the already-known Starlette/httpx deprecation warning; it caused no skip
or failure.
