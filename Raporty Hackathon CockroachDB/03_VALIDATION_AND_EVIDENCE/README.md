# Validation and Evidence

Fifty-three canonical evidence files are linked and individually hashed in the
[artifact manifest](../manifest/artifact-manifest.json). The strongest jury
paths are below.

## CockroachDB foundations

- [Capability matrix](../../docs/evidence/cockroachdb-v26-2/capability-matrix.json)
- [Schema replay](../../docs/evidence/cockroachdb-v26-2/step4-schema-validation.json)
- [RLS/FORCE RLS](../../docs/evidence/cockroachdb-v26-2/step5-rls-validation.json)
- [Retry-safe persistence](../../docs/evidence/cockroachdb-v26-2/step6-persistence-validation.json)
- [Source registry and publication state](../../docs/evidence/cockroachdb-v26-2/step9-source-registry-validation.json)

## Knowledge and correction path

- [Corpus inventory](../../docs/evidence/corpus/step14-german-law-corpus-inventory-summary.json)
- [Publication proof](../../docs/evidence/corpus/step16-german-law-hat-publication-summary.json)
- [Hybrid Evidence Bundle](../../docs/evidence/retrieval/step20-hybrid-evidence-bundle-validation.json)
- [Correction Packet](../../docs/evidence/modeling/step24-correction-packet-validation.json)
- [Verified Answer](../../docs/evidence/modeling/step26-verified-answer-fail-closed-validation.json)
- [German Law full E2E](../../docs/evidence/e2e/step38-german-law-full-e2e-validation.json)

## Personal Memory, security, and release

- [Owner approval/activation](../../docs/evidence/personal-memory/step30-user-approval-commit-activation-validation.json)
- [Cross-model reuse](../../docs/evidence/personal-memory/step31-active-patch-retrieval-validation.json)
- [Audit chain](../../docs/evidence/audit/step33-audit-ledger-validation.json)
- [Credential separation](../../docs/evidence/security/step36-credential-authority-validation.json)
- [Failure recovery](../../docs/evidence/reliability/step37-failure-recovery-validation.json)
- [Full security regression](../../docs/evidence/security/step41-full-security-regression-validation.json)
- [RC backup/restore](../../docs/evidence/release/step42-rc-backup-restore-validation.json)
- [Final demo/submission validation](../../docs/evidence/demo/step43-documentation-demo-submission-validation.json)

## Preserved failed/intermediate evidence

- [Step10 failed attempt](../../docs/evidence/ingestion/step10-ingestion-saga-validation-failure.json)
- [Step11 first failure](../../docs/evidence/parsing/step11-parsing-pipeline-validation-failure.json)
- [Step11 recovery failures](../../docs/evidence/parsing/)

These failure records are classified `FAILED_VALIDATION` or `INTERMEDIATE` in
the machine-readable catalog; the final successful evidence remains separate.
