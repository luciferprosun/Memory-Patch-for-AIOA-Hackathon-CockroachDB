# EXECUTION MODE — GPT-5.3-CODEX-SPARK

This is a complete autonomous production task. Do not reduce it to a small patch.

Execute the entire task from repository guard through implementation, tests, controlled validation, one commit, push, and the complete closure report.

Do not stop after planning, partial implementation, targeted tests, or preparing a commit.

Allowed final verdicts only:

```text
COMPLETE AND PUSHED
BLOCKED SAFELY — NO COMMIT
FAILED VALIDATION — NOT COMMITTED
```

Never fabricate source authority, legal status, test results, Git state, or cleanup results.

Never choose or hardcode the final hackathon question.

Never hardcode the Nachweisgesetz 2022/2025 scenario.

Never start Step 14.

---

# MEMORY PATCH STEP 13 — GERMAN LAW HAT PACKAGE AND SOURCE AUTHORITY POLICY 1A

Execute exactly one production task:

**Step 13 — German Law HAT Package and Source Authority Policy 1A**

## 1. Authorized baseline

Repository:

```text
/media/l/LSC_DATA/AIOA_WORKSPACE/hackathons/AIOIA_HACKATHONS/Memory-Patch-for-AIOA-Hackathon-CockroachDB
```

Remote:

```text
https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB.git
```

Branch:

```text
main
```

Required starting HEAD and `origin/main`:

```text
fb3e9bbeaa4dfc146bcc75d00edc4780be94edba
```

Required starting subject:

```text
feat(hats): add trusted registry and runtime boundary 1a
```

Expected:

```text
ahead/behind: 0 0
worktree: CLEAN
Step 12: COMPLETE AND PUSHED
Step 13: NOT STARTED
Step 14: NOT STARTED
```

Run the full repository guard before reading implementation files:

```bash
cd /media/l/LSC_DATA/AIOA_WORKSPACE/hackathons/AIOIA_HACKATHONS/Memory-Patch-for-AIOA-Hackathon-CockroachDB

pwd
git rev-parse --show-toplevel
git remote -v
git branch --show-current
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
git status --short
git status --branch
git log -1 --format='sha=%H%nsubject=%s%ncommitted=%cI'
git show --stat --oneline HEAD
git worktree list --porcelain

for marker in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
  path="$(git rev-parse --git-path "$marker")"
  if [ -e "$path" ]; then
    printf '%s: PRESENT at %s\n' "$marker" "$path"
  else
    printf '%s: absent\n' "$marker"
  fi
done

test -f docs/audits/STEP_12_HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_CLOSURE_1A.md
grep -n -A35 -B4 'Step 12' docs/roadmap/PRODUCTION_ROADMAP.md
```

Continue only if every baseline condition matches exactly.

If anything differs, do not reset, clean, stash, restore, rebase, or switch branches. Return a precise blocker and stop.

---

## 2. Scope

Step 13 must implement:

- one production German Law HAT manifest;
- one trusted system-installed German Law HAT implementation;
- German-law request and source-metadata contracts;
- domain, language, jurisdiction, and knowledge-as-of scope policy;
- German-law source-authority policy;
- German-law temporal-policy contract;
- German-law metadata parser-adapter boundary;
- Step 12 registry/runtime integration;
- contract tests;
- official-source research evidence;
- controlled validation;
- documentation, closure, commit, and push.

Step 13 must not implement:

- real corpus inventory;
- corpus deduplication;
- corpus registration;
- corpus ingestion;
- real document temporal normalization;
- corpus publication;
- retrieval;
- embeddings;
- vector search;
- model calls;
- PDF parsing;
- OCR;
- remote loading;
- dynamic imports;
- package installation;
- UI;
- the final demonstration question;
- Step 14.

Step 14 owns the real corpus inventory and registration.

Step 15 owns real temporal and jurisdictional normalization.

Step 16 owns corpus publication and verification.

---

## 3. Read existing contracts

Inspect before design:

- `AGENTS.md`;
- `docs/roadmap/PRODUCTION_ROADMAP.md`;
- Step 1 HAT SDK and Kernel contracts;
- Step 2 authority closure;
- Step 4 HAT scopes and source lineage;
- Step 5 security;
- Step 6 idempotency;
- Step 9 source registry and publication;
- Step 11 parsing;
- Step 12 HAT registry/runtime architecture, code, migration, tests, evidence, and closure;
- `src/aioa_memory_kernel/contracts/hats.py`;
- `src/aioa_memory_kernel/contracts/scope.py`;
- `schemas/hat-manifest.schema.json`;
- current Step 12 registry package;
- existing synthetic HAT fixtures;
- relevant historical German-law design notes under `docs/history`.

Search before creating abstractions:

```bash
grep -RIn --exclude-dir=.git \
  -e "German Law" \
  -e "german-law" \
  -e "GermanLaw" \
  -e "jurisdiction" \
  -e "source_authority" \
  -e "temporal" \
  -e "Bundesgesetzblatt" \
  -e "Gesetze im Internet" \
  -e "EUR-Lex" \
  -e "DIP" \
  -e "HatSdk" \
  -e "hat-capabilities-1a" \
  src config schemas tests scripts sql docs
```

Reuse the Step 12 manifest decoder, compatibility gate, capability vocabulary, trusted installed catalog, runtime handle, registry persistence, canonical serialization, and Step 9 authority vocabulary.

Do not create a second HAT registry or source registry.

---

## 4. Official-source research gate

This is a legal source-authority policy. Verify it read-only against current official primary sources.

At minimum verify:

1. **Bundesgesetzblatt / Verkündungsplattform des Bundes**
   - official promulgation;
   - electronic official publication from 1 January 2023;
   - authentic promulgated text versus later consolidation.

2. **Gesetze im Internet**
   - consolidated federal statutes and regulations;
   - explicit non-official status of the consolidated online text;
   - current-status and delayed-consolidation caveats.

3. **Bundestag/Bundesrat DIP**
   - bills, parliamentary papers, protocols, and legislative procedure;
   - legislative materials are not enacted law.

4. **Official federal courts**
   - Bundesverfassungsgericht;
   - Bundesgerichtshof;
   - Bundesarbeitsgericht;
   - Bundesverwaltungsgericht;
   - Bundesfinanzhof;
   - Bundessozialgericht.

   Distinguish official decision text, the case-specific holding, generalized interpretation, and legal binding effect. Do not implement substantive precedent analysis.

5. **EUR-Lex**
   - authentic electronic Official Journal;
   - CELEX identity;
   - authentic official act versus consolidated text and summary;
   - EU applicability remains distinct from German national promulgation.

6. **Secondary categories**
   - official administrative guidance;
   - parliamentary research;
   - reputable legal commentary;
   - academic writing;
   - private databases;
   - user-supplied documents;
   - derived summaries.

Requirements:

- official sources only for policy facts;
- no paid-source access;
- no corpus scraping;
- no large quotations;
- no reliance on search snippets alone;
- preserve sanitized source URLs, titles, retrieval times, and policy facts in evidence;
- stop rather than invent an unverified source property.

---

## 5. German Law HAT manifest

Add one production manifest using the existing Step 12 schema and registry.

Choose one stable repository-consistent HAT ID, conceptually:

```text
german-law
```

Use exact versions. Never use `latest`.

Manifest requirements:

### Domain IDs

Represent broad legal domains without statute-specific hardcoding, conceptually:

```text
law.de
law.de.federal
law.de.state
law.eu
```

### Languages

Mandatory source/request language:

```text
de
```

Add `en` or `pl` only if deterministic request normalization is genuinely implemented without translation or a model.

### Capabilities

Declare only implemented capabilities:

```text
REQUEST_NORMALIZATION
SCOPE_DERIVATION
EVIDENCE_CONSTRAINTS
SOURCE_AUTHORITY_RANKING
```

Do not declare claim extraction, conflict detection, correction requirements, or correction proposals unless truly implemented and fully tested without crossing later roadmap steps.

### Security

Exactly:

```text
external_action_authority = NONE
canonical_write_authority = NONE
patch_approval_authority = NONE
patch_commit_authority = NONE
executable_user_code = false
private_memory_access = false
```

Every nested contract must have an immutable version and contain no paths, import strings, entry points, commands, credentials, or executable content.

---

## 6. German-law scopes

Define typed domain-specific scope declarations without modifying Kernel Core enums.

Mandatory dimensions:

```text
legal_jurisdiction
knowledge_as_of
source_language
legal_source_class
```

Evaluate optional dimensions only when justified:

```text
federal_state
court_identity
court_level
proceeding_identity
document_identifier
document_version
legal_domain
eu_applicability
```

Rules:

- distinguish German federal, German state, and EU jurisdiction;
- never infer Germany solely from German language;
- never silently default missing jurisdiction;
- require or explicitly mark ambiguity for missing `knowledge_as_of`;
- do not replace missing temporal scope with current time;
- do not collapse state law into federal law;
- do not collapse EU law into German law;
- language is descriptive, not authority;
- source class is not semantic truth;
- unsupported scope fails closed or remains explicitly ambiguous.

---

## 7. Source-authority policy

Reuse Step 9 levels where possible:

```text
OFFICIAL_PRIMARY
AUTHORITATIVE_SECONDARY
INFORMATIONAL_SECONDARY
USER_SUPPLIED
DERIVED
UNKNOWN
```

Add a German-law HAT-specific source class, conceptually:

```text
DE_FEDERAL_AUTHENTIC_PROMULGATION
DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW
DE_FEDERAL_OFFICIAL_COURT_DECISION
DE_STATE_AUTHENTIC_PROMULGATION
DE_STATE_OFFICIAL_CONSOLIDATED_LAW
DE_STATE_OFFICIAL_COURT_DECISION
EU_AUTHENTIC_OFFICIAL_JOURNAL
EU_OFFICIAL_CONSOLIDATED_ACT
OFFICIAL_LEGISLATIVE_MATERIAL
OFFICIAL_ADMINISTRATIVE_GUIDANCE
OFFICIAL_RESEARCH_OR_EXPLANATORY_MATERIAL
REPUTABLE_LEGAL_SECONDARY
PRIVATE_LEGAL_DATABASE
USER_SUPPLIED_LEGAL_DOCUMENT
DERIVED_SUMMARY
UNKNOWN_LEGAL_SOURCE
```

Use exact repository naming conventions.

Do not reduce all legal sources to one numeric score.

Assess on typed dimensions:

- source class;
- authenticity;
- official publisher;
- jurisdiction match;
- document/version identity;
- temporal applicability;
- court identity and level;
- publication verification;
- consolidation status;
- language;
- retrieval and verification time.

Required decisions:

1. Authentic promulgation outranks a convenience consolidation for proving exact enacted text at publication.
2. Official consolidation can be preferred for current wording only while preserving its non-authentic status and amendment lineage.
3. Official court decisions are primary evidence of those decisions, not automatic universal authority for every proposition.
4. Legislative materials are evidence of legislative history, not enacted law.
5. Administrative guidance is agency interpretation, not a substitute for statute, regulation, or controlling judgment.
6. EU Official Journal acts and German national implementation remain distinct.
7. EUR-Lex consolidated texts and summaries are not authentic Official Journal acts.
8. Secondary commentary can aid interpretation but cannot silently replace conflicting official primary evidence.
9. User-supplied and derived content cannot become official through repetition.
10. Same-class conflicts remain explicit.
11. Missing source identity, authenticity, jurisdiction, or temporal status fails closed or requires review.
12. Source-policy output cannot publish a source.

Return a typed assessment containing:

- Step 9 authority level;
- German source class;
- authenticity status;
- verification status;
- policy version;
- reason codes;
- unresolved limitations;
- deterministic assessment digest.

---

## 8. Temporal-policy contract

Define a versioned policy that distinguishes:

```text
published_at
promulgated_at
adopted_at
effective_from
effective_to
applicable_from
applicable_to
decision_date
retrieved_at
ingested_at
verified_at
superseded_at
```

Rules:

- publication is not entry into force;
- adoption is not applicability;
- retrieval is not legal validity;
- ingestion is not verification;
- consolidation date is not authentic promulgation;
- future amendments cannot appear in earlier `knowledge_as_of` results;
- missing temporal metadata remains unknown;
- invalid intervals fail closed;
- open intervals are explicit;
- later verification does not rewrite original publication metadata;
- supersession does not imply deletion;
- incompatible intervals require review.

Define source-class-specific required and optional temporal fields.

Do not normalize the real corpus. That is Step 15.

---

## 9. Request and source metadata models

Implement immutable typed models conceptually equivalent to:

```text
GermanLawRequest
GermanLawSourceMetadata
GermanLawSourceAuthorityAssessment
GermanLawTemporalAssessment
```

### GermanLawRequest

Bind at minimum:

- request ID;
- query text or reference;
- request language;
- legal jurisdiction;
- `knowledge_as_of`;
- optional federal state;
- optional legal domain;
- optional official identifier hint;
- optional court/proceeding hint;
- request digest.

Reject empty data, unsupported language/jurisdiction, malformed time, executable fields, credentials, and hidden authority declarations.

Preserve user wording.

Do not answer, translate, execute, or grant authority.

### GermanLawSourceMetadata

Bind at minimum:

- source ID;
- source registry candidate/reference;
- source class;
- official publisher;
- canonical official identifier;
- jurisdiction;
- language;
- authenticity status;
- consolidation status;
- optional court identity/level;
- declared temporal fields;
- verification status;
- retrieval reference;
- metadata digest.

Verification statuses should distinguish:

```text
UNVERIFIED
OFFICIAL_REFERENCE_VERIFIED
AUTHENTICITY_VERIFIED
CONFLICTING
INSUFFICIENT
```

Parsing metadata is not verification.

Do not store full legal document bodies in these models.

---

## 10. Parser-adapter boundary

Step 11 owns generic decoding, normalization, sections, chunks, and security findings.

Step 13 may add a fixed German-law metadata adapter registry operating only on typed Step 11 parse artifacts or bounded synthetic metadata.

Conceptual adapters:

```text
FederalGazetteMetadataAdapter
ConsolidatedFederalLawMetadataAdapter
OfficialCourtDecisionMetadataAdapter
EuLegalActMetadataAdapter
LegislativeMaterialMetadataAdapter
```

Adapters must:

- never fetch URLs;
- never run OCR;
- never parse PDF directly;
- never execute HTML;
- never call a model;
- never access the real corpus;
- never publish;
- never assign final authority without the source policy;
- return typed metadata candidates plus validation findings.

Unsupported media/source combinations fail closed.

An adapter is not a downloader, legal interpreter, or authority.

---

## 11. Trusted system-installed implementation

Implement one explicit system-installed German Law HAT satisfying `HatSdk`.

Register it only through the Step 12 `TrustedInstalledHatCatalog`.

No dynamic loading.

Implement only declared capabilities:

### `normalize_request`

Normalize bounded structured request data.

Do not answer the legal question, call a model, infer missing dates, infer Germany from language, or select the final demo question.

### `derive_scope_requirements`

Return typed `ScopeDimension` values.

Missing required temporal or jurisdictional scope must produce explicit ambiguity or rejection.

### `build_retrieval_constraints`

Return declarative constraints only.

No database query, network query, or retrieval execution.

### `rank_source_authority`

Apply the deterministic policy to typed source metadata.

Preserve typed distinctions and conflicts.

Do not return a legal conclusion.

Methods not declared as capabilities must be rejected by Step 12 capability gating.

---

## 12. Persistence decision

Step 12 already provides HAT registry persistence.

Default expectation:

```text
NO NEW DATABASE MIGRATION
```

Use the existing registry and runtime binding during controlled validation.

Add a migration only after a precise schema-gap analysis proves it is essential.

Do not add:

- corpus tables;
- real source rows;
- temporal-normalization tables;
- retrieval indexes;
- German-law columns to Kernel Core.

No migration is better than an unnecessary migration.

---

## 13. Fixtures

Use synthetic or short official metadata fixtures only.

Required categories:

- authentic federal promulgation;
- official consolidated federal law;
- official court decision;
- official legislative material;
- authentic EU Official Journal act;
- official EU consolidated act;
- administrative guidance;
- reputable secondary commentary;
- user-supplied metadata;
- derived summary;
- unknown source;
- conflicting metadata.

Do not use:

- Nachweisgesetz;
- the 2022/2025 scenario;
- the final demo question;
- large legal text;
- copyrighted commentary.

---

## 14. Minimum tests

Add deterministic tests covering at least:

### Manifest/package

- manifest validation;
- exact ID/version;
- Kernel compatibility;
- domain IDs;
- languages;
- capabilities;
- zero authority;
- no dynamic fields;
- no final-question hardcoding;
- canonical digest.

### Request/scopes

- federal, state, and EU requests;
- unsupported jurisdiction;
- missing jurisdiction;
- explicit/missing `knowledge_as_of`;
- language does not infer jurisdiction;
- state/federal mismatch;
- deterministic request digest;
- wording preserved.

### Source metadata/authority

- every required source category;
- malformed identifier;
- spoofed publisher;
- missing authenticity;
- conflict preservation;
- authentic versus consolidated;
- court decision distinction;
- legislative-history distinction;
- guidance distinction;
- EU authentic versus consolidated;
- secondary conflict;
- user verification required;
- derived remains derived;
- unknown remains unknown;
- jurisdiction and temporal mismatch;
- deterministic policy output.

### Temporal policy

- publication versus effective date;
- adoption versus applicability;
- retrieval versus validity;
- future amendment;
- expired interval;
- open interval;
- invalid interval;
- missing fields;
- supersession;
- `knowledge_as_of`.

### Adapters

- each fixed adapter;
- unsupported adapter;
- no network;
- no PDF/OCR/HTML execution/model;
- deterministic output.

### Step 12 integration

- `assert_system_installed_hat`;
- catalog registration;
- manifest match;
- enable/resolve;
- every declared capability;
- undeclared capability rejection;
- exact replay;
- conflicting manifest rejection;
- disable behavior;
- event chain;
- Personal Memory execution rejection.

### Boundaries/regressions

- Step 9 regressions;
- Step 11 regressions;
- Step 12 regressions;
- no corpus inventory/registration/ingestion;
- no publication;
- no retrieval;
- no embeddings;
- no model;
- no Nachweisgesetz hardcoding;
- no final question;
- Step 14 not started;
- no import-time I/O;
- no secrets/local paths.

The full suite must exceed the Step 12 baseline of 891 tests and pass completely.

Do not weaken existing tests.

---

## 15. Controlled validation

Allowed:

- read-only official-source verification;
- local manifest and fixtures;
- one disposable CockroachDB v26.2.4 runtime;
- existing migrations;
- Step 12 registry;
- sanitized repository evidence.

Prohibited:

- AWS writes;
- S3 writes;
- external-volume writes;
- real corpus reads/writes;
- paid sources;
- model calls;
- persistent database;
- Step 14 work.

Display a plan proving:

```text
AWS writes: 0
S3 writes: 0
external-volume writes: 0
corpus reads: 0
corpus writes: 0
paid-source access: 0
model calls: 0
persistent database: 0
Step 14 work: 0
```

Validation flow:

1. start disposable CockroachDB v26.2.4;
2. apply and replay all existing migrations;
3. register the German Law HAT manifest;
4. validate compatibility and capabilities;
5. create trusted operator receipt and runtime binding;
6. enable and resolve the HAT;
7. normalize synthetic federal, state, and EU requests;
8. derive scopes;
9. build retrieval constraints;
10. assess mixed source metadata;
11. prove authentic/consolidated distinctions;
12. prove court/legislative-material distinctions;
13. prove temporal and jurisdictional failure behavior;
14. exact replay;
15. conflicting manifest rejection;
16. disable and reject resolution;
17. verify event chain;
18. gracefully drain and remove runtime.

Success requires:

```text
force kill: NO
persistent database: NO
real corpus touched: NO
final question selected: NO
Step 14 started: NO
```

---

## 16. Evidence and documentation

Add sanitized evidence, conceptually:

```text
docs/evidence/hats/step13-german-law-hat-policy-validation.json
```

Include:

- starting SHA;
- manifest and policy versions/digests;
- official-source references and retrieval times;
- source classes and authority mappings;
- temporal-policy digest;
- adapter-policy digest;
- scopes/languages/capabilities;
- registry, receipt, and runtime-binding digests;
- validation decisions;
- no final question;
- no Nachweisgesetz hardcoding;
- no corpus access;
- no AWS/S3/external writes;
- no model;
- cleanup;
- evidence digest.

Add:

- architecture document;
- ADR;
- source-authority policy;
- temporal-policy contract;
- parser-adapter contract;
- official-source research note;
- controlled-validation runbook;
- closure report.

Update `docs/README.md`, `AGENTS.md`, and roadmap only after successful closure.

---

## 17. Validation and security

Run:

```bash
python -m compileall src tests scripts
```

Run targeted Step 13 tests, Step 9/11/12 regressions, the full suite, static/security checks, secret scan, and:

```bash
git diff --check
git status --short
git diff --stat
git diff
```

Inspect the complete diff.

Verify no:

- credentials;
- local absolute paths in evidence;
- dynamic imports;
- package installation;
- subprocess/shell;
- `eval`, `exec`, `compile`;
- remote runtime loading;
- paid-source access;
- corpus copying;
- PDF/OCR implementation;
- model call;
- legal answer hardcoding;
- Nachweisgesetz scenario;
- final-question selection;
- HAT self-enablement;
- publication bypass;
- Personal Memory access;
- Step 14 contamination.

---

## 18. Commit and push

Commit only after every requirement passes.

Subject:

```text
feat(hats): add german law package and source authority policy 1a
```

Then:

```bash
git diff --check
git status --short
git diff --stat
git add <only intended Step 13 files>
git commit -m "feat(hats): add german law package and source authority policy 1a"
git push origin main
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
git status --short
git log -1 --oneline
git show --stat --oneline HEAD
```

Required final state:

```text
HEAD == origin/main
ahead/behind == 0 0
worktree clean
Step 12 remains COMPLETE
Step 13 is COMPLETE AND PUSHED
Step 14 remains NOT STARTED
```

Do not force-push.

Do not start Step 14.

---

## 19. Required final report

Return:

# Step 13 — German Law HAT Package and Source Authority Policy 1A

## 1. Executive Verdict
## 2. Repository Identity
## 3. Official-Source Research
## 4. German Law HAT Manifest
## 5. Scope and Request Policy
## 6. Source Authority Policy
## 7. Temporal Policy
## 8. Parser Adapters
## 9. Step 12 Runtime Integration
## 10. Controlled Validation
## 11. Tests
## 12. Files Changed
## 13. Safety and Boundaries
## 14. Commit and Push
## 15. Roadmap State
## 16. Exact Next Authorized Step

The final section must state exactly one:

```text
Step 14 — German Law Corpus Inventory, Deduplication and Source Registration 1A remains the next unopened production step, but it was not started.
```

or:

```text
Step 13 remains open because: <exact blocker>.
```

---

## 20. Start now

Begin with the repository and Step 12 guard.

Verify the current official-source properties using official primary sources.

Implement the German Law HAT package, manifest, request/scope policy, source-authority policy, temporal-policy contract, metadata-adapter boundary, and trusted Step 12 runtime integration.

Do not touch the real corpus.

Do not choose the final demo question.

Run all tests and controlled validation.

Commit, push, report, and stop.

Do not start Step 14.
