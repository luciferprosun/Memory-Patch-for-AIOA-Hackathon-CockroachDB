# Architecture, ADR, and Security

The repository contains 50 architecture records, 48 ADRs, and three focused
security documents in the curated source set. Their exact paths and hashes are
listed in the [artifact manifest](../manifest/artifact-manifest.json).

## Core architecture

- [Memory Patch system overview](../../docs/architecture/MEMORY_PATCH_SYSTEM_OVERVIEW_1A.md)
- [Knowledge Kernel contract](../../docs/architecture/KNOWLEDGE_KERNEL_CONTRACT_BASELINE_1A.md)
- [HAT SDK contract](../../docs/architecture/HAT_SDK_CONTRACT_1A.md)
- [Memory trust precedence](../../docs/architecture/MEMORY_TRUST_AND_PRECEDENCE_1A.md)
- [Project boundary](../../docs/architecture/PROJECT_BOUNDARY.md)

## CockroachDB architecture

- [Capability baseline](../../docs/architecture/COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md)
- [Logical schema and migrations](../../docs/architecture/COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md)
- [Tenant roles and RLS](../../docs/architecture/COCKROACHDB_TENANT_ROLES_SESSION_CONTEXT_AND_RLS_1A.md)
- [Persistence/idempotency/retry](../../docs/architecture/COCKROACHDB_PERSISTENCE_IDEMPOTENCY_RETRY_FOUNDATION_1A.md)
- [RC freeze and restore](../../docs/architecture/RC_FREEZE_BACKUP_RESTORE_1A.md)

## Authority and security

- [Credential separation architecture](../../docs/architecture/CREDENTIAL_SEPARATION_COMMIT_AUTHORITY_HARDENING_1A.md)
- [Purpose-bound credential matrix](../../docs/security/STEP36_CREDENTIAL_CAPABILITY_MATRIX_1A.md)
- [Step41 threat matrix](../../docs/security/STEP41_FULL_SECURITY_THREAT_MATRIX_1A.md)
- [Repository threat model](../../docs/security/Memory-Patch-for-AIOA-Hackathon-CockroachDB-threat-model.md)
- [All ADRs](../../docs/adr/)

Model output, Personal Memory, HATs, and Critic output remain separate from
canonical evidence and from approval, commit, activation, and execution
authority.
