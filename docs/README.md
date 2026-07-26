# AIOA Memory Patch / Knowledge Chat provenance import

This repository isolates the Memory Patch and Knowledge Chat layer of AIOA for
the CockroachDB hackathon. The Critical Loop is a separate AIOA layer and is
intentionally outside this submission.

This import contains reports, plans, inventories and validation evidence
created or substantively updated from June 30, 2026 through July 24, 2026.
Importing historical reports does not mean that a final CockroachDB
implementation already exists. The repository now also contains the
domain-neutral Step 1 kernel contract foundation; later persistence and
integration remain future roadmap work.

The historical provenance import intentionally excludes raw German-law
datasets, raw UNIX corpus records, retrieval-index data, application code and
provider secrets. Every imported document has source commit, ref, date and
SHA-256 provenance.

## Start here

- [Canonical production roadmap](roadmap/PRODUCTION_ROADMAP.md)
- [Canonical Step 2 closure record](audits/STEP_2_KERNEL_CONTRACT_REAUDIT_CLOSURE_1A.md)
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
