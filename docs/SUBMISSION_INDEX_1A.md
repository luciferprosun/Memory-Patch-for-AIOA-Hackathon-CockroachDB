# Memory Patch Submission Index 1A

## Judge quick path

1. [Top-level project overview](../README.md)
2. [Judge-facing Golden Path](demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md)
3. [5–8 minute video script](demo/YOUTUBE_DEMO_SCRIPT_1A.md)
4. [Operator runbook](operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md)
5. [Copy-ready submission package](submission/HACKATHON_SUBMISSION_PACKAGE_1A.md)
6. [System architecture](architecture/MEMORY_PATCH_SYSTEM_OVERVIEW_1A.md)
7. [Canonical roadmap](roadmap/PRODUCTION_ROADMAP.md)
8. [Devpost component integration](submission/DEVPOST_COMPONENT_INTEGRATION_1A.md)

## Product architecture

- [Kernel contract baseline](architecture/KNOWLEDGE_KERNEL_CONTRACT_BASELINE_1A.md)
- [German Law full E2E architecture](architecture/GERMAN_LAW_FULL_END_TO_END_1A.md)
- [Personal Memory HATs](architecture/PERSONAL_MEMORY_HATS_1A.md)
- [Personal Memory retrieval](architecture/ACTIVE_PATCH_RETRIEVAL_CROSS_MODEL_REUSE_1A.md)
- [Audit architecture](architecture/AUDIT_LEDGER_HASH_CHAIN_AUDIT_EXPORT_1A.md)
- [Owner UI architecture](architecture/PERSONAL_MEMORY_UI_1A.md)
- [Optional Critic architecture](architecture/AOIA_CRITIC_PROMPT_LOOP_PRODUCTION_BRIDGE_1A.md)
- [4 GB runtime architecture](architecture/RESOURCE_DEPLOYMENT_OPTIMIZATION_4GB_1A.md)
- [RC backup/restore architecture](architecture/RC_FREEZE_BACKUP_RESTORE_1A.md)

## Strongest validation evidence

- [Step 38 German Law live E2E](evidence/e2e/step38-german-law-full-e2e-validation.json)
- [Step 38 hash-only demo trace](evidence/e2e/step38-german-law-demo-trace.json)
- [Step 39 candidate-only Critic](evidence/critic/step39-critic-prompt-loop-bridge-validation.json)
- [Step 40 constrained 4 GB profile](evidence/performance/step40-4gb-resource-validation.json)
- [Step 41 full security/regression](evidence/security/step41-full-security-regression-validation.json)
- [Step 42 RC manifest](evidence/release/step42-rc-manifest-1a.json)
- [Step 42 recovery validation](evidence/release/step42-rc-backup-restore-validation.json)
- [Step 43 demo/submission validation](evidence/demo/step43-documentation-demo-submission-validation.json)
- [Read-only ccloud control-plane gate](evidence/cockroachdb-cloud/ccloud-control-plane-gate-1a.json)

## Security and authority

- [Credential/capability matrix](security/STEP36_CREDENTIAL_CAPABILITY_MATRIX_1A.md)
- [Step 41 threat matrix](security/STEP41_FULL_SECURITY_THREAT_MATRIX_1A.md)
- [Step 41 threat model](security/Memory-Patch-for-AIOA-Hackathon-CockroachDB-threat-model.md)
- [Memory trust and precedence](architecture/MEMORY_TRUST_AND_PRECEDENCE_1A.md)
- [Step 41 closure](audits/STEP_41_FULL_SECURITY_REGRESSION_CLOSURE_1A.md)
- [Step 42 closure](audits/STEP_42_RC_BACKUP_RESTORE_CLOSURE_1A.md)
- [Step 43 final closure](audits/STEP_43_DOCUMENTATION_DEMO_SUBMISSION_CLOSURE_1A.md)

## Reproducible operations

- [Step 38 German Law runbook](operations/STEP_38_GERMAN_LAW_E2E_VALIDATION_1A.md)
- [Step 39 Critic bridge runbook](operations/STEP_39_CRITIC_BRIDGE_VALIDATION_1A.md)
- [Step 40 4 GB runbook](operations/STEP_40_4GB_RUNTIME_VALIDATION_1A.md)
- [Step 41 security runbook](operations/STEP_41_FULL_SECURITY_REGRESSION_VALIDATION_1A.md)
- [Step 42 backup/restore runbook](operations/STEP_42_RC_BACKUP_RESTORE_1A.md)
- [Step 43 demo runbook](operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md)
- [ccloud release gate runbook](operations/CCLOUD_CONTROL_PLANE_RELEASE_GATE_1A.md)

All linked evidence is bounded and sanitized. Replay is explicitly distinct
from fresh live provider execution. This index does not grant any model,
Critic, UI, reviewer, or Personal Memory object additional authority.
