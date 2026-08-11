# Step 36 Credential Separation and Commit Authority Closure 1A

## Starting point and scope

- Exact Step 35 base: `6b2948fc371bbac1b5d48403d65bf7efadd8f56d`.
- Step 35 was complete and pushed; Steps 36 and 37 were not started.
- Scope is limited to credential-purpose separation, least-privileged database
  authority, Commit Helper hardening, secret containment/redaction and
  fail-closed runtime assembly.
- No production secret was retrieved, rotated or committed. No provider call,
  AWS/S3 mutation, external action or failure-injection campaign was run.
- This record does not invent the final Step 36 Git closure SHA before that
  commit exists.

## Credential and principal inventory

Step 36 adds a closed credential-purpose inventory with an explicit consumer,
source boundary, browser-visibility classification and rotation procedure for
each runtime capability. The canonical inventory and complete `YES` / `NO` /
`NOT_APPLICABLE` capability matrix are recorded in
`docs/security/STEP36_CREDENTIAL_CAPABILITY_MATRIX_1A.md`.

The typed deployment inputs distinguish the normal application, Personal
Memory technical services, migrator, audit reader, human reviewer, review
service, source publisher, reserved ingestion boundary and model provider.
Audit append remains a typed logical operation using the exact originating
business transaction rather than a broad standalone audit-writer credential.
S3 uses machine-local workload identity instead of an application environment
secret.

`SecretValue` is immutable, purpose-bound, non-serializable and redacted in
normal string, format and representation paths. `load_required_credential`
loads only the exact name assigned to the requested capability. A missing or
wrong-purpose credential fails closed; there is no generic, master,
migration/admin or unrelated-capability fallback. Child-process assembly uses
a minimal allowlist rather than inheriting the entire parent environment.

Receipts and hashes remain integrity and lineage references, not bearer
credentials. Their possession does not grant approval, commit, activation,
review, publication or execution authority.

## Database roles and migration 0018

Append-only migration
`sql/cockroachdb/migrations/0018_step36_credential_authority_hardening.sql`
is registered in manifest/runner schema version 16 for CockroachDB v26.2.4.
Its SHA-256 is
`8ef9ab3a7ea7908b7e8bb408c385076d32c9d7f35ec854962624fd0ff1edf12c`.
The repository now has 18 migrations; no historical migration was edited.

Migration 0018 creates the narrow `mp_source_publication_worker` and
read-only `mp_audit_reader` capability roles and reasserts the existing
application, Commit Helper, reviewer and review-service roles as `NOLOGIN`,
`NOCREATEROLE`, `NOCREATEDB`, non-admin and `NOBYPASSRLS`. It revokes
cross-role inheritance, narrows grants and strengthens exact-role predicates
so deliberately composed LOGINs cannot accumulate protected authority.

The migration also:

- strengthens `step30_commit_helper_authorized()` and the Step 34 reviewer /
  review-service predicates;
- moves official Source Registry publication transitions out of the normal
  application role and behind the exact source-publisher role, RLS policies
  and invoker-security triggers;
- limits the audit reader to owner-private reads of the Step 33 ledger and
  chain heads, with no append, head update, delete or business mutation;
- preserves tenant/user context, RLS and FORCE RLS on protected data; and
- stores no password, token, DSN or other credential value.

Deployment supplies separate LOGIN identities to these NOLOGIN capability
roles. The migrator/schema owner retains necessarily broad effective database
power, but it is operations-only and is never a runtime or missing-secret
fallback.

## Browser, provider and Personal Memory authority

The Step 35 browser receives only its opaque session handle and CSRF token.
Trusted tenant and owner context remains server-derived. The frontend exposes
no database URL, Commit Helper credential, provider key, reviewer/audit
credential, migration credential, AWS identity or public environment bridge.
Fake privileged sentinels are rendered through the browser surface during
security tests and must produce zero hits.

The provider adapter consumes only `MOONSHOT_API_KEY` as the typed model-
provider capability. It exposes no database, Personal Memory, reviewer,
source-publication, S3 or external-action mutation port, and provider failures
are redacted before reaching downstream surfaces.

Step 30 authority remains split:

1. the authenticated owner approval service uses the application boundary;
2. `PersonalMemoryCommitHelper` uses the dedicated narrow technical database
   boundary and revalidates the approval receipt, proposal/validation hashes,
   tenant, owner, slot, expected state/version, quota, binding, evidence and
   replay identity; and
3. activation uses a separate typed request/receipt gate over the same narrow
   Step 30 technical credential.

The normal application credential cannot independently perform a protected
technical commit. The Commit Helper cannot approve, alter the approved
payload, switch owner/tenant/slot, migrate, publish a source, review a case,
call a provider or execute an external action. Commit and activation share one
least-privileged technical database capability; their semantic authority
remains separated by typed service contracts and exact state/receipt gates.

## Reviewer, audit, publication, migrator and storage boundaries

Human review and review intake/handoff use distinct deployment inputs and
mutually exclusive database roles. Reviewer authority remains limited to the
exact Step 34 case workflow. It cannot approve an owner patch, technically
commit or activate it, mutate quota/model binding, publish a source, migrate
the schema or execute an external action.

Audit append remains logically separated and atomic with the exact business
transaction whose typed principal owns the event. No broad physical audit
appender or standalone audit secret was introduced. The physical audit-reader
role is read-only and cannot append or mutate business state. Audit events
remain observational integrity records and create no business authority.

Source publication uses its own exact worker role and typed service purpose;
the normal application, provider, Commit Helper, reviewer and audit reader do
not gain official-publication authority. The migration/admin identity is
operations-only, never browser-visible and never used as a fallback.

Step 7/8 storage remains a separate machine-local workload-identity boundary.
Browser, provider, Commit Helper, reviewer and audit-reader principals receive
no AWS credential, and S3 runtime receives no unrelated database authority.
Step 36 performed no AWS/S3 request, deletion, credential retrieval or
production identity change.

## Redaction, fail-closed behavior and rotation readiness

Central bounded redaction recognizes secret-bearing keys, authorization and
bearer values, database URLs, common provider/Git/AWS token shapes, private
key blocks and optional machine-local paths. Secret-bearing exceptions and
fake sentinels are rejected or redacted across logs, Step 33 audit/export,
Step 32 owner export, Step 34 review and Step 35 UI responses. No raw
credential value belongs in source, tests, validation evidence or docs.

Each privileged input documents its consumer and rotation procedure:
provision a replacement for the same exact purpose, restart/reload only that
consumer, run its positive and negative capability probes, then revoke the old
identity. Step 36 makes this rotation-ready but deliberately performs no
production rotation.

Missing dedicated configuration stops the operation. It does not retry with a
broader role, a generic database URL, migrator/admin authority or an unrelated
secret. Browser-supplied tenant/owner values, model text and copied receipt
hashes cannot change trusted scope or unlock another capability.

## Validation status

The following repository gates were already confirmed against the current
Step 36 worktree:

- all three Step 36 focused suites: 36 tests, failures `0`, errors `0`;
- full repository discovery: 1,915 tests, failures `0`, errors `0`;
- focused Steps 5-7, 9, 22, 27 and 30-35 authority/security regressions:
  539 tests, failures `0`, errors `0`;
- contract validator: `PASS`;
- pinned frontend asset-integrity check: `PASS`; and
- offline CockroachDB migration/manifest validation, including migration
  0018: `PASS`.

The controlled Step 36 credential-authority validation is `PASS`. Its one
owned disposable CockroachDB v26.2.4 runtime applied all 18 migrations,
replayed the chain without reapplication, verified the Step 36 catalog,
exercised exact and deliberately composed roles, completed the real Step 30
Commit Helper and Step 34 review regressions, and removed all nine temporary
LOGINs, the database, process, ports and memory store without force kill.
Browser, log, audit, export, review and UI leakage counts are all zero; every
forbidden authority flag is false. The sanitized evidence is
`docs/evidence/security/step36-credential-authority-validation.json`, and its
canonical validation digest is
`0f083f161a843a33152e21c8577a992162031682f2824d7febca8b9f170619f8`.

## Known limitations and retained boundaries

- Step 10 ingestion still uses the normal application database role.
  `DATABASE_URL_INGESTION` is reserved, but Step 36 does not claim a physical
  ingestion-worker role split.
- Audit append is a typed logical boundary on originating business
  transactions, not a new physical cross-business audit-appender role.
- Technical commit and activation intentionally share the same narrow Step 30
  database credential; typed request, state and receipt gates separate their
  semantics.
- The migrator remains broadly privileged by operational necessity and must
  stay outside ordinary runtime assembly.
- S3 workload identity/profile selection and production secret provisioning
  are deployment responsibilities; Step 36 validates separation without
  retrieving or rotating their values.
- No production provider, AWS/S3 or external-action path was exercised.
- No failure-injection framework, outage simulation, kill/restart campaign,
  network-partition harness or recovery campaign was implemented.

`Step 37: NOT STARTED`.
