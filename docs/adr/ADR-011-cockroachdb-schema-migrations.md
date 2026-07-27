# ADR-011: CockroachDB Logical Schema and Forward-Only Migrations

- Status: Accepted
- Date: 2026-07-27

## Context

The Step 1 and Step 2 contracts require exact tenant and personal-memory
ownership, immutable evidence binding, separate proposal/approval/commit
records, and non-authoritative HAT and routing data. Step 3 pinned
CockroachDB `v26.2.4` and proved the relevant structural capabilities, but its
disposable probe tables were not a production schema.

Step 4 needs a maintainable schema that can be recreated on limited local
hardware and can accept Step 5 RLS without destructive key changes. The public
contracts use opaque string identifiers. They do not pin an embedding model or
dimension.

## Decision

Adopt the `memory_patch` SQL schema and the manifest in
[`sql/cockroachdb/migrations/manifest.json`](../../sql/cockroachdb/migrations/manifest.json).
The initial foundation contains three ordered, forward-only SQL files:

1. identity, HAT manifests, Personal Memory HATs, and explicit HAT scopes;
2. source/snapshot/version/chunk lineage and full-text search documents;
3. Kernel, evidence, governed-memory, binding, and audit facts.

Use opaque application-supplied `STRING` identifiers to preserve the Kernel
wire contract. Every tenant-owned dependency propagates `tenant_id`.
`hat_scopes` is a strict tagged union:

- a shared Knowledge HAT has manifest identity and no personal owner;
- a private Personal Memory HAT has an exact tenant/user/space owner and no
  shared manifest identity.

Source lineage uses composite foreign keys through source, snapshot, version,
and chunk. Evidence deletion is restrictive. Stored routes and authority
claims are facts; no trigger, generated authority column, role, or policy
executes a transition.

Use an explicit TSVECTOR table with a per-row `simple`, `english`, or `german`
dictionary and a migration-managed inverted index.

Do not create a VECTOR column or index in this migration set. A later bounded
decision must first pin the embedding model identity and dimension. Step 3’s
synthetic `VECTOR(3)` fixture is not a production dimension.

The migration runner:

- verifies the exact CockroachDB v26.2.4 binary pin;
- validates stable IDs, filenames, ordering, and SHA-256;
- applies each migration and its bookkeeping row in one explicit transaction;
- fails on unknown migration IDs or checksum drift;
- performs no automatic retry of arbitrary migration SQL;
- treats a second invocation as a checksum-verified no-op;
- requires an explicit live flag and bounded timeouts;
- permits destructive test cleanup only for a generated `mp_step4_` database.

## Alternatives rejected

- A heavyweight migration dependency was rejected because the standard-library
  runner satisfies the current fixed-order, checksum, transaction, and
  discovery requirements without changing dependencies.
- Dynamically generated migrations were rejected because they would not be
  reviewable immutable repository artifacts.
- A single nullable owner on source or chunk rows was rejected because it
  could blur shared and private scope.
- Separate duplicate shared/private lineage table families were rejected in
  favor of one explicit, strongly constrained scope root.
- UUID database types and generated-ID defaults were rejected because they
  would change the established string identifier contract.
- Workflow triggers were rejected because they would hide Kernel authority in
  the database.
- A fabricated vector dimension was rejected because no canonical contract or
  configuration fixes it.
- Down migrations were rejected for this foundation. No rollback-safety claim
  is made.

## Consequences

Positive:

- a fresh v26.2.4 database is reproducible from three immutable files;
- 29 tables have a machine-readable expected-object manifest;
- composite foreign keys make tenant, HAT, and owner changes structurally
  visible and reject mismatched lineage;
- later RLS policies can predicate on existing tenant and owner columns;
- approval and commit bindings are inspectable without conflating them;
- migration drift and false applied state fail closed.

Constraints:

- migrations are forward-only and require explicit operational observation;
- a failed migration is surfaced and not recorded, but manual diagnosis may be
  required before rerun if CockroachDB reports a non-transactional operational
  condition;
- static checks do not authenticate a claimed approver or consume an approval;
- no SQL user is isolated until Step 5 roles, session context, RLS, and FORCE
  RLS are implemented and tested;
- no application persistence semantics exist until Step 6;
- vector storage requires a later reviewed migration;
- production-like distributed and schema-change behavior must be revalidated
  before deployment.

The complete table inventory, diagram, commands, and enforcement boundary are
in the
[Step 4 architecture baseline](../architecture/COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md).
