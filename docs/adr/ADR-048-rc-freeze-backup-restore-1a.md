# ADR-048: Freeze the RC and prove native backup through isolated restore

Status: Accepted only when the Step42 closure commit is reachable from
`origin/main`.

## Context

Step41 proves the current system's security and regression state. A release
candidate still needs one reproducible identity and evidence that its
authoritative persisted state can be backed up and restored without widening
authority, losing provenance, corrupting Personal Memory/audit state, leaking
secrets, or returning a known-bad answer.

File existence, SQL dumps, filesystem copies of a running CockroachDB store,
and cache archives do not prove that contract. The repository already pins
CockroachDB v26.2.4, uses migration replay, immutable source-object identities,
derived external caches, purpose-bound credentials, and canonical SHA-256
contracts.

## Decision

1. Freeze the RC from the pushed Step41 parent plus a deterministic manifest of
   runtime source/config/schema/SQL/dependencies and existing system identities.
   Record the final Step42 commit separately to avoid self-reference.
2. Treat CockroachDB as `AUTHORITATIVE_BACKUP_REQUIRED`; use its native database
   backup and restore mechanism for the controlled fixture.
3. Treat exact immutable source-object versions and the pushed Git RC as
   `AUTHORITATIVE_EXTERNALLY_PROTECTED`; Step42 performs no AWS mutation.
4. Treat embedding/model caches and derived embeddings as rebuildable from
   frozen canonical inputs. Never call them authoritative.
5. Exclude every credential and secret value from archives. Restore uses the
   existing Step36 purpose-bound provisioning paths.
6. Back up only after migration replay and a complete source/provenance,
   retrieval, Personal Memory, and audit fixture reach a deterministic
   watermark.
7. Bind native backup validation and a sorted file/size/SHA-256 tree receipt to
   the RC manifest.
8. Restore only into a distinct, validated Step42 disposable runtime and
   database. Apply cluster-role bootstrap separately, restore under a new name,
   and require 18/18 migration replay skips.
9. Require exact watermark equality plus post-restore RLS/role, source,
   retrieval/temporal, model/verifier, Personal Memory, audit-tamper, Critic
   optionality, and 4 GB profile checks.
10. Remove the backup tree and both runtime stores after validation. The local
    proof is not production HA/DR or KMS-encrypted backup certification.
11. Freeze runtime semantics at the pushed Step42 commit. Step43 cannot change
    them without reopening this RC.

## Consequences

- Recovery evidence proves application coherence and authority, not merely
  successful database startup.
- The controlled validation is slower because it applies the full migration
  chain and runs real retrieval/model paths before and after native recovery.
- A production deployment still needs its own encrypted remote backup
  destination, retention, access control, and managed multi-node drill.
- Large derived artifacts need not be duplicated, but their exact model/corpus
  inputs remain frozen and verified.
- Backup/restore code has no AWS SDK, secret-store, approval, commit,
  activation, source-publication, or external-action call surface.

## Rejected alternatives

- A SQL dump was rejected because it is not the native mechanism selected for
  the complete CockroachDB state fixture.
- Copying the live store directory was rejected as an unsafe consistency and
  ownership model.
- Treating external cache or embeddings as authority was rejected.
- Restoring over the source or a shared database was rejected.
- Embedding secrets in an archive for convenience was rejected.
- Declaring success from backup file presence without restore and application
  validation was rejected.
