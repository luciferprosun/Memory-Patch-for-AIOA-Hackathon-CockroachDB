# AIOA Memory Patch / Knowledge Chat provenance import

This repository isolates the Memory Patch and Knowledge Chat layer of AIOA for
the CockroachDB hackathon. The Critical Loop is a separate AIOA layer and is
intentionally outside this submission.

This import contains reports, plans, inventories and validation evidence
created or substantively updated from June 30, 2026 through July 24, 2026.
Importing historical reports does not mean that a final CockroachDB
implementation already exists. The repository now contains the domain-neutral
Step 1 kernel contract foundation and the live-tested CockroachDB v26.2.4
capability baseline, plus the Step 4 tenant-ready logical schema and the Step
5 SQL-enforced tenant/user isolation boundary. Step 6 adds the typed,
retry-safe persistence, durable idempotency/resume, and immutable-write
foundation. Step 9 adds a live-validated typed source registry, deterministic
provenance DAG, publication eligibility, append-only publication events, and
optimistic state transitions. Step 7 adds live-validated immutable S3 snapshot
storage, and Step 8 adds a live-validated fail-closed external-volume runtime
boundary. Step 10 adds the live-recovered durable S3-CockroachDB ingestion
saga while preserving those storage and Step 9 publication boundaries. Step
11 adds deterministic plain-text and JSON parsing, Unicode NFC normalization,
sectioning, model-neutral chunking, static security findings, durable parsed
artifacts, and real Step 10 parser/validator ports. Step 12 adds the trusted
installed-HAT registry/runtime boundary. Step 13 adds
the production German Law HAT manifest, explicit request/scope contracts,
source-authority and temporal policies, and fixed non-fetching metadata
adapters without touching a corpus. Production driver wiring and later
integrations remain future roadmap work. Step 14 adds the bounded read-only
German-law corpus inventory, evidence-only deduplication, explicit
license/privacy/quarantine classifications, a canonical external bundle, and
idempotent Step 9 registration validation with zero publication. Step 15 adds
digest-bound temporal and jurisdictional normalization, document/version and
supersession candidates, preserved conflicts, and review-only Step 9 proposals
without publication or legal-question resolution.

The historical provenance import intentionally excludes raw German-law
datasets, raw UNIX corpus records, retrieval-index data, application code and
provider secrets. Every imported document has source commit, ref, date and
SHA-256 provenance.

## Start here

- [Canonical production roadmap](roadmap/PRODUCTION_ROADMAP.md)
- [Canonical Step 2 closure record](audits/STEP_2_KERNEL_CONTRACT_REAUDIT_CLOSURE_1A.md)
- [CockroachDB v26.2 capability baseline 1A](architecture/COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md)
- [ADR-010: CockroachDB v26.2 version pin](adr/ADR-010-cockroachdb-v26-2-version-pin.md)
- [Step 3 capability matrix](evidence/cockroachdb-v26-2/capability-matrix.json)
- [Step 3 closure record](audits/STEP_3_COCKROACHDB_CAPABILITY_SPIKE_CLOSURE_1A.md)
- [Immutable CockroachDB version pin](../config/cockroachdb/version-pin.json)
- [CockroachDB logical schema and migration foundation 1A](architecture/COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md)
- [ADR-011: CockroachDB logical schema and forward-only migrations](adr/ADR-011-cockroachdb-schema-migrations.md)
- [Step 4 live schema validation evidence](evidence/cockroachdb-v26-2/step4-schema-validation.json)
- [Step 4 closure record](audits/STEP_4_COCKROACHDB_SCHEMA_MIGRATION_CLOSURE_1A.md)
- [CockroachDB tenant roles, session context and RLS 1A](architecture/COCKROACHDB_TENANT_ROLES_SESSION_CONTEXT_AND_RLS_1A.md)
- [ADR-012: CockroachDB tenant roles, transaction context and forced RLS](adr/ADR-012-cockroachdb-tenant-roles-session-context-rls.md)
- [Step 5 live RLS validation evidence](evidence/cockroachdb-v26-2/step5-rls-validation.json)
- [Step 5 closure record](audits/STEP_5_TENANT_RLS_CLOSURE_1A.md)
- [CockroachDB persistence, idempotency, and retry foundation 1A](architecture/COCKROACHDB_PERSISTENCE_IDEMPOTENCY_RETRY_FOUNDATION_1A.md)
- [ADR-013: CockroachDB persistence, idempotency, and retry boundary](adr/ADR-013-cockroachdb-persistence-idempotency-retry-boundary.md)
- [Step 6 live persistence validation evidence](evidence/cockroachdb-v26-2/step6-persistence-validation.json)
- [Step 6 closure record](audits/STEP_6_PERSISTENCE_IDEMPOTENCY_RETRY_CLOSURE_1A.md)
- [S3 Snapshot Authority and Object Lock Adapter 1A](architecture/S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_ADAPTER_1A.md)
- [ADR-015: S3 snapshot Object Lock and CloudFormation boundary](adr/ADR-015-s3-snapshot-object-lock-and-cloudformation-boundary.md)
- [Step 7 CloudFormation deployment plan](operations/STEP_7_S3_CLOUDFORMATION_DEPLOYMENT_1A.md)
- [Step 7 live S3 validation evidence](evidence/aws-s3/step7-s3-snapshot-validation.json)
- [Step 7 closure record](audits/STEP_7_S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_CLOSURE_1A.md)
- [Step 8 readiness handoff from Step 7](operations/STEP_8_READINESS_HANDOFF_FROM_STEP_7_1A.md)
- [External-volume runtime adapter and fail-closed policy 1A](architecture/EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md)
- [ADR-016: External-volume runtime fail-closed boundary](adr/ADR-016-external-volume-runtime-fail-closed-boundary.md)
- [Step 8 external-volume live validation](operations/STEP_8_EXTERNAL_VOLUME_LIVE_VALIDATION_1A.md)
- [Step 8 live external-volume evidence](evidence/external-volume/step8-external-volume-validation.json)
- [Step 8 closure record](audits/STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md)
- [Source registry, provenance, and publication states 1A](architecture/SOURCE_REGISTRY_PROVENANCE_PUBLICATION_STATES_1A.md)
- [ADR-014: Source registry, provenance, and publication boundary](adr/ADR-014-source-registry-provenance-publication-boundary.md)
- [Steps 7 and 8 explicit deferral record](audits/STEP_7_STEP_8_EXPLICIT_DEFERRAL_2026_07_29.md)
- [Step 9 live source-registry validation evidence](evidence/cockroachdb-v26-2/step9-source-registry-validation.json)
- [Step 9 closure record](audits/STEP_9_SOURCE_REGISTRY_PROVENANCE_PUBLICATION_CLOSURE_1A.md)
- [Idempotent S3-CockroachDB ingestion saga 1A](architecture/IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_1A.md)
- [ADR-017: Idempotent ingestion saga boundary](adr/ADR-017-idempotent-s3-cockroachdb-ingestion-saga-boundary.md)
- [Step 10 live validation and recovery runbook](operations/STEP_10_INGESTION_SAGA_LIVE_VALIDATION_1A.md)
- [Step 10 failed first-attempt evidence](evidence/ingestion/step10-ingestion-saga-validation-failure.json)
- [Step 10 successful recovery evidence](evidence/ingestion/step10-ingestion-saga-validation.json)
- [Step 10 closure record](audits/STEP_10_IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_CLOSURE_1A.md)
- [Generic parsing, normalization and chunking pipeline 1A](architecture/GENERIC_PARSING_NORMALIZATION_CHUNKING_PIPELINE_1A.md)
- [ADR-018: Generic parsing, normalization and chunking boundary](adr/ADR-018-generic-parsing-normalization-chunking-boundary.md)
- [Step 11 zero-external-write live-validation runbook](operations/STEP_11_PARSING_PIPELINE_LIVE_VALIDATION_1A.md)
- [Step 11 successful live evidence](evidence/parsing/step11-parsing-pipeline-validation.json)
- [Step 11 closure record](audits/STEP_11_GENERIC_PARSING_NORMALIZATION_CHUNKING_PIPELINE_CLOSURE_1A.md)
- [HAT registry, manifest validation and runtime boundary 1A](architecture/HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_1A.md)
- [ADR-019: Trusted HAT registry and runtime boundary](adr/ADR-019-trusted-hat-registry-runtime-boundary.md)
- [Step 12 controlled validation runbook](operations/STEP_12_HAT_REGISTRY_LIVE_VALIDATION_1A.md)
- [Step 12 controlled validation evidence](evidence/hats/step12-hat-registry-validation.json)
- [Step 12 closure record](audits/STEP_12_HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_CLOSURE_1A.md)
- [German Law HAT and source-authority boundary 1A](architecture/GERMAN_LAW_HAT_SOURCE_AUTHORITY_POLICY_1A.md)
- [ADR-020: German Law trusted policy HAT](adr/ADR-020-german-law-hat-source-authority-policy.md)
- [Step 13 official-source research](provenance/STEP_13_OFFICIAL_SOURCE_RESEARCH_1A.md)
- [Step 13 controlled validation evidence](evidence/hats/step13-german-law-hat-policy-validation.json)
- [Step 13 closure record](audits/STEP_13_GERMAN_LAW_HAT_SOURCE_AUTHORITY_CLOSURE_1A.md)
- [German Law corpus inventory, deduplication and source registration 1A](architecture/GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_1A.md)
- [ADR-021: German Law corpus inventory and non-destructive deduplication](adr/ADR-021-german-law-corpus-inventory-dedup-source-registration.md)
- [Step 14 controlled inventory and registration runbook](operations/STEP_14_GERMAN_LAW_CORPUS_INVENTORY_VALIDATION_1A.md)
- [Step 14 controlled validation evidence](evidence/corpus/step14-german-law-corpus-inventory-summary.json)
- [Step 14 closure record](audits/STEP_14_GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_CLOSURE_1A.md)
- [German Law temporal and jurisdictional normalization 1A](architecture/GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_1A.md)
- [ADR-022: Preserve temporal and jurisdiction facts as reviewable evidence](adr/ADR-022-german-law-temporal-jurisdictional-normalization.md)
- [Step 15 controlled normalization runbook](operations/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_VALIDATION_1A.md)
- [Step 15 controlled validation evidence](evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json)
- [Step 15 closure record](audits/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_CLOSURE_1A.md)
- [Step 16 controlled publication runbook](operations/STEP_16_GERMAN_LAW_HAT_PUBLICATION_VALIDATION_1A.md)
- [Step 16 controlled validation evidence](evidence/corpus/step16-german-law-hat-publication-summary.json)
- [Step 16 closure record](audits/STEP_16_GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_CLOSURE_1A.md)
- [Knowledge Kernel Contract Baseline 1A](architecture/KNOWLEDGE_KERNEL_CONTRACT_BASELINE_1A.md)
- [Personal Memory HATs 1A](architecture/PERSONAL_MEMORY_HATS_1A.md)
- [Memory Trust and Precedence 1A](architecture/MEMORY_TRUST_AND_PRECEDENCE_1A.md)
- [Knowledge HAT SDK Contract 1A](architecture/HAT_SDK_CONTRACT_1A.md)
- [Multi-Tenant Isolation Contract 1A](architecture/MULTI_TENANT_ISOLATION_CONTRACT_1A.md)
- [Data Ownership and Storage Classes 1A](architecture/DATA_OWNERSHIP_AND_STORAGE_CLASSES_1A.md)
- [External data volume contract](EXTERNAL_DATA_VOLUME.md)
- [Knowledge Module Migration Handoff 1B](history/2026-06-30_to_2026-07-24/plans/KNOWLEDGE_MODULE_MIGRATION_HANDOFF_1B.md)
- [Provider-Independent Knowledge Context Bridge 1A](history/2026-06-30_to_2026-07-24/memory-and-retrieval/PROVIDER_INDEPENDENT_KNOWLEDGE_CONTEXT_BRIDGE_1A.md)
- [German Federal Law Knowledge HAT integration map](history/2026-06-30_to_2026-07-24/german-law-hat/KNOWLEDGE_HAT_INTEGRATION_MAP.md)
- [README Knowledge and Memory Layer excerpt](history/2026-06-30_to_2026-07-24/mixed-document-excerpts/README_KNOWLEDGE_SECTIONS.md)
- [Architect handoff Knowledge sections](history/2026-06-30_to_2026-07-24/mixed-document-excerpts/START_HERE_ARCHITECT_KNOWLEDGE_SECTIONS.md)

## Provenance and audit

- [Human-readable import manifest](provenance/KNOWLEDGE_CHAT_IMPORT_MANIFEST.md)
- [CSV import manifest](provenance/KNOWLEDGE_CHAT_IMPORT_MANIFEST.csv)
- [Source repository snapshot](provenance/SOURCE_REPOSITORY_SNAPSHOT.md)
- [Discovery method](audits/DISCOVERY_METHOD.md)
- [Included files](audits/INCLUDED_FILES.md)
- [Excluded candidates](audits/EXCLUDED_CANDIDATES.md)
- [Potential gaps](audits/POTENTIAL_GAPS.md)

The manifest is authoritative for the 42 imported records: 40 complete copies
and two clearly marked partial excerpts.
