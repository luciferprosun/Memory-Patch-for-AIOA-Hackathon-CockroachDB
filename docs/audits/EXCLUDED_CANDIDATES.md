# Excluded Candidates

All 105 excluded documentation-format candidates are accounted for below. The count is based on unique paths changed in the exact Git date window.

## Most important exclusions

- The four foundational Memory Layer architecture documents were created before June 30. Their only in-window change was an AIOA branding substitution, so they were not misrepresented as hackathon-window work.
- Thirteen normalized UNIX corpus records and the lexical postings index were excluded as raw corpus/index data.
- German Federal Law catalog and binding JSON files were excluded as runtime configuration; the documentation map was imported instead.
- Whole-repository handoff/freeze inventories were excluded because they mix the entire application into a Memory-specific provenance repository.
- Critical/Orchestra, provider, desktop-demo and CI material was excluded when Knowledge terminology was incidental.

## Complete accounting

| Original path | Exclusion reason |
|---|---|
| `.github/workflows/ci.yml` | Application configuration, packaging or CI material; not documentation provenance. |
| `AGENTS.md` | Repository operating/governance material with only incidental relevance (Category C). |
| `AUTHORITY_SCOPE.md` | Repository operating/governance material with only incidental relevance (Category C). |
| `CONTRIBUTING.md` | Repository operating/governance material with only incidental relevance (Category C). |
| `CURRENT_STATE.md` | Whole-project status/roadmap material; Knowledge Hats or retrieval are incidental and add no substantive Memory Layer plan beyond imported sources (Category C). |
| `PROTOTYPE_FREEZE_CHECKLIST_1A.md` | General controlled-agent freeze report; Knowledge Hub/Memory Hats appear only as explicitly unstarted out-of-scope items (Category C). |
| `RELEASE_FREEZE_1A.md` | General controlled-agent freeze report; Knowledge Hub/Memory Hats appear only as explicitly unstarted out-of-scope items (Category C). |
| `ROADMAP.md` | Whole-project status/roadmap material; Knowledge Hats or retrieval are incidental and add no substantive Memory Layer plan beyond imported sources (Category C). |
| `STATUS.md` | Whole-project status/roadmap material; Knowledge Hats or retrieval are incidental and add no substantive Memory Layer plan beyond imported sources (Category C). |
| `apps/aoia_desktop_demo/README.md` | Desktop-demo documentation whose in-window subject is the general competition UI, not Knowledge Chats or the Memory Layer. |
| `apps/aoia_desktop_demo/knowledge/hats/catalog_entries/german_federal_employment_worker_law.json` | Application configuration, packaging or CI material; not documentation provenance. |
| `config/knowledge_hats/local_bindings.example.json` | Application configuration, packaging or CI material; not documentation provenance. |
| `data/architect_handoff_manifest_1a.json` | Generated whole-repository handoff/freeze inventory; primarily covers the entire application and would import broad non-Memory metadata. |
| `data/final_repository_freeze_1a/freeze_manifest.json` | Generated whole-repository handoff/freeze inventory; primarily covers the entire application and would import broad non-Memory metadata. |
| `data/unix_corpus_ingestion_1b/intake/records/01ef819d99f267f4ab10dc895ae3e3d79d0f06da07fd0ee8333e338e7d0691e7.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/0b1d122389c97a417a1e8f0b70b9530e43dfec5a939f3b69395b4b541a81f56c.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/345eb96669b0db2c5e4ed78d0bcf7ca514fb2e2f231148c25bd82114801d531d.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/93dd379ced45a438882af5b30a5c935b79a02f23b5c144c47907e988ae9d2769.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/a95b23888489cea19acfc086084521f4912ae78527420c639e39054a69916e50.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/aece08eb503a6a35fb1fa79af59289407a29784f963619010b4681ffcfaf0d90.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/af6e8de3dc3f5f53abf3be4ea2d1be262f0f559470ca41fc6fcba1fcc09f8ef5.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/b9a5b9714c29a64191f6aa326daca8e2d44b013ae8b18d444ac62f0c9386e718.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/cb51dcd553a5e9e248cc1cddb567ec15255540a71c512d7dc1c26388680bd295.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/dfd915e902bb2dec906a97336f8f1c44d8c58162be1d0223bd110a93e6322358.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/e363f94b74a04b6cf3a1559de06da8fa484a1e7333be578551bc7341bb59dde1.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/e3aa44370b8e3916cdb2ff1b4e2cd27fa74fd1286aa0af05098e73d2051f203c.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_corpus_ingestion_1b/intake/records/ffaab55790816e301f9fa1c96520fd0e7f90065bac7b4461493eedc58a1c1da6.json` | Normalized raw knowledge-corpus record, not a report or plan. |
| `data/unix_full_validation_freeze_1a/adversarial_report.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/adversarial_report.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/benchmark.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/benchmark.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/capability_boundary_report.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/capability_boundary_report.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/determinism_report.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/determinism_report.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/limitations.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/limitations.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/reproducibility_commands.txt` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/reproducibility_commands.txt`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/architecture_summary.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/architecture_summary.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/authority_boundary.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/authority_boundary.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/demo.txt` | Generated presentation output, not a report or plan. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/index.html` | Generated presentation output, not a report or plan. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/limitations.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/limitations.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/reproduction.txt` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/reproducibility_commands.txt`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/sponsor_demo_checklist.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/sponsor_demo_checklist.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/visible_demo_manifest.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/visible_demo_manifest.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo/visible_demo_verification.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/visible_demo_verification.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a/sponsor_demo_checklist.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/sponsor_demo_checklist.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a_r1/sponsor_demo/demo.txt` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a/sponsor_demo/demo.txt`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a_r1/sponsor_demo/index.html` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a/sponsor_demo/index.html`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a_r1/sponsor_demo/limitations.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/limitations.json`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a_r1/sponsor_demo/reproduction.txt` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/reproducibility_commands.txt`; omitted by content deduplication. |
| `data/unix_full_validation_freeze_1a_r1/sponsor_demo_checklist.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/sponsor_demo_checklist.json`; omitted by content deduplication. |
| `data/unix_retrieval_adapter_1a/index/postings.json` | Raw lexical retrieval index data, not a report or plan. |
| `data/visible_unix_prototype_1a/demo.txt` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a/sponsor_demo/demo.txt`; omitted by content deduplication. |
| `data/visible_unix_prototype_1a/demo_manifest.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/visible_demo_manifest.json`; omitted by content deduplication. |
| `data/visible_unix_prototype_1a/index.html` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a/sponsor_demo/index.html`; omitted by content deduplication. |
| `data/visible_unix_prototype_1a/queries/execution_blocked.html` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/queries/no_route.html` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/queries/path_traversal.html` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/queries/process_signals.html` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/queries/review_needed.html` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/queries/unix_file_permissions.html` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/review_models/execution_blocked.json` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/review_models/no_route.json` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/review_models/path_traversal.json` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/review_models/process_signals.json` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/review_models/review_needed.json` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/review_models/unix_file_permissions.json` | Generated visible-prototype output or review model, not a report or plan. |
| `data/visible_unix_prototype_1a/verification.json` | Exact SHA-256 duplicate of `data/unix_full_validation_freeze_1a_r1/sponsor_demo/visible_demo_verification.json`; omitted by content deduplication. |
| `docs/BASH_SAFETY_PHASE1_SPEC.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/BENCHMARK_LIMITATIONS.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/FRAMEWORK_SURFACE_MAP.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/GLOSSARY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/README.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/REVIEWER_QUICKSTART.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/ROADMAP_4_MONTHS.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/THREAT_MODEL.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/api/API_BOUNDARY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/architecture/AOIA_MEMORY_MODEL.md` | Foundational pre-window Memory Layer document; its only in-window change was cosmetic AIOA branding, not a substantive Memory Layer update. |
| `docs/architecture/FORBIDDEN_MEMORY_FLOWS.md` | Foundational pre-window Memory Layer document; its only in-window change was cosmetic AIOA branding, not a substantive Memory Layer update. |
| `docs/architecture/MEMORY_HATS_ARCHITECTURE.md` | Foundational pre-window Memory Layer document; its only in-window change was cosmetic AIOA branding, not a substantive Memory Layer update. |
| `docs/architecture/MEMORY_LAYER_ACCESS_MATRIX.md` | Foundational pre-window Memory Layer document; its only in-window change was cosmetic AIOA branding, not a substantive Memory Layer update. |
| `docs/dev/IOA_LAB_CLONE_QUICKSTART.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/dev/TERMINAL_PROVIDER_SWITCHER_QUICKSTART.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/governance/EXTERNAL_MODEL_OUTPUT_POLICY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/governance/IMPLEMENTED_CAPABILITIES.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/governance/TEST_ENVIRONMENT_POLICY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/nms/GLOSSARY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/nms/NLNET_UPDATE_SUMMARY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/nms/README.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/nms/ROADMAP_4_MONTHS.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/reviewer/ONE_CONCRETE_EXAMPLE.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/reviewer/PROJECT_OVERVIEW_FOR_REVIEWERS.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/reviewer/QUICK_START_FOR_GRANT_REVIEWERS.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/security/SECRET_MANAGEMENT.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/stress_tests/AOIA_NMS_STRESS_TEST_PROTOCOL.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/stress_tests/FAILURE_MODES.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/stress_tests/LSC_CASE_STUDY_PROTOCOL.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/stress_tests/MODEL_AUDIT_MATRIX.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/stress_tests/README.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/ui/UI_POLICY.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/ui/UI_STATE_CONTRACT.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `docs/ui/UI_THREAT_MODEL.md` | General safety, UI, NMS, reviewer or governance document; the in-window edit was unrelated or branding-only and Memory terminology is incidental (Category C). |
| `pyproject.toml` | Application configuration, packaging or CI material; not documentation provenance. |
| `reports/AUTH_IDENTITY_PATH_REDACTION_FALSE_POSITIVE_REPAIR_1A.md` | Report primarily about authentication, provider runtime or Orchestra/Critical workflow, outside the Memory Patch scope. |
| `reports/EPISTEMIC_ORCHESTRA_CONTRACTS_CPT_STAGE_BINDING_1A.md` | Critical/Orchestra contract report; Knowledge Context is only a deferred or incidental reference, so importing it would broaden this repository. |
| `reports/ORCHESTRA_USER_PROVIDER_CONNECTIONS_LIVE_ROLE_SELECTION_1A.md` | Report primarily about authentication, provider runtime or Orchestra/Critical workflow, outside the Memory Patch scope. |
| `runtime/requirements.txt` | Application configuration, packaging or CI material; not documentation provenance. |
| `web/index.html` | Application UI implementation, not a documentation report. |

## Exact duplicate groups

For each group, the first path is the comparison representative. Later paths have identical SHA-256 content. Some representatives were also excluded because the whole group was generated presentation output.

- `data/unix_full_validation_freeze_1a_r1/adversarial_report.json` = `data/unix_full_validation_freeze_1a/adversarial_report.json`
- `data/unix_full_validation_freeze_1a_r1/benchmark.json` = `data/unix_full_validation_freeze_1a/benchmark.json`
- `data/unix_full_validation_freeze_1a_r1/capability_boundary_report.json` = `data/unix_full_validation_freeze_1a/capability_boundary_report.json`
- `data/unix_full_validation_freeze_1a_r1/determinism_report.json` = `data/unix_full_validation_freeze_1a/determinism_report.json`
- `data/unix_full_validation_freeze_1a_r1/limitations.json` = `data/unix_full_validation_freeze_1a/limitations.json` = `data/unix_full_validation_freeze_1a/sponsor_demo/limitations.json` = `data/unix_full_validation_freeze_1a_r1/sponsor_demo/limitations.json`
- `data/unix_full_validation_freeze_1a_r1/reproducibility_commands.txt` = `data/unix_full_validation_freeze_1a/reproducibility_commands.txt` = `data/unix_full_validation_freeze_1a/sponsor_demo/reproduction.txt` = `data/unix_full_validation_freeze_1a_r1/sponsor_demo/reproduction.txt`
- `data/unix_full_validation_freeze_1a_r1/sponsor_demo/architecture_summary.json` = `data/unix_full_validation_freeze_1a/sponsor_demo/architecture_summary.json`
- `data/unix_full_validation_freeze_1a_r1/sponsor_demo/authority_boundary.json` = `data/unix_full_validation_freeze_1a/sponsor_demo/authority_boundary.json`
- `data/unix_full_validation_freeze_1a/sponsor_demo/demo.txt` = `data/unix_full_validation_freeze_1a_r1/sponsor_demo/demo.txt` = `data/visible_unix_prototype_1a/demo.txt`
- `data/unix_full_validation_freeze_1a/sponsor_demo/index.html` = `data/unix_full_validation_freeze_1a_r1/sponsor_demo/index.html` = `data/visible_unix_prototype_1a/index.html`
- `data/unix_full_validation_freeze_1a_r1/sponsor_demo/sponsor_demo_checklist.json` = `data/unix_full_validation_freeze_1a/sponsor_demo/sponsor_demo_checklist.json` = `data/unix_full_validation_freeze_1a/sponsor_demo_checklist.json` = `data/unix_full_validation_freeze_1a_r1/sponsor_demo_checklist.json`
- `data/unix_full_validation_freeze_1a_r1/sponsor_demo/visible_demo_manifest.json` = `data/unix_full_validation_freeze_1a/sponsor_demo/visible_demo_manifest.json` = `data/visible_unix_prototype_1a/demo_manifest.json`
- `data/unix_full_validation_freeze_1a_r1/sponsor_demo/visible_demo_verification.json` = `data/unix_full_validation_freeze_1a/sponsor_demo/visible_demo_verification.json` = `data/visible_unix_prototype_1a/verification.json`
