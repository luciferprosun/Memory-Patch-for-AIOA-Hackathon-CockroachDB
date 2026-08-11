# Step 36 Credential Capability Matrix 1A

Status: implemented security contract for Step 36. Values describe ordinary
runtime authority, not administrative emergency access. `YES`, `NO`, and
`NOT_APPLICABLE` are the only capability values. A credential name identifies
its machine-local deployment input; this document contains no credential
values.

## Capability matrix

| principal | credential source | read canonical knowledge | write canonical knowledge | publish source | call provider | read Personal Memory | propose Personal Memory | approve Personal Memory | commit Personal Memory | activate Personal Memory | review cases | append audit | read audit | write S3 | delete S3 | bypass RLS | browser-visible | rotation mechanism |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Untrusted browser code | opaque session handle and CSRF token; neither is backend authority | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NOT_APPLICABLE | YES | expire session and reauthenticate |
| Authenticated owner API | `DATABASE_URL_APP` | YES | NO | NO | NO | YES | YES | YES | NO | NO | NO | YES | NO | NO | NO | NO | NO | replace login credential and restart owner API |
| Normal Kernel runtime | `DATABASE_URL_APP` | YES | YES | NO | NO | YES | YES | NO | NO | NO | NO | YES | NO | NO | NO | NO | NO | replace login credential and restart Kernel runtime |
| Model provider adapter | `MOONSHOT_API_KEY` | NO | NO | NO | YES | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NOT_APPLICABLE | NO | replace provider key and restart adapter |
| Canonical ingestion worker | `DATABASE_URL_APP` currently; `DATABASE_URL_INGESTION` reserved | YES | YES | NO | NO | NO | NO | NO | NO | NO | NO | YES | NO | NO | NO | NO | NO | replace login credential and restart ingestion worker |
| Source publication worker | `DATABASE_URL_SOURCE_PUBLICATION` | YES | NO | YES | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | replace login credential and restart publication worker |
| Personal Memory Commit Helper | `DATABASE_URL_COMMIT_HELPER` | NO | NO | NO | NO | YES | NO | NO | YES | YES | NO | NO | NO | NO | NO | NO | NO | replace login credential and restart Step 30 technical services |
| Personal Memory activation service | `DATABASE_URL_COMMIT_HELPER` | NO | NO | NO | NO | YES | NO | NO | YES | YES | NO | NO | NO | NO | NO | NO | NO | replace login credential and restart Step 30 technical services |
| Human reviewer | `DATABASE_URL_REVIEWER` | NO | NO | NO | NO | YES | NO | NO | NO | NO | YES | YES | YES | NO | NO | NO | NO | replace login credential and restart reviewer workspace |
| Review intake / handoff service | `DATABASE_URL_REVIEW_SERVICE` | NO | NO | NO | NO | YES | NO | NO | NO | NO | YES | YES | YES | NO | NO | NO | NO | replace login credential and restart review service |
| Typed audit append interface | originating business DB credential; no standalone appender secret | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | YES | NO | NO | NO | NO | NOT_APPLICABLE | rotate the originating business credential |
| Audit reader / exporter | `DATABASE_URL_AUDIT_READER` | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | YES | NO | NO | NO | NO | replace login credential and restart audit reader |
| Migration / admin operator | `DATABASE_URL_MIGRATOR` | YES | YES | YES | NO | YES | YES | YES | YES | YES | YES | YES | YES | NO | NO | YES | NO | rotate operations credential and restart migration job |
| S3 snapshot runtime | machine-local workload identity | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | NO | YES | NO | NOT_APPLICABLE | NO | replace workload identity outside repository |

## Separation notes

- `mp_source_publication_worker`, `mp_personal_memory_commit_helper`, and
  `mp_audit_reader` are distinct `NOLOGIN`, `NOBYPASSRLS` capability roles.
  Deployment composes each with a separate LOGIN and the trusted request
  context setter; no password is stored in migrations.
- Audit append remains a typed, logically separated operation in business
  transactions and has no standalone credential. The originating business
  principal retains only its own operation-specific capabilities. The audit
  reader is physically read-only and has no event insert, chain-head update,
  or business-table mutation privilege.
- Commit and activation deliberately share one narrow Step 30 technical DB
  capability. The public service interfaces and receipt/state gates are
  separate, but the credential-level matrix therefore marks both capabilities
  `YES` for each process that holds that credential.
- Reviewer and review intake/handoff are separate, mutually exclusive
  CockroachDB roles and use distinct deployment inputs.
- Step 10 ingestion remains an intentional residual normal-runtime database
  capability in the current schema. `DATABASE_URL_INGESTION` is reserved by
  the typed deployment contract, but Step 36 does not claim a new physical
  ingestion role.
- The migrator row records effective database power, not business legitimacy.
  It is deliberately broad, operations-only, never browser-visible, and never
  available as a runtime fallback.
- Receipts and hashes are integrity references, never bearer credentials.

## Rotation readiness

Replace only the named deployment input, restart the named consumer, validate
its focused positive and negative capability probes, then revoke the old LOGIN
or provider identity. Rotation of production values is deliberately outside
Step 36 and was not performed.
