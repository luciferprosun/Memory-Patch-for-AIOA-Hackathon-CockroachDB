# Credential Separation and Commit Authority Hardening 1A

## Scope

Step 36 separates runtime credentials by capability and makes protected
Personal Memory, review, source-publication and audit-read paths reject the
wrong credential before performing their operation. It extends the existing
Step 5 RLS/FORCE RLS, Step 6 transaction/idempotency, Step 9 publication,
Step 22 provider, Step 30 Commit Helper, Step 33 audit, Step 34 review and
Step 35 browser boundaries. It does not change the semantic Personal Memory
state machines.

The canonical principal/capability inventory is
`docs/security/STEP36_CREDENTIAL_CAPABILITY_MATRIX_1A.md`. Every capability
cell is explicit. The matrix describes ordinary runtime authority, not the
operations-only schema owner or emergency administrative access.

Step 36 retrieves no secret values, rotates no production credential, makes
no provider call, and performs no AWS or S3 mutation. Validation uses fake
sentinels and owned disposable CockroachDB roles only.

## Typed credential boundary

`aioa_memory_kernel.security.credentials` defines a closed
`CredentialPurpose` family and one exact deployment input per capability:

- `DATABASE_URL_APP` for owner and ordinary Kernel application work;
- `DATABASE_URL_COMMIT_HELPER` for exact Step 30 commit and activation;
- `DATABASE_URL_MIGRATOR` for operations-only schema migration;
- no standalone audit-appender secret: typed audit append uses the exact
  originating business transaction, while `DATABASE_URL_AUDIT_READER` is for
  bounded read/export assembly;
- `DATABASE_URL_REVIEWER` for the Step 34 human reviewer workspace and
  `DATABASE_URL_REVIEW_SERVICE` for mutually exclusive intake/handoff work;
- `DATABASE_URL_SOURCE_PUBLICATION` for Step 9 publication transitions;
- `DATABASE_URL_INGESTION` as the reserved ingestion deployment boundary;
- `MOONSHOT_API_KEY` for the approved Step 22 provider adapter; and
- machine-local workload identity, not an environment secret value, for S3.

`load_required_credential` loads only the environment name assigned to the
requested purpose. A missing dedicated input fails closed. It never tries a
generic `DATABASE_URL`, migrator/admin URL, provider key, or another
capability as a fallback.

`SecretValue` is immutable, purpose-bound and deliberately non-serializable.
Its string, format and representation forms are redacted. Raw access requires
an exact matching `CredentialPurpose`. This is a containment mechanism, not
home-grown encryption or a secret store.

`SerializableTransactionRunner` carries a typed credential purpose.
Capability-owning services call `require_credential_purpose` in their
constructors, so assembly with an unlabelled or wrong-purpose runner fails
before SQL. The purpose label is defense in depth; CockroachDB grants, RLS and
exact role membership remain the database authority.

## Browser and provider separation

The Step 35 browser receives only an opaque owner session and CSRF token. No
credential specification is public, and the server-rendered package contains
no public environment bridge such as `VITE_`, `NEXT_PUBLIC_`, `process.env`
or `import.meta.env`. Database URLs, Commit Helper credentials, provider keys,
AWS identities, reviewer service credentials and audit credentials remain
server-side.

The Moonshot adapter loads only `MOONSHOT_API_KEY` as
`CredentialPurpose.MODEL_PROVIDER`. A Commit Helper, application, reviewer or
migrator secret cannot be supplied as its typed provider credential. The
adapter exposes no database, Personal Memory, review, publication or external
execution mutation port. Provider exceptions and representations do not
serialize the key.

## Personal Memory approval, commit and activation

The authority split is three-part:

1. `PersonalMemoryApprovalService` requires an
   `APPLICATION_DATABASE` runner and the authenticated exact owner. It can
   create only the Step 30 owner approval receipt.
2. `PersonalMemoryCommitHelper` requires a
   `PERSONAL_MEMORY_COMMIT_DATABASE` runner. It accepts only a typed commit
   request and revalidates the approval receipt, proposal and validation
   hashes, tenant, owner, slot, expected state/version, quota, model binding,
   evidence policy and replay identity.
3. `PersonalMemoryActivationService` uses the same narrow database
   capability but a separate typed activation request and receipt gate.

The application runner cannot construct the Commit Helper or activation
service. The Commit Helper cannot construct or invoke owner approval. It has
no generic SQL, arbitrary table update, state setter, source publication,
review, provider or external-action interface.

Migration 0018 strengthens `step30_commit_helper_authorized()` so a LOGIN
must be an exact member of `mp_personal_memory_commit_helper` without admin,
owner, application, reviewer, publication or audit-reader membership. A
mistakenly composed LOGIN therefore fails even when one of its memberships is
the helper role. The existing role remains `NOLOGIN`, `NOCREATEROLE`,
`NOCREATEDB` and `NOBYPASSRLS`; deployment supplies a distinct LOGIN and the
trusted request-context capability without storing a password in SQL.

Approval, commit, activation and audit receipt hashes remain integrity and
lineage references. Possession of a hash is not authentication, a bearer
credential or permission to call another operation.

## Normal application and source publication

The ordinary `mp_app_runtime` role remains the bounded application role under
RLS/FORCE RLS. It can register source metadata and perform its existing
runtime work, but migration 0018 revokes `UPDATE` on
`source_registry_entries` and `INSERT` on `source_publication_events`. The
application credential therefore cannot perform an official publication
transition.

`mp_source_publication_worker` is a new `NOLOGIN`, `NOBYPASSRLS` capability
role. It has only the reads required to verify HAT scope and provenance, the
exact registry update and immutable publication-event insert, plus
idempotency rows constrained to `PUBLICATION_STATE_TRANSITION`.
`SourceRegistryService.transition_publication_state` additionally requires a
`SOURCE_PUBLICATION_DATABASE` runner.

`step36_source_publisher_authorized()`, RLS policies and two invoker-security
triggers require the exact, non-composed publication principal. The role has
no Personal Memory commit, review, audit administration, schema migration or
external execution authority.

## Reviewer separation

Step 34 claim, queue and decision services require a
`HUMAN_REVIEWER_DATABASE` runner backed by `DATABASE_URL_REVIEWER`. Case
intake and typed business handoff require a separate
`REVIEW_SERVICE_DATABASE` runner backed by `DATABASE_URL_REVIEW_SERVICE`.
Migration 0018 retains the existing reviewer-authorization rows and
owner/tenant checks while strengthening the reviewer and review-service
predicates to reject LOGINs that also inherit each other or application,
Commit Helper, publication, audit-reader, owner or admin capabilities.

A reviewer may perform only case-specific Step 34 work through typed
services. Reviewer credentials cannot insert a Personal Memory technical
commit, activate a patch, modify a slot/quota/model binding, publish a source,
run migrations or execute an external action. Owner approval and human review
remain different authority types.

## Audit roles and atomicity

Audit append and audit read are distinct at application assembly:

- `AuditLedgerService` labels its typed append runner as the logical
  `AUDIT_APPENDER_DATABASE` operation. That label has no standalone deployment
  secret; its connection is the exact originating business principal needed
  for the atomic operation.
- Its optional read/export runner requires `AUDIT_READER_DATABASE`.
- `mp_audit_reader` is a physical read-only `NOLOGIN`, `NOBYPASSRLS` role with
  owner-private `SELECT` on `audit_events` and `audit_chain_heads` only.
  It has no insert, head update, delete or business-table mutation grant.

Audit append is intentionally **logically separated**, not represented by a
new physical `mp_audit_appender` role in migration 0018. Existing Step 33 and
Step 34 operations append their typed audit fact in the short business
transaction whose exact principal owns the operation, preserving atomic
business-state-plus-audit semantics. Introducing a cross-business physical
appender role here would either broaden that role or break atomicity. The
typed runner purpose prevents accidental reader/appender substitution, while
the existing append-only table grants, RLS, event types and hash-chain rules
continue to govern writes.

Audit events remain observational integrity records. The audit interface
cannot manufacture a business transition. An originating business principal
retains only the authority of its typed operation, while the physical audit
reader cannot approve, commit, activate, review, publish or execute business
actions.

## Migration and RLS model

Migration 0018 is append-only relative to migration history and is manifest
version 16 for CockroachDB v26.2.4. It creates the publication and audit-reader
roles, reasserts all relevant capability roles as non-login/non-admin and
revokes cross-role inheritance. It adds no table and embeds no password.

All new authority functions are `SECURITY INVOKER`. Publication and audit
reader policies bind the trusted Step 5 tenant/user/HAT context, and all
runtime roles remain `NOBYPASSRLS`. Database triggers independently deny a
publication write performed outside the exact publisher boundary. The
migration runner validates role options, grants, policies, triggers and
function ownership against the Step 36 security manifest.

The migration/admin credential is operations-only. It is not loaded by
ordinary application, provider, Commit Helper, reviewer or browser assembly,
and a missing dedicated runtime credential never falls back to it.

## Ingestion residual

Step 10 ingestion remains an explicit residual capability of the current
normal application database role. `CredentialPurpose.INGESTION_DATABASE` and
`DATABASE_URL_INGESTION` reserve a deployment boundary, but Step 36 does not
claim that a new physical `mp_ingestion_worker` role exists. Splitting the
entire older ingestion schema would be a larger authority redesign than this
step and must be handled deliberately without weakening current ingestion
transactions. The capability matrix records the residual rather than
overstating physical isolation.

## S3 and external-volume boundary

Step 7/8 storage remains separate from database, approval, review, provider
and Commit Helper authority. `S3_RUNTIME_IDENTITY` has no environment secret
name and denotes machine-local workload identity. Browser, provider,
reviewer, Commit Helper and audit-reader principals do not receive AWS
credentials. Ordinary S3 runtime has no unrelated database authority and
Object Lock deletion assumptions remain unchanged.

`build_minimal_subprocess_environment` replaces whole-environment inheritance
for the hardened CLI/validation paths. It carries a small operational
allowlist and only explicitly named consumer inputs. This prevents a child
process that needs a database or workload identity from inheriting unrelated
provider, migration, reviewer or Commit Helper values.

The AWS subprocess boundary supports only non-secret identity/configuration
pointers for profile/config-file selection, web identity and ECS container
credentials, plus region/role selectors. It deliberately excludes raw
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` values;
the selected AWS process, not the parent application, resolves the referenced
machine-local or workload identity.

Step 36 performs no AWS/S3 request, secret retrieval or production identity
change.

## Redaction and data minimization

`aioa_memory_kernel.security.redaction` centralizes bounded detection for
secret-bearing keys, authorization/bearer values, database URLs, common
provider/Git/AWS key shapes, private keys and optional machine-local paths.
It supplies safe text/exception redaction without serializing arbitrary
objects. Step 32 owner export reuses this boundary; Step 33 audit, Step 34
review and Step 35 UI retain their stronger domain-specific validation and
are covered by cross-surface regression tests.

The credential inventory stores only names, consumers, capabilities and
rotation instructions. Validation evidence, audit events, owner exports,
review details, UI responses and logs contain no raw secret. Fake sentinels
are used to prove this behavior.

## Rotation readiness

Rotation is an operations procedure, not an application fallback:

1. provision the replacement under the same exact deployment input;
2. restart or reload only the named consumer;
3. run that capability's positive and negative probes;
4. verify RLS, cross-role denial and redaction; and
5. revoke the old LOGIN, provider key or workload identity.

No service tries a previous, generic, admin or master credential after a
failure. Step 36 documents this procedure but does not rotate any production
value.

## Step 37 boundary

Step 36 adds no failure-injection framework, chaos proxy, kill/restart
campaign, network partition, provider outage, CockroachDB outage, S3 outage
or external-volume corruption test. Step 37 owns failure injection and
recovery. `Step 37: NOT STARTED`.
