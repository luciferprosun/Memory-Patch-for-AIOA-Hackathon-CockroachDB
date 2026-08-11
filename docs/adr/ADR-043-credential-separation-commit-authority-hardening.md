# ADR-043: Separate credentials by capability and harden Commit Helper authority

## Status

Proposed. It becomes accepted only when the Step 36 closure commit is
reachable on `origin/main`.

## Context

Steps 5-35 established RLS, persistence, source publication, provider calls,
Personal Memory approval/commit/activation, audit, human review and an owner
UI. The semantic service boundaries were separate, but deployment assembly
still needed an explicit fail-closed credential vocabulary and stronger proof
that one composed database LOGIN could not acquire unrelated high-risk
capabilities.

The browser must never receive a backend credential. A provider key must not
be database authority. The normal application role must not technically
commit an approved patch or publish a source. Commit Helper must not approve,
and reviewer/audit-reader identities must not mutate Personal Memory or
canonical publication state. Missing dedicated credentials must never fall
back to a migrator/admin input.

## Decision

1. Define a closed `CredentialPurpose` inventory with one exact deployment
   input per runtime capability. Load only that name and fail closed when it
   is missing. Do not support generic, admin or master fallback.
2. Wrap loaded values in an immutable, purpose-bound, non-serializable
   `SecretValue` whose normal rendering is redacted. This is containment, not
   encryption or secret storage.
3. Bind `SerializableTransactionRunner` to one credential purpose. Approval,
   Commit Helper, activation, review, source publication and audit services
   reject an unlabelled or wrong-purpose runner before SQL.
4. Keep the browser free of database URLs, Commit Helper values, provider
   keys, AWS identities, reviewer/audit credentials and public environment
   bridges. The server derives tenant/owner context from authentication.
5. Keep the provider adapter limited to `MOONSHOT_API_KEY` and expose no DB,
   Personal Memory, review, publication, S3 or external-action mutation port.
6. Preserve the Step 30 three-way authority split: owner approval uses the
   application boundary, technical commit and activation use the dedicated
   Commit Helper boundary, and neither technical service can approve.
7. Strengthen `step30_commit_helper_authorized()` so only a non-composed
   `mp_personal_memory_commit_helper` LOGIN can pass. Keep the capability role
   `NOLOGIN`, `NOBYPASSRLS`, non-admin and without schema/publication/review
   membership.
8. Move Step 9 publication transition grants from `mp_app_runtime` to a new
   exact `mp_source_publication_worker` role. Preserve ordinary source
   registration, but require dedicated service purpose, RLS, an invoker
   predicate and trigger guards for registry publication.
9. Give the mutually exclusive Step 34 reviewer and review-service roles
   distinct deployment inputs, strengthen their predicates to reject composed
   LOGINs and retain typed, least-privileged business handoff.
10. Add a physical read-only `mp_audit_reader` role. Keep audit append
    logically separated through a typed operation label on the originating
    business credential because business state and its typed audit fact must
    remain atomic under the exact business principal. Do not create a
    standalone appender secret or broad cross-business audit writer role.
11. Keep migration/admin credentials operations-only. All ordinary runtime
    roles are `NOBYPASSRLS`; no dedicated-secret failure may retry with
    migrator/admin authority.
12. Reserve `DATABASE_URL_INGESTION` in the typed inventory but record that
    current Step 10 ingestion still uses the normal application DB role. Do
    not claim a physical ingestion-role split that Step 36 did not implement.
13. Use a bounded subprocess environment allowlist and shared secret-shape
    detection/redaction. Preserve stronger audit, export, review and UI
    domain validation.
14. Treat approval, commit, activation and audit hashes as integrity
    references, never bearer credentials.
15. Leave AWS/S3 identities machine-local and separate from DB/provider/
    review/Commit Helper authority. Perform no secret retrieval, production
    rotation or AWS/S3 mutation in Step 36.
16. Leave failure injection, outage simulation and recovery campaigns to
    Step 37.

## Consequences

Capability assembly now fails early in Python and again at CockroachDB grants,
exact-role predicates, RLS/FORCE RLS and publication triggers. A LOGIN that
combines helper, reviewer, publication, audit-reader, application or admin
roles loses the protected predicate instead of accumulating authority.

The source publisher and audit reader gain narrow physical roles without
passwords in migrations. The audit appender remains a typed logical boundary
to preserve short atomic business transactions. The ingestion role is a
documented residual rather than a false least-privilege claim.

Deployments must provision separate LOGIN credentials for the named
capabilities and restart the affected consumer during rotation. Receipt
holders and browser clients cannot use hashes as credentials.

## Rejected alternatives

### Use one application database URL for every service

Rejected because it couples owner CRUD, commit, review, publication, audit
and migration authority and makes compromise impact unnecessarily broad.

### Fall back to a root or migrator URL

Rejected because missing least-privileged configuration must stop the
operation, not silently increase privilege.

### Rely only on a Python purpose label

Rejected because process-level intent is not a database authorization
boundary. Grants, RLS and exact non-composed role predicates remain required.

### Give the Commit Helper approval authority

Rejected because human approval and technical persistence are intentionally
different Step 30 authorities.

### Create a generic audit writer database role

Rejected for 1A because it would require broad cross-business writes or break
atomic business-state-plus-audit transactions. Typed appender assembly and
existing append-only controls preserve the narrower design.

### Claim ingestion is physically separated

Rejected because migration 0018 does not create an ingestion role. The
reserved credential purpose and current normal-runtime residual are recorded
explicitly.

### Put credentials in browser configuration

Rejected because frontend configuration, hidden fields and JavaScript state
are not security boundaries.

`Step 37: NOT STARTED`.
