# Data Ownership and Storage Classes 1A

## Classification

`StorageClass` distinguishes intended future physical ownership:

| Storage class | Intended responsibility |
| --- | --- |
| `CRDB_TRANSACTIONAL` | Future authoritative tenant, run, routing, evidence metadata, packet, draft, verdict, personal-space, proposal, approval, committed patch, and audit transactions |
| `S3_GLOBAL_LOCKED_SNAPSHOT` | Future exact, versioned bytes of public or system-registered sources; versioning/Object Lock may be evaluated later |
| `S3_USER_PRIVATE_SNAPSHOT` | Future private user documents with owner access, export, deletion, and bounded retention |
| `EXTERNAL_DERIVED` | Corpora replicas, embedding caches, generated indexes, ingestion staging, temporary files, reports, backups, and verification inventories |
| `SESSION_EPHEMERAL` | Bounded non-authoritative session state |

This file describes contracts only. No database, bucket, cloud resource, or
snapshot is created by Step 1A.

## Private and global objects

Private user documents must be physically and logically separable from global
source snapshots. Permanent Object Lock retention must not be imposed on
private user documents because user export/deletion and bounded retention
remain contractual requirements.

Private payloads must not flow through a shared, unfiltered CDC stream. Future
CDC/export designs need ownership-aware selection and separately protected
payload references. Audit records contain hashes and metadata rather than raw
private documents, complete prompts, complete model outputs, or credentials.

## External derived storage

The prepared external volume may hold derived and recoverable large data. It
must never silently become an active CockroachDB node store, canonical
transactional memory, the sole copy of an active patch, secret storage, or an
automatic fallback to the internal drive. Active virtual environments and
database node stores remain excluded.

## Approval and commitment

Approval and physical commitment are separate future authorities. An owner or
human domain reviewer can create a bound approval record. Only a bounded
technical commit service can create the commitment receipt and activate the
approved object. Models, Knowledge HATs, Knowledge Hub, and Critic Prompt Loop
have neither authority.

The current commit receipt names `CRDB_TRANSACTIONAL` as the intended
authoritative class, but this is not an operational database or RLS claim.
Current contracts validate actor-type claims and exact object bindings; they do
not authenticate a human or service. Authentication, authorization, durable
uniqueness, approval consumption, idempotency, transactions, RLS, and audit
storage remain future application and CockroachDB responsibilities.
