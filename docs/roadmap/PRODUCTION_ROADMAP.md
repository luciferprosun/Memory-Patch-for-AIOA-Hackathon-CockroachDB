# MEMORY PATCH FOR AIOA — ROADMAPA PRODUKCYJNA v1.0

> **KANONICZNA LINIA PRODUKCYJNA REPOZYTORIUM**
>
> Ten dokument jest nadrzędną, uporządkowaną roadmapą produkcyjną dla tego
> repozytorium. Stepy wykonujemy pojedynczo i w podanej kolejności. Nie wolno
> rozpoczynać następnego stepu bez werdyktu `COMPLETE AND PUSHED` dla poprzedniego
> albo jawnej decyzji użytkownika o jego odłożeniu.
>
> Przyjęto jako kanoniczną roadmapę: 2026-07-26 (Europe/Berlin).
> SHA-256 przekazanego pliku źródłowego:
> `b7d7dc62361a32a449074fcc35a065b502087bbee0cd3461deb99a65d711db6a`.

## Zweryfikowane checkpointy roadmapy — historyczne i bieżące

Poniższy stan łączy checkpoint przy przyjęciu roadmapy z późniejszymi,
audytowanymi zamknięciami i decyzjami użytkownika:

- `Step 0A`: `COMPLETE AND PUSHED` — commit `b3d555ec230a894b541e3570347fcf086511df2a`.
- `Step 0B`: `COMPLETE AND PUSHED` — commit `870145c78d9e6bf02e318bdca2327eb808f381b7`.
- `Step 0C`: przyjęty jako ukończony baseline architektoniczny z roadmapy źródłowej.
- `Step 1`: `COMPLETE AND PUSHED` — commit `3f6d341bc3ceb964a2b25d4913a0695595dbd7d0`.
- `Step 2`: `COMPLETE AND PUSHED` — commit `807b459b3d0270bd84c5590df6e7abf3e4f9842b`.
- `Step 3`: `COMPLETE` — CockroachDB `v26.2.4`; live local spike: `34 PASS / 0 FAIL / 2 DEFER`.
- `Step 4`: `COMPLETE` — CockroachDB `v26.2.4`; 3 deterministyczne migracje, 29 tabel i live schema validation PASS.
- `Step 5`: `COMPLETE` — 4 role `NOLOGIN`, 27 tabel z RLS i FORCE RLS, 50 polityk oraz live isolation validation `95 PASS / 0 FAIL`.
- `Step 6`: `COMPLETE` — typed persistence boundary, retry wyłącznie dla `40001`, durable idempotency/resume, migracja `0005`, 28 tabel z RLS i FORCE RLS oraz pełna walidacja live PASS.
- `Step 7: COMPLETE AND PUSHED at actual closure commit`.
  Step 7 was resumed and completed after Step 9 because it had previously
  been explicitly deferred. The closure adds the deterministic S3 snapshot
  adapter, one retained CloudFormation bucket with Object Lock, and live
  exact-version checksum/retention verification.
- `Step 8: COMPLETE AND PUSHED at actual closure commit`.
  The recovered implementation adds fresh external-volume identity checks,
  operation-specific fail-closed behavior, bounded exact reads, atomic
  no-overwrite writes, and one approved exact-byte live validation.
- `Step 9: COMPLETE AND PUSHED at actual closure commit`.
  Source registry, whole-DAG provenance, publication eligibility, append-only
  events, RLS/FORCE RLS and live validation are closed by the intended Step 9
  commit once it is reachable on `origin/main`.
- `Step 10: COMPLETE AND PUSHED at actual closure commit`.
  The durable idempotent ingestion saga, migration `0007`, Step 6-9 boundary
  integration, exact existing-evidence recovery, and graceful disposable
  CockroachDB shutdown are closed by the intended Step 10 commit once it is
  reachable on `origin/main`.
- `Step 11: COMPLETE AND PUSHED at actual closure commit`.
  Deterministic text/JSON parsing, NFC normalization, sectioning, chunking,
  security findings, migration `0008`, real Step 10 parser/validator ports,
  and zero-external-write live validation are closed by the intended Step 11
  commit once it is reachable on `origin/main`.
- `Step 12: COMPLETE AND PUSHED at actual closure commit`.
- `Step 13: COMPLETE AND PUSHED at actual closure commit`.
- `Step 14: COMPLETE AND PUSHED at actual closure commit`.
  The deterministic bounded inventory, exact and review-only deduplication,
  license/privacy/quarantine classification, external canonical bundle, and
  zero-publication Step 9 registration validation are closed by the intended
  Step 14 commit once it is reachable on `origin/main`.
- `Step 15: COMPLETE AND PUSHED at actual closure commit`.
  The digest-bound external temporal and jurisdictional normalization bundle,
  document/version and supersession candidates, preserved conflicts,
  review-only Step 9 proposals, and graceful disposable CockroachDB validation
  are closed by the intended Step 15 commit once it is reachable on
  `origin/main`.
- `Step 16: COMPLETE AND PUSHED at actual closure commit`
- `Step 17: COMPLETE AND PUSHED at actual closure commit`.
  Deterministyczny Axis A, niezależny Axis B, oddzielenie evidence/answer,
  tenant/user isolation oraz zero-effect offline validation są zamknięte przez
  zamierzony commit Step 17, gdy jest osiągalny na `origin/main`.
- `Step 18: COMPLETE AND PUSHED at actual closure commit`.
  Exact and structured retrieval, German lexical search, route binding,
  hard tenant/HAT/scope/source filters, real Step 16 fixture validation, and
  zero Step 19/20 leakage are closed by the intended Step 18 commit once it is
  reachable on `origin/main`.
- `Step 19: COMPLETE AND PUSHED at actual closure commit`.
  Immutable local-only E5 embeddings, verified external derived cache,
  lineage-bound `VECTOR(384)` persistence, L2 vector candidates, shared hard
  scope, RLS/FORCE RLS, real-model capability proof, and disposable
  CockroachDB validation are closed by the intended Step 19 commit once it is
  reachable on `origin/main`.
- `Step 20: COMPLETE AND PUSHED at actual closure commit`.
  Verified Step 18/19 inputs, exact lineage deduplication, fixed integer
  fusion, deterministic diversity, provider-neutral context bytes, immutable
  Evidence Bundle identity, hard authority/isolation revalidation, and
  controlled real-input/disposable validation are closed by the intended
  Step 20 commit once it is reachable on `origin/main`.
- `Step 21: COMPLETE AND PUSHED at actual closure commit`.
  Explicit current/historical/future time, exact supersession and conflict
  preservation, policy-driven freshness, canonical evidence states, bounded
  same-scope fallback, and offline validation are closed by the intended Step
  21 commit once it is reachable on `origin/main`.
- `Step 22: COMPLETE AND PUSHED at actual closure commit`.
  Provider-neutral original-query-only Draft V1 generation, pinned hosted
  provider/model identity, evidence-leakage prevention, bounded timeout/retry,
  exact Draft V1 byte/hash identity, existing Step 4 draft persistence,
  credential/tool isolation, and controlled validation are closed by the
  intended Step 22 commit once it is reachable on `origin/main`.
- `Step 23: COMPLETE AND PUSHED at actual closure commit`.
  Exact-span deterministic claim extraction, stable claim identities,
  verified Step 20/21 evidence binding, conservative candidate verdicts,
  conflict preservation, and the immutable packet-input snapshot are closed
  by the intended Step 23 commit once it is reachable on `origin/main`.
- `Step 24: COMPLETE AND PUSHED at actual closure commit`.
  Frozen Step 23 input verification, deterministic corrections and
  prohibitions, exact citation/conflict binding, canonical packet identity,
  separate HMAC-SHA-256 integrity receipts, replay/tamper proof, and explicit
  persistence deferral are closed by the intended Step 24 commit once it is
  reachable on `origin/main`.
- `Step 25: COMPLETE AND PUSHED at actual closure commit`.
  Verified packet/HMAC gating, Draft V2 generation through the existing
  tool-less provider boundary, immutable stage-2 persistence, reused exact
  claim spans, layered deterministic and semantic-candidate verification,
  fixed claim verdicts, summary states, and offline controlled validation are
  closed by the intended Step 25 commit once it is reachable on `origin/main`.
- `Step 26: COMPLETE AND PUSHED at actual closure commit`.
  Complete Step 17/20/21/24/25 integrity binding, exact Verified Answer
  assembly, final policy/evidence ceilings, HAT_ENFORCE no-Draft-V1-fallback,
  one same-packet retry with full re-verification, deterministic review and
  bounded-failure outputs, and offline controlled validation are closed by
  the intended Step 26 commit once it is reachable on `origin/main`.
- `Step 27: COMPLETE AND PUSHED at actual closure commit`.
  Owner-private empty Personal Memory HAT slots, versioned configuration and
  lifecycle, hard quota policy, provider-neutral model bindings, database
  owner isolation, archive/export/two-stage logical delete, migration replay,
  and controlled CockroachDB validation are closed by the intended Step 27
  commit once it is reachable on `origin/main`.
- `Step 28: COMPLETE AND PUSHED at actual closure commit`.
  Owner- and slot-bound Correction Candidate Envelopes, exact Kernel/Critic
  producer boundaries, `DETECTED`-only durable intake, hash-bound lineage,
  hard candidate quotas, exact replay/deduplication, RLS/FORCE RLS isolation,
  and controlled CockroachDB validation are closed by the intended Step 28
  commit once it is reachable on `origin/main`.
- `Step 29: COMPLETE AND PUSHED at actual closure commit`.
  Owner-scoped Personal Memory Patch Proposals, the exact
  `DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL`
  progression, immutable canonical-evidence binding, deterministic
  deduplication/conflict/freshness gates, transactional quota and state
  guards, RLS/FORCE RLS isolation, and controlled CockroachDB validation are
  closed by the intended Step 29 commit once it is reachable on
  `origin/main`.
- `Step 30: COMPLETE AND PUSHED at actual closure commit`.
  Exact owner-human approval, hash-bound receipts, three independent replay
  identities, the separate least-privileged Commit Helper, precommit and
  preactivation TOCTOU revalidation, the no-skip
  `AWAITING_APPROVAL -> APPROVED -> COMMITTED -> ACTIVE` progression,
  RLS/FORCE RLS isolation, and controlled CockroachDB validation are closed
  by the intended Step 30 commit once it is reachable on `origin/main`.
- `Step 31: COMPLETE AND PUSHED at actual closure commit`.
  Exact Step 30 ACTIVE-only retrieval, full approval/commit/activation
  lineage verification, owner/tenant/slot RLS isolation, mandatory route
  scope, temporal and provider-neutral model-binding checks, bounded
  deterministic context assembly, two-model same-patch reuse, canonical
  conflict suppression, non-canonical authority separation and controlled
  CockroachDB validation are closed by the intended Step 31 commit once it is
  reachable on `origin/main`.
- `Step 32: COMPLETE AND PUSHED at actual closure commit`.
  Exact owner-scoped Personal Memory supersession, revocation, deterministic
  export, logical deletion, Step 31 retrieval suppression, separate owner
  consent, deterministic de-identification, review-only shared-promotion
  proposals, RLS/FORCE RLS isolation and controlled CockroachDB validation are
  closed by the intended Step 32 commit once it is reachable on `origin/main`.
- `Step 33: COMPLETE AND PUSHED at actual closure commit`.
  Typed security-event normalization, owner-partitioned append-only hash
  chains, explicit genesis, serializable sequence heads, deterministic tamper
  verification, bounded proof-carrying redacted owner export, RLS/FORCE RLS
  isolation and controlled CockroachDB validation are closed by the intended
  Step 33 commit once it is reachable on `origin/main`.
- `Step 34: NOT STARTED`.

## Zasada prowadzenia prac

Każdy step jest osobnym, zamkniętym zadaniem dla Codexa:

1. jeden precyzyjny prompt;
2. guard repozytorium, brancha, HEAD i worktree;
3. implementacja tylko zakresu danego stepu;
4. testy i walidacja;
5. jeden commit;
6. push do `origin/main`;
7. raport zamknięcia;
8. audyt raportu przed uruchomieniem następnego stepu.

Nie rozpoczynamy kolejnego stepu, dopóki poprzedni nie ma werdyktu **COMPLETE AND PUSHED** albo nie został świadomie odłożony.

---

# STAN POCZĄTKOWY

- [x] **Step 0A — Local Toolchain Bootstrap 1A**
  Narzędzia lokalne, `uv`, AWS CLI, `ccloud`, klient CockroachDB, GitHub CLI i guardy repozytorium.

- [x] **Step 0B — External Data Volume Preparation and Safe Migration 1A — CLOSURE**
  Zamknąć przygotowanie `LSC_DATA`, lokalną konfigurację, marker woluminu, strukturę katalogów, skrypty preflight/verify i commit.
  Jeżeli ten step został już wykonany, najpierw potwierdzić raport, commit i czysty worktree.

- [x] **Step 0C — Multi-Model Architecture Audit and Decision Baseline**
  Grok, Meta AI, Gemini, DeepSeek, Sonnet, Kimi oraz końcowa decyzja architektoniczna.

---

# FAZA 1 — KERNEL CORE I GRANICE AUTORYTETU

## Cel fazy

Zamrozić neutralny domenowo Kernel Core, HAT SDK, Personal Memory HAT-y, typy stanów, autorytet, pamięć i izolację użytkowników — bez bazy, chmury, modeli i niemieckiego korpusu.

- [x] **Step 1 — Knowledge Kernel Contracts, HAT SDK and Personal Memory HAT Slots Foundation 1A**
  Kontrakty Kernel Core, Knowledge HAT, Personal Memory HAT, trust hierarchy, quota policy, scope dimensions, state machines, hashing, JSON schemas, ADR-y i testy standard-library-only.

- [x] **Step 2 — Kernel Contract Re-Audit and Authority Invariant Closure 1A**
  Audyt implementacji Stepu 1: brak model authority, brak execution authority HAT-u, brak cross-user path, brak bezpośredniego `PROPOSED → ACTIVE`, deterministyczne hashe i poprawne granice Critic Loop.
  Zamknięcie: commit `807b459b3d0270bd84c5590df6e7abf3e4f9842b`; 211/211 testów; contract validator PASS; utwardzone granice authority, isolation, binding, mutability i schema. [Kanoniczny rekord zamknięcia](../audits/STEP_2_KERNEL_CONTRACT_REAUDIT_CLOSURE_1A.md).

### Gate fazy 1

Faza jest zamknięta, gdy:

- Kernel nie zawiera reguł niemieckiego prawa;
- dwa różne przykładowe HAT-y mogą użyć tych samych kontraktów;
- Personal Memory HAT jest prywatną przestrzenią danych, nie wykonywalnym pluginem;
- wszystkie state machines i testy izolacji przechodzą;
- repozytorium jest czyste i zsynchronizowane.

---

# FAZA 2 — COCKROACHDB, TENANTY I TRANSAKCJE

## Cel fazy

Udowodnić możliwości docelowej wersji CockroachDB i wdrożyć trwały, wielodostępny model danych z retry-safe transakcjami.

- [x] **Step 3 — CockroachDB v26.2 Capability Spike and Version Pin 1A**
  Zweryfikować na realnym środowisku: `VECTOR`, vector index, full-text search, RLS, `FORCE ROW LEVEL SECURITY`, Row-Level TTL, changefeed, partial unique indexes, `AS OF SYSTEM TIME`, transakcje `SERIALIZABLE` i błędy `40001`.
  Wynik: macierz PASS/FAIL/DEFER i przypięta wersja klastra.
  Zamknięcie: CockroachDB `v26.2.4`, disposable local single-node na loopback, `34 PASS / 0 FAIL / 2 DEFER`. [Baseline możliwości](../architecture/COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md), [macierz evidence](../evidence/cockroachdb-v26-2/capability-matrix.json), [rekord zamknięcia](../audits/STEP_3_COCKROACHDB_CAPABILITY_SPIKE_CLOSURE_1A.md).

- [x] **Step 4 — CockroachDB Logical Schema and Migration Foundation 1A**
  Tabele dla tenantów, użytkowników, HAT-ów, źródeł, snapshotów, wersji, chunków, kernel runs, routing decisions, evidence bundles, packets, drafts, verdicts, personal memory, patch proposals, approvals, committed patches i audit events.
  Zamknięcie: CockroachDB `v26.2.4`, 3 forward-only migracje, 29 tenant-ready tabel, identyczna reprodukcja dwóch świeżych schematów, no-op drugiego uruchomienia i negatywne constraint probes PASS. [Baseline schematu](../architecture/COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md), [live evidence](../evidence/cockroachdb-v26-2/step4-schema-validation.json), [rekord zamknięcia](../audits/STEP_4_COCKROACHDB_SCHEMA_MIGRATION_CLOSURE_1A.md).

- [x] **Step 5 — Tenant Roles, Session Context and Row-Level Security 1A**
  Role SQL, tenant context, RLS, `FORCE ROW LEVEL SECURITY`, brak `BYPASSRLS`, negatywne testy cross-tenant i cross-user.
  Zamknięcie: CockroachDB `v26.2.4`, cztery role `NOLOGIN` bez `BYPASSRLS`, transakcyjny trusted context, 27 tabel chronionych przez RLS i FORCE RLS, 50 polityk oraz live validation `95 PASS / 0 FAIL`. [Model izolacji](../architecture/COCKROACHDB_TENANT_ROLES_SESSION_CONTEXT_AND_RLS_1A.md), [live evidence](../evidence/cockroachdb-v26-2/step5-rls-validation.json), [rekord zamknięcia](../audits/STEP_5_TENANT_RLS_CLOSURE_1A.md).

- [x] **Step 6 — Persistence Adapters, Idempotency and Transaction Retry Foundation 1A**
  Adapter CockroachDB, krótkie transakcje, idempotency keys, obsługa `40001`, immutable evidence IDs, resume states i brak transakcji otwartych podczas wywołań modeli lub AWS.
  Zamknięcie: CockroachDB `v26.2.4`, typed DB-API boundary bez nieprzypiętej zależności, pełny `SERIALIZABLE` retry tylko dla `40001`, 10 prób z backoffem do 1 sekundy, durable compare-and-set idempotency/resume, neutralny composite external-reference prewire, migracja `0005`, RLS/FORCE RLS oraz pełna walidacja live PASS. [Model persistence](../architecture/COCKROACHDB_PERSISTENCE_IDEMPOTENCY_RETRY_FOUNDATION_1A.md), [live evidence](../evidence/cockroachdb-v26-2/step6-persistence-validation.json), [rekord zamknięcia](../audits/STEP_6_PERSISTENCE_IDEMPOTENCY_RETRY_CLOSURE_1A.md).

### Gate fazy 2

- schemat tworzy się od zera i migruje powtarzalnie;
- User A nie odczytuje ani nie modyfikuje User B;
- retry nie tworzy duplikatów;
- model/HAT nie ma credentials do approval/commit;
- capability spike wskazuje, co naprawdę wchodzi do MVP.

---

# FAZA 3 — STORAGE, S3 I INGESTION

## Cel fazy

Zbudować bezpieczne, idempotentne przyjmowanie źródeł: CockroachDB jako autorytet stanu, S3 jako dokładne wersje bajtów, USB jako dane pochodne i cache.

- [x] **Step 7 — S3 Snapshot Authority and Object Lock Adapter 1A**
  Versioning, Object Lock Governance dla development/hackathon, `s3_version_id`, SHA-256, content length, retention metadata, prywatne i globalne klasy snapshotów, bez twierdzenia o cross-system ACID.
  `Step 7: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie wykonano historycznie po Step 9, ponieważ Step 7 był wcześniej
  jawnie odroczony. Globalny adapter wymusza deterministyczną tożsamość,
  checksum, exact version, Object Lock i storage-only evidence. Prywatna klasa
  pozostaje oddzielona i jest odrzucana przez globalny locked adapter.
  [Architektura](../architecture/S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_ADAPTER_1A.md),
  [ADR-015](../adr/ADR-015-s3-snapshot-object-lock-and-cloudformation-boundary.md),
  [live evidence](../evidence/aws-s3/step7-s3-snapshot-validation.json),
  [rekord zamknięcia](../audits/STEP_7_S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_CLOSURE_1A.md),
  [historyczny rekord odroczenia](../audits/STEP_7_STEP_8_EXPLICIT_DEFERRAL_2026_07_29.md).

- [x] **Step 8 — External Volume Runtime Adapter and Fail-Closed Policy 1A**
  Integracja przygotowanego `LSC_DATA`: identity check, marker, read/write, free space, operation-specific failure policy i zakaz fallbacku dużych zapisów na dysk systemowy.
  `Step 8: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: typed runtime adapter, świeża weryfikacja mount/device/marker,
  operation-specific fail-closed policy, pełny zakaz fallbacku na dysk
  systemowy, ochrona przed symlinkami i special files, bounded exact reads,
  atomowy no-overwrite write oraz jedna zatwierdzona walidacja live 88 bajtów
  z exact read-back.
  [Architektura](../architecture/EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md),
  [ADR-016](../adr/ADR-016-external-volume-runtime-fail-closed-boundary.md),
  [live evidence](../evidence/external-volume/step8-external-volume-validation.json),
  [rekord zamknięcia](../audits/STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md),
  [historyczny rekord odroczenia](../audits/STEP_7_STEP_8_EXPLICIT_DEFERRAL_2026_07_29.md).

- [x] **Step 9 — Source Registry, Provenance and Publication States 1A**
  Rejestr źródeł, authority level, licencja/status, jurysdykcje lub inne scope dimensions, parser version, transformation version, hash lineage i publication eligibility.
  `Step 9: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: typed registration, deterministyczny scope i pełny provenance DAG, publication eligibility, append-only event chain, optimistic compare-and-set, Step 6 durable idempotency, RLS/FORCE RLS, 74 focused tests, `48 PASS / 0 FAIL` live probes i pełny regression PASS.
  [Architektura](../architecture/SOURCE_REGISTRY_PROVENANCE_PUBLICATION_STATES_1A.md),
  [ADR-014](../adr/ADR-014-source-registry-provenance-publication-boundary.md),
  [live evidence](../evidence/cockroachdb-v26-2/step9-source-registry-validation.json),
  [rekord zamknięcia](../audits/STEP_9_SOURCE_REGISTRY_PROVENANCE_PUBLICATION_CLOSURE_1A.md).

- [x] **Step 10 — Idempotent S3–CockroachDB Ingestion Saga 1A**
  Stany: `REGISTERED → ACQUIRED_LOCAL → HASH_VERIFIED → SNAPSHOT_UPLOAD_PENDING → SNAPSHOT_UPLOADED → SNAPSHOT_LOCK_VERIFIED → PARSED → VALIDATED → PUBLISHED`, z retry, reconciliation, quarantine i cleanup orphanów.
  `Step 10: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: deterministyczna saga i append-only event chain, durable intent
  i receipt, bounded worker claims, migracja `0007`, RLS/FORCE RLS,
  Step 8 local reconciliation, Step 7 exact-version Object Lock evidence,
  Step 9 publication transition, typed synthetic parser/validator receipts,
  zero-write recovery po zachowanym pierwszym nieudanym cleanup oraz graceful
  CockroachDB drain bez force kill.
  [Architektura](../architecture/IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_1A.md),
  [ADR-017](../adr/ADR-017-idempotent-s3-cockroachdb-ingestion-saga-boundary.md),
  [runbook](../operations/STEP_10_INGESTION_SAGA_LIVE_VALIDATION_1A.md),
  [failed-attempt evidence](../evidence/ingestion/step10-ingestion-saga-validation-failure.json),
  [recovery evidence](../evidence/ingestion/step10-ingestion-saga-validation.json),
  [rekord zamknięcia](../audits/STEP_10_IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_CLOSURE_1A.md).

- [x] **Step 11 — Generic Parsing, Normalization and Chunking Pipeline 1A**
  Neutralne parser contracts, dokumenty, sekcje, char ranges, chunk IDs, deterministic chunking, prompt-injection flags, quarantine i testowe źródła syntetyczne.
  `Step 11: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: dokładny dispatch `text/plain` i `application/json`, strict UTF-8,
  Unicode NFC, deterministyczne sekcje i chunki, statyczne security findings,
  migracja `0008`, RLS/FORCE RLS, realne porty Step 10 z
  `synthetic_validation_boundary=false`, oraz live reconciliation z zerem
  nowych zapisów S3 i external-volume.
  [Architektura](../architecture/GENERIC_PARSING_NORMALIZATION_CHUNKING_PIPELINE_1A.md),
  [ADR-018](../adr/ADR-018-generic-parsing-normalization-chunking-boundary.md),
  [runbook](../operations/STEP_11_PARSING_PIPELINE_LIVE_VALIDATION_1A.md),
  [live evidence](../evidence/parsing/step11-parsing-pipeline-validation.json),
  [rekord zamknięcia](../audits/STEP_11_GENERIC_PARSING_NORMALIZATION_CHUNKING_PIPELINE_CLOSURE_1A.md).

### Gate fazy 3

Jedno prawdziwe, neutralne źródło można:

- zarejestrować;
- pobrać;
- zahashować;
- zapisać jako konkretną wersję S3;
- sparsować;
- podzielić;
- opublikować;
- ponownie uruchomić bez duplikacji;
- odtworzyć z pełnym provenance.

---

# FAZA 4 — HAT PLATFORM I GERMAN LAW HAT

## Cel fazy

Zaimplementować neutralną platformę HAT-ów, a następnie podłączyć pełną bibliotekę niemieckiego prawa jako pierwszy realny HAT — bez hardcodowania pytania demonstracyjnego.

- [x] **Step 12 — HAT Registry, Manifest Validation and Runtime Boundary 1A**
  Rejestr HAT-ów, kompatybilność wersji, scope dimensions, required capabilities, source policies, no-execution enforcement i bezpieczne ładowanie tylko zatwierdzonych manifestów.
  `Step 12: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: strict local JSON, typed `HatManifest`, SemVer Kernel API gate,
  capability vocabulary, zero-authority enforcement, migracja `0009`,
  append-only registry events, trusted operator receipts, jawny system-installed
  catalog i fixed runtime capability gate bez dynamicznego ładowania.
  [Architektura](../architecture/HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_1A.md),
  [ADR-019](../adr/ADR-019-trusted-hat-registry-runtime-boundary.md),
  [runbook](../operations/STEP_12_HAT_REGISTRY_LIVE_VALIDATION_1A.md),
  [live evidence](../evidence/hats/step12-hat-registry-validation.json),
  [rekord zamknięcia](../audits/STEP_12_HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_CLOSURE_1A.md).

- [x] **Step 13 — German Law HAT Package and Source Authority Policy 1A**
  Manifest German Law HAT, domeny, języki, jurysdykcje, hierarchia źródeł urzędowych i wtórnych, polityka temporalna, parser adapters i contract tests.
  Bez wybierania końcowego pytania testowego.
  `Step 13: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: `german-law@1.0.0`, jawne scope federal/state/EU i
  `knowledge_as_of`, typed source-authority i temporal policy, fixed
  non-fetching metadata adapters, Step 12 trusted runtime integration oraz
  controlled CockroachDB validation bez korpusu i z zerem zapisów zewnętrznych.
  [Architektura](../architecture/GERMAN_LAW_HAT_SOURCE_AUTHORITY_POLICY_1A.md),
  [ADR-020](../adr/ADR-020-german-law-hat-source-authority-policy.md),
  [official-source research](../provenance/STEP_13_OFFICIAL_SOURCE_RESEARCH_1A.md),
  [live evidence](../evidence/hats/step13-german-law-hat-policy-validation.json),
  [rekord zamknięcia](../audits/STEP_13_GERMAN_LAW_HAT_SOURCE_AUTHORITY_CLOSURE_1A.md).

- [x] **Step 14 — German Law Corpus Inventory, Deduplication and Source Registration 1A**
  Pełna inwentaryzacja posiadanej biblioteki, hashe, duplikaty, near-duplicates, źródła urzędowe, prywatność, licensing, quarantine i mapping do source registry.
  `Step 14: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: 19,391 stabilnych plików i 1,963,389,627 bajtów odczytanych
  strumieniowo bez mutacji źródła; 28 grup exact-duplicate, 24 kandydatów
  near-duplicate, 6,124 deterministyczne kandydaty rejestracji, zewnętrzny
  canonical bundle, exact replay/conflict/isolation PASS i zero publikacji.
  [Architektura](../architecture/GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_1A.md),
  [ADR-021](../adr/ADR-021-german-law-corpus-inventory-dedup-source-registration.md),
  [runbook](../operations/STEP_14_GERMAN_LAW_CORPUS_INVENTORY_VALIDATION_1A.md),
  [live evidence](../evidence/corpus/step14-german-law-corpus-inventory-summary.json),
  [rekord zamknięcia](../audits/STEP_14_GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_CLOSURE_1A.md).

- [x] **Step 15 — German Law Temporal and Jurisdictional Normalization 1A**
  `published_at`, `effective_from`, `effective_to`, `retrieved_at`, `ingested_at`, `superseded_at`, `verified_at`, zakres jurysdykcji, wersje i konflikty.
  `Step 15: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie: 6,134 hash-bound rekordów metadanych znormalizowanych do
  zewnętrznego bundle’a bez mutacji corpus; 6,124 tożsamości dokumentów i
  wersji, 24 kandydatów relacji wersji, zachowane konflikty i wyłącznie
  review-only propozycje Step 9.  Controlled CockroachDB validation potwierdza
  exact replay, conflict rejection, zero publikacji i graceful cleanup bez
  force kill.
  [Architektura](../architecture/GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_1A.md),
  [ADR-022](../adr/ADR-022-german-law-temporal-jurisdictional-normalization.md),
  [runbook](../operations/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_VALIDATION_1A.md),
  [live evidence](../evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json),
  [rekord zamknięcia](../audits/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_CLOSURE_1A.md).

- [x] **Step 16 — German Law HAT Publication and Corpus Verification 1A**
  Publikacja zweryfikowanych wersji, provenance chain, parser/chunk coverage, walidacja temporalna i raport braków.
  `Step 16: COMPLETE AND PUSHED at actual closure commit`.
  Zrealizowanie obejmuje 6,124 kandydatów publikacji, 3,544 opublikowanych wersji, pełny proof `Object Lock`, deterministyczny brak modeli i brak śladów Step 17.
  [Architektura](../architecture/GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_1A.md),
  [ADR-023](../adr/ADR-023-german-law-hat-publication-corpus-verification.md),
  [runbook](../operations/STEP_16_GERMAN_LAW_HAT_PUBLICATION_VALIDATION_1A.md),
  [live evidence](../evidence/corpus/step16-german-law-hat-publication-summary.json),
  [rekord zamknięcia](../audits/STEP_16_GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_CLOSURE_1A.md).
  Kernel ma być gotowy na dowolne później wybrane pytanie z dostępnego korpusu.

### Gate fazy 4

- German Law HAT jest pluginem korzystającym z neutralnego SDK;
- Kernel Core nie zna konkretnych ustaw ani pytań;
- korpus ma wersje, scope i provenance;
- błędne lub niepewne dokumenty są w quarantine;
- późniejsze pytanie testowe można wybrać bez zmiany architektury.

---

# FAZA 5 — ROUTING I RETRIEVAL

## Cel fazy

Zbudować deterministyczne wybranie HAT-u, twarde scope filters i Evidence Bundle, którego semantic ranking nie może unieważnić.

- [x] **Step 17 — Axis A Router, Axis B Policy Gate and Evidence Status 1A**
  `PASS_THROUGH / HAT_ASSIST / HAT_ENFORCE / AMBIGUOUS`; niezależne
  `ALLOW_ANSWER / BLOCK_ANSWER / REQUIRE_CONFIRMATION` oraz
  `ALLOW / ALLOW_SCOPED / REQUIRE_HUMAN / DENY`; osobne evidence status i
  answer status.
  `Step 17: COMPLETE AND PUSHED at actual closure commit`.
  Zrealizowanie obejmuje hash-bound snapshot istniejącego trusted HAT registry,
  domain-neutral routing, osobne knowledge/execution policy ceilings, częściowe
  evidence bez zmiany DB vocabulary, German Law fixture oraz brak zewnętrznych
  efektów i implementacji Step 18.
  [Architektura](../architecture/AXIS_A_ROUTER_AXIS_B_POLICY_GATE_EVIDENCE_STATUS_1A.md),
  [ADR-024](../adr/ADR-024-axis-a-router-axis-b-policy-evidence-status.md),
  [runbook](../operations/STEP_17_ROUTING_POLICY_VALIDATION_1A.md),
  [offline evidence](../evidence/routing/step17-routing-policy-validation.json),
  [rekord zamknięcia](../audits/STEP_17_AXIS_A_ROUTER_AXIS_B_POLICY_GATE_CLOSURE_1A.md).

- [x] **Step 18 — Exact and Full-Text Retrieval Baseline 1A**
  Exact identifiers, statute/section lookup, German full-text, keyword search, source authority, tenant/HAT isolation i twarde scope filters.
  `Step 18: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje immutable i hash-bound contracts, pełne związanie z
  `KnowledgeRouteResult`, parametryzowany read-only repository, hard filters
  przed candidate generation, ponowne użycie istniejącego German `TSVECTOR`
  i Source Registry oraz controlled CockroachDB validation na jednym
  zweryfikowanym elemencie korpusu Step 16.
  [Architektura](../architecture/EXACT_FULL_TEXT_RETRIEVAL_BASELINE_1A.md),
  [ADR-025](../adr/ADR-025-exact-full-text-retrieval-hard-scope-filtering.md),
  [runbook](../operations/STEP_18_RETRIEVAL_VALIDATION_1A.md),
  [live evidence](../evidence/retrieval/step18-exact-fulltext-retrieval-validation.json),
  [rekord zamknięcia](../audits/STEP_18_EXACT_FULL_TEXT_RETRIEVAL_CLOSURE_1A.md).

- [x] **Step 19 — Embedding Generation and Vector Retrieval Foundation 1A**
  Przypięty model embeddingowy, wersja i dimension, batch generation, cache na external volume, rekordy w CockroachDB, capability proof i resource limits.
  `Step 19: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje immutable/hash-bound contracts, przypięty lokalny model
  `intfloat/multilingual-e5-small` wraz z revision i safetensors SHA-256,
  zweryfikowany external-volume cache, migrację `0010` z `VECTOR(384)`, L2
  vector index i RLS/FORCE RLS, ponowne użycie hard scope Step 18 oraz
  kontrolowaną walidację realnego modelu, korpusu Step 16 i disposable
  CockroachDB.
  [Architektura](../architecture/EMBEDDING_GENERATION_VECTOR_RETRIEVAL_FOUNDATION_1A.md),
  [ADR-026](../adr/ADR-026-embedding-generation-vector-retrieval-foundation.md),
  [runbook](../operations/STEP_19_EMBEDDING_VECTOR_VALIDATION_1A.md),
  [live evidence](../evidence/retrieval/step19-embedding-vector-validation.json),
  [rekord zamknięcia](../audits/STEP_19_EMBEDDING_VECTOR_RETRIEVAL_CLOSURE_1A.md).

- [x] **Step 20 — Hybrid Retrieval, Evidence Bundle and Deterministic Ranking 1A**
  Łączenie exact, full-text i vector candidates, hard filters przed finalnym dopuszczeniem, deterministic ordering, diversity, context budget i zamrożony Evidence Bundle.
  `Step 20: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje bezpośrednią weryfikację hash/binding Step 18 i Step 19,
  exact-lineage deduplication, fixed-point RRF z exact priority, bounded
  deterministic diversity, provider-neutral UTF-8 byte budget, immutable
  Evidence Bundle, ponowną walidację authority/isolation oraz controlled
  validation z realnymi wynikami Step 18/19 i disposable CockroachDB.
  [Architektura](../architecture/HYBRID_RETRIEVAL_EVIDENCE_BUNDLE_DETERMINISTIC_RANKING_1A.md),
  [ADR-027](../adr/ADR-027-hybrid-retrieval-evidence-bundle-deterministic-ranking.md),
  [runbook](../operations/STEP_20_HYBRID_EVIDENCE_VALIDATION_1A.md),
  [live evidence](../evidence/retrieval/step20-hybrid-evidence-bundle-validation.json),
  [rekord zamknięcia](../audits/STEP_20_HYBRID_RETRIEVAL_EVIDENCE_BUNDLE_CLOSURE_1A.md).

- [x] **Step 21 — Temporal Resolver, Conflict Detection and Freshness Policy 1A**
  Historyczne i bieżące pytania, future-effective, repealed/superseded, conflicting evidence, stale sources, insufficient evidence i completeness fallback.
  `Step 21: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje hash-bound question time, start-inclusive/end-exclusive
  applicability, explicit supersession graph and conflict groups, separate
  policy-driven freshness, canonical evidence status, one bounded same-scope
  completeness fallback, preserved Step 17/20 authority and isolation, and
  controlled offline validation with an honest real/synthetic fixture split.
  [Architektura](../architecture/TEMPORAL_RESOLVER_CONFLICT_FRESHNESS_POLICY_1A.md),
  [ADR-028](../adr/ADR-028-temporal-resolution-conflict-preservation-freshness-policy.md),
  [runbook](../operations/STEP_21_TEMPORAL_RESOLUTION_VALIDATION_1A.md),
  [validation evidence](../evidence/retrieval/step21-temporal-conflict-freshness-validation.json),
  [rekord zamknięcia](../audits/STEP_21_TEMPORAL_RESOLVER_CONFLICT_FRESHNESS_CLOSURE_1A.md).

### Gate fazy 5

Dla dowolnego obsługiwanego pytania Kernel zwraca:

- prawidłowy route;
- niezależną politykę działania;
- właściwy HAT;
- Evidence Bundle z dokładnymi wersjami;
- albo precyzyjny stan `INSUFFICIENT / CONFLICTING / UNAVAILABLE / STALE`.

---

# FAZA 6 — DRAFT V1, CORRECTION PACKET I DRAFT V2

## Cel fazy

Udowodnić rzeczywistą korektę modelu, bez ukrywania błędnej odpowiedzi i bez zwracania nieweryfikowalnego Draft V2.

- [x] **Step 22 — Provider-Neutral Model Adapter and Draft V1 1A**
  Oryginalne pytanie bez correction evidence, model metadata, timeout/retry, Draft V1 hash i brak dostępu modelu do bazy lub authority credentials.
  `Step 22: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje pinned provider/model configuration, provider-neutral
  interface, original-query-only prompt projection, sentinel evidence-leakage
  negative, tools disabled, isolated credentials, bounded timeout/retry,
  immutable Draft V1 text/hash, reuse istniejącego Step 4 draft persistence,
  tenant/user isolation i kontrolowaną walidację bez authority escalation.
  [Architektura](../architecture/PROVIDER_NEUTRAL_MODEL_ADAPTER_DRAFT_V1_1A.md),
  [ADR-029](../adr/ADR-029-provider-neutral-evidence-blind-draft-v1.md),
  [runbook](../operations/STEP_22_MODEL_DRAFT_V1_VALIDATION_1A.md),
  [validation evidence](../evidence/modeling/step22-provider-neutral-draft-v1-validation.json),
  [rekord zamknięcia](../audits/STEP_22_PROVIDER_NEUTRAL_MODEL_DRAFT_V1_CLOSURE_1A.md).

- [x] **Step 23 — Claim Extraction and Evidence Binding 1A**
  Claims z Draft V1, span references, claim IDs, evidence mappings, `SUPPORTED / REFUTED / UNVERIFIED` candidates i zamrożenie tego, co faktycznie trafi do packetu.
  `Step 23: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje exact Unicode code-point spans, deterministic claim IDs,
  typed atomicity/non-factual classification, hash-verified Step 20/21 evidence
  linkage, strict temporal/source-authority ceilings, conflict preservation,
  candidate-only `SUPPORTED / REFUTED / UNVERIFIED` semantics oraz immutable
  `PacketInputSnapshot` bez retrieval widening i bez authority escalation.
  [Architektura](../architecture/CLAIM_EXTRACTION_EVIDENCE_BINDING_1A.md),
  [ADR-030](../adr/ADR-030-claim-extraction-evidence-binding.md),
  [runbook](../operations/STEP_23_CLAIM_EVIDENCE_BINDING_VALIDATION_1A.md),
  [validation evidence](../evidence/modeling/step23-claim-evidence-binding-validation.json),
  [rekord zamknięcia](../audits/STEP_23_CLAIM_EXTRACTION_EVIDENCE_BINDING_CLOSURE_1A.md).

- [x] **Step 24 — Correction Packet Construction and Integrity 1A**
  Canonical JSON, ordered evidence, required corrections, prohibited claims, conflicts, citations, policy, scope, packet hash/HMAC boundary i deterministic replay.
  `Step 24: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje verified `PacketInputSnapshot`, deterministyczne
  correction/prohibition rules, immutable citation/conflict identities,
  fixed packet policy, canonical JSON/hash, domain-separated HMAC-SHA-256
  receipt, byte-identical replay, tamper negatives, brak nowej migracji oraz
  jawne odroczenie zapisu do skoordynowanej trwałej linii upstream.
  [Architektura](../architecture/CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_1A.md),
  [ADR-031](../adr/ADR-031-correction-packet-construction-integrity.md),
  [runbook](../operations/STEP_24_CORRECTION_PACKET_VALIDATION_1A.md),
  [validation evidence](../evidence/modeling/step24-correction-packet-validation.json),
  [rekord zamknięcia](../audits/STEP_24_CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_CLOSURE_1A.md).

- [x] **Step 25 — Draft V2 Generation and Layered Claim Verifier 1A**
  Draft V1 + packet → Draft V2; schema checks, deterministic fact/date/source checks, semantic claim verification i brak automatycznego zaufania do modelu-verifiera.
  `Step 25: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje packet hash/HMAC gate przed model call, reuse Step 22
  provider boundary bez tools, immutable Draft V2 i existing Step 4 stage-2
  persistence, Step 23 exact spans, deterministic packet/fact/date/source/
  citation/evidence layers, bounded semantic candidate signals, deterministic
  failure precedence, typed claim verdicts i hash-bound verification summary.
  [Architektura](../architecture/DRAFT_V2_GENERATION_LAYERED_CLAIM_VERIFIER_1A.md),
  [ADR-032](../adr/ADR-032-draft-v2-layered-claim-verifier.md),
  [runbook](../operations/STEP_25_DRAFT_V2_LAYERED_VERIFIER_VALIDATION_1A.md),
  [validation evidence](../evidence/modeling/step25-draft-v2-layered-verifier-validation.json),
  [rekord zamknięcia](../audits/STEP_25_DRAFT_V2_LAYERED_CLAIM_VERIFIER_CLOSURE_1A.md).

- [x] **Step 26 — Verified Answer Assembly and Fail-Closed Output 1A**
  Zwrócenie tylko zweryfikowanej odpowiedzi; dla `HAT_ENFORCE` brak fallbacku do znanego błędnego Draft V1; retry raz, potem human review lub bounded failure.
  `Step 26: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje complete upstream hash/identity gate, immutable exact
  Draft V2 `VerifiedAnswer`, deterministic policy/evidence ceilings,
  packet-bound citations and claim coverage, structurally forbidden Draft V1
  fallback, one same-packet/no-new-evidence retry, full Step 25 re-verification,
  typed review/failure outputs, explicit no-migration persistence decision i
  zero approval/execution/Personal Memory authority.
  [Architektura](../architecture/VERIFIED_ANSWER_ASSEMBLY_FAIL_CLOSED_OUTPUT_1A.md),
  [ADR-033](../adr/ADR-033-verified-answer-fail-closed-output.md),
  [runbook](../operations/STEP_26_VERIFIED_ANSWER_VALIDATION_1A.md),
  [validation evidence](../evidence/modeling/step26-verified-answer-fail-closed-validation.json),
  [rekord zamknięcia](../audits/STEP_26_VERIFIED_ANSWER_FAIL_CLOSED_OUTPUT_CLOSURE_1A.md).

### Gate fazy 6

- Draft V1 jest naprawdę niekorygowany;
- packet zawiera dokładnie użyte dowody;
- Draft V2 jest claim-by-claim verified;
- nieweryfikowalna odpowiedź zostaje zablokowana;
- model nie zmienia route, policy ani pamięci.

---

# FAZA 7 — PERSONAL MEMORY HAT I UCZENIE W AIOA

## Cel fazy

Dać użytkownikowi pulę prywatnych HAT-ów, w których zatwierdzone korekty stają się trwałą, model-independent pamięcią AIOA.

- [x] **Step 27 — Personal Memory HAT Persistence, Quotas and Model Bindings 1A**
  Puste sloty, konfiguracja, aktywacja, quota policy, model bindings, owner isolation, archive/export/delete contracts i brak zależności pamięci od Gemmy.
  `Step 27: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje Step 1 contract reuse, immutable owner-bound empty slot,
  explicit configuration/lifecycle commands, hard transactional quotas,
  provider/model-neutral exact bindings, Step 4/5/6 persistence and
  idempotency reuse, migration 0011, database RLS/FORCE RLS owner isolation,
  canonical configuration export, two-stage logical delete, Gemma
  independence i zero patch/approval/activation/retrieval authority.
  [Architektura](../architecture/PERSONAL_MEMORY_HAT_PERSISTENCE_QUOTAS_MODEL_BINDINGS_1A.md),
  [ADR-034](../adr/ADR-034-personal-memory-hat-persistence.md),
  [runbook](../operations/STEP_27_PERSONAL_MEMORY_VALIDATION_1A.md),
  [validation evidence](../evidence/personal-memory/step27-personal-memory-persistence-validation.json),
  [rekord zamknięcia](../audits/STEP_27_PERSONAL_MEMORY_HAT_PERSISTENCE_CLOSURE_1A.md).

- [x] **Step 28 — Knowledge Hub and Critic Prompt Loop Correction Candidate Bridge 1A**
  Critic Loop i Kernel mogą zgłaszać Correction Candidate Envelope, ale nie aprobować, commitować ani aktywować pamięci.
  `Step 28: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje immutable/hash-bound candidate envelope, exact
  owner/tenant/slot/model/route/result lineage, wyłącznie producentów
  `KNOWLEDGE_KERNEL` i `CRITIC_PROMPT_LOOP`, stan `DETECTED`, durable
  insert/read reuse istniejącego carrier table, migration 0012,
  RLS/FORCE RLS, hard quota, idempotent replay, deterministic exact dedup,
  syntetyczny Critic fixture oraz zero proposal/evidence-validation/approval/
  commit/activation/retrieval/canonical-evidence authority.
  [Architektura](../architecture/KNOWLEDGE_HUB_CRITIC_CORRECTION_CANDIDATE_BRIDGE_1A.md),
  [ADR-035](../adr/ADR-035-correction-candidate-bridge.md),
  [runbook](../operations/STEP_28_CORRECTION_CANDIDATE_BRIDGE_VALIDATION_1A.md),
  [validation evidence](../evidence/personal-memory/step28-correction-candidate-bridge-validation.json),
  [rekord zamknięcia](../audits/STEP_28_KNOWLEDGE_HUB_CRITIC_CANDIDATE_BRIDGE_CLOSURE_1A.md).

- [x] **Step 29 — Personal Memory Patch Proposal and Evidence Validation 1A**
  `DETECTED → PROPOSED → EVIDENCE_BOUND → VALIDATED → AWAITING_APPROVAL`, dedup, conflict check, stale evidence check i owner scope.
  `Step 29: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje immutable/hash-bound proposal, exact reuse Step 28
  candidate i Step 27 target binding, canonical Step 20/21/23/24/26 evidence
  lineage, no-skip state machine, deterministic dedup/conflict/freshness/
  temporal gates, quota and model-binding revalidation, immutable validation
  receipt, migration 0013, RLS/FORCE RLS, owner isolation, controlled
  CockroachDB validation oraz zero approval/commit/activation authority.
  [Architektura](../architecture/PERSONAL_MEMORY_PATCH_PROPOSAL_EVIDENCE_VALIDATION_1A.md),
  [ADR-036](../adr/ADR-036-personal-memory-patch-proposal-validation.md),
  [runbook](../operations/STEP_29_PERSONAL_MEMORY_PATCH_VALIDATION_1A.md),
  [validation evidence](../evidence/personal-memory/step29-personal-memory-patch-validation.json),
  [rekord zamknięcia](../audits/STEP_29_PERSONAL_MEMORY_PATCH_PROPOSAL_VALIDATION_CLOSURE_1A.md).

- [x] **Step 30 — User Approval, Commit Helper and Activation 1A**
  Oddzielne approval i technical commit, revalidation hashy, dedicated credentials, replay protection, `APPROVED → COMMITTED → ACTIVE`.
  `Step 30: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje wyłącznie jawne zatwierdzenie dokładnego proposal przez
  właściciela-człowieka, immutable approval/commit/activation receipts,
  niezależną ochronę replay, oddzielny `mp_personal_memory_commit_helper`,
  ponowną walidację hashy, slotu, quota, evidence i model binding przed
  commitem oraz aktywacją, migration 0014, RLS/FORCE RLS, owner isolation,
  TOCTOU negatives, identyczność treści proposal→committed→active i zero
  retrieval/cross-model/external-execution authority.
  [Architektura](../architecture/USER_APPROVAL_COMMIT_HELPER_ACTIVATION_1A.md),
  [ADR-037](../adr/ADR-037-user-approval-commit-helper-activation.md),
  [runbook](../operations/STEP_30_USER_APPROVAL_COMMIT_ACTIVATION_VALIDATION_1A.md),
  [validation evidence](../evidence/personal-memory/step30-user-approval-commit-activation-validation.json),
  [rekord zamknięcia](../audits/STEP_30_USER_APPROVAL_COMMIT_HELPER_ACTIVATION_CLOSURE_1A.md).

- [x] **Step 31 — Active Patch Retrieval and Cross-Model Reuse 1A**
  Użycie aktywnego patcha przy późniejszym pytaniu, scope/temporal checks, ta sama pamięć dla Gemmy lub innego modelu, bez traktowania patcha jako canonical evidence.
  `Step 31: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje immutable/hash-bound request, assessment, result i
  private context envelope, pełną Step 29/30 lineage verification,
  ACTIVE-only owner/tenant/slot RLS retrieval, exact scope/temporal/model
  binding gates, reuse tego samego patch ID/hash przez dwa provider-neutral
  modele, odrzucenie niepowiązanego modelu, canonical-conflict suppression,
  deterministic bounds/order, istniejący indeks bez nowej migracji i zero
  state mutation/supersession/revocation/shared-promotion authority.
  [Architektura](../architecture/ACTIVE_PATCH_RETRIEVAL_CROSS_MODEL_REUSE_1A.md),
  [ADR-038](../adr/ADR-038-active-patch-retrieval-cross-model-reuse.md),
  [runbook](../operations/STEP_31_ACTIVE_PATCH_RETRIEVAL_VALIDATION_1A.md),
  [validation evidence](../evidence/personal-memory/step31-active-patch-retrieval-validation.json),
  [rekord zamknięcia](../audits/STEP_31_ACTIVE_PATCH_RETRIEVAL_CROSS_MODEL_REUSE_CLOSURE_1A.md).

- [x] **Step 32 — Supersession, Revocation, Export, Deletion and Shared Promotion 1A**
  Konflikt z nowszą wiedzą, stale marking, supersession, revocation, user export/delete oraz osobny, de-identyfikowany personal-to-shared review flow.
  `Step 32: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje immutable/hash-bound supersession i revocation receipts,
  zachowanie historycznej treści, bieżące i historyczne Step 31 eligibility,
  deterministyczny owner export, logiczne Step 27 delete/tombstone, osobną
  zgodę właściciela na shared review, deterministyczną de-identyfikację,
  wyłącznie review-required `SHARED_PROMOTION_PROPOSED`, migration 0015,
  RLS/FORCE RLS i brak canonical-evidence/source-publication authority.
  [Architektura](../architecture/PERSONAL_MEMORY_SUPERSESSION_REVOCATION_EXPORT_DELETE_SHARED_PROMOTION_1A.md),
  [ADR-039](../adr/ADR-039-personal-memory-lifecycle-shared-promotion-boundary.md),
  [runbook](../operations/STEP_32_PERSONAL_MEMORY_LIFECYCLE_VALIDATION_1A.md),
  [validation evidence](../evidence/personal-memory/step32-personal-memory-lifecycle-validation.json),
  [rekord zamknięcia](../audits/STEP_32_PERSONAL_MEMORY_LIFECYCLE_CLOSURE_1A.md).

### Gate fazy 7

- użytkownik ma pusty Personal Memory HAT;
- system wykrywa błąd i proponuje patch;
- użytkownik zatwierdza;
- commit helper aktywuje patch;
- kolejny model korzysta z patcha;
- inny użytkownik go nie widzi;
- nowsze canonical evidence może go unieważnić;
- patch nie przechodzi automatycznie do shared HAT.

---

# FAZA 8 — AUDYT, UI I BEZPIECZEŃSTWO

## Cel fazy

Pokazać cały Kernel człowiekowi, zapewnić trwały audyt, izolację credentials i kontrolowane zachowanie awaryjne.

- [x] **Step 33 — Audit Ledger, Hash Chain and Idempotent Export 1A**
  Typed append-only events, sequence, previous hash, replay keys, bounded
  private metadata, deterministic gap/tamper detection and redacted audit
  export with range proof.
  `Step 33: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje `AuditEventEnvelope`, zamknięte typy zdarzeń i aktorów,
  jawny genesis, owner-partitioned SHA-256 hash chain, serializable O(1) chain
  head, idempotent replay i concurrent append, migration 0016, RLS/FORCE RLS,
  read-only verifier, pełną macierz tamper detection oraz ograniczony,
  proof-carrying i redacted owner export bez business authority.
  [Architektura](../architecture/AUDIT_LEDGER_HASH_CHAIN_AUDIT_EXPORT_1A.md),
  [ADR-040](../adr/ADR-040-append-only-audit-ledger-hash-chain.md),
  [runbook](../operations/STEP_33_AUDIT_LEDGER_VALIDATION_1A.md),
  [validation evidence](../evidence/audit/step33-audit-ledger-validation.json),
  [rekord zamknięcia](../audits/STEP_33_AUDIT_LEDGER_HASH_CHAIN_CLOSURE_1A.md).

- [x] **Step 34 — Human Review Workspace and Draft Comparison UI 1A**
  Query, route, policy, evidence, Draft V1, Correction Packet, Draft V2, claim
  verdicts, patch proposal, approval i committed state.
  `Step 34: COMPLETE AND PUSHED at actual closure commit`.
  Zamknięcie obejmuje zamknięte typy review case dla Step 26 i Step 32,
  deterministyczną kolejkę i claim, least-privileged reviewer authorization,
  minimal-disclosure detail projection, weryfikację Step 33 audit chain,
  case-specific human decisions, typed business handoff, ochronę replay/TOCTOU,
  migration 0017 oraz RLS/FORCE RLS bez model, Critic, publication ani external
  execution authority.
  [Architektura](../architecture/HUMAN_REVIEW_WORKSPACE_1A.md),
  [ADR-041](../adr/ADR-041-human-review-workspace.md),
  [runbook](../operations/STEP_34_HUMAN_REVIEW_VALIDATION_1A.md),
  [validation evidence](../evidence/review/step34-human-review-workspace-validation.json),
  [rekord zamknięcia](../audits/STEP_34_HUMAN_REVIEW_WORKSPACE_CLOSURE_1A.md).

- [ ] **Step 35 — Personal Memory HAT Management UI 1A**
  Sloty, quota, model bindings, active patches, conflicts, archive, revoke, export, delete request i private/shared distinction.

- [ ] **Step 36 — Credential Separation and Commit Authority Hardening 1A**
  Kernel bez commit credentials, Model Adapter bez DB credentials, Approval/Commit Helper z ograniczoną rolą, negative capability tests.

- [ ] **Step 37 — Failure Injection and Recovery Matrix 1A**
  CockroachDB down, S3 down, USB missing/read-only, model failure, vector index stale, conflicting evidence, failed approval, interrupted transaction i recovery/resume.

### Gate fazy 8

- użytkownik widzi pełen proces;
- prywatna treść nie wycieka do wspólnego audytu;
- model i HAT nie mają commit authority;
- wszystkie główne awarie mają jawny fail/degraded state;
- po restarcie stan można wznowić bez duplikacji.

---

# FAZA 9 — INTEGRACJA, WYDAJNOŚĆ I RELEASE

## Cel fazy

Zamknąć działający Knowledge Kernel jako pełny system AIOA, a nie pojedynczy przykład prawny.

- [ ] **Step 38 — German Law HAT Full End-to-End Integration 1A**
  Pełny przebieg na dowolnych pytaniach wybranych później przez użytkownika: routing, temporal retrieval, correction, approval, personal reuse i audit.

- [ ] **Step 39 — AOIA Critic Prompt Loop Production Bridge 1A**
  Most z istniejącym Critic Loop; tylko proposal input, brak authority escalation, hash-bound correction events i wspólne run references.

- [ ] **Step 40 — Resource and Deployment Optimization for 4 GB Hardware 1A**
  Lekki local Kernel/UI, zdalny CockroachDB/model/embedding tam, gdzie trzeba, precomputed assets, limity pamięci, cache, startup preflight i honest offline/degraded mode.

- [ ] **Step 41 — Full Security and Regression Suite 1A**
  Tenant attacks, prompt injection, poisoned sources, SQL injection, path traversal, approval replay, stale evidence, malformed packets, cross-HAT leakage i rollback.

- [ ] **Step 42 — Release Candidate Freeze, Backup and Restore 1A**
  Pin versions, schema freeze, backup/restore drill, deployment scripts, configuration examples, operator runbook, no-secret scan i RC tag/commit.

- [ ] **Step 43 — Documentation, Demo Automation and Submission Package 1A**
  Architecture, ADR-y, API/contracts, HAT SDK guide, Personal Memory HAT guide, test evidence, capability proof, repeatable demo script i końcowy materiał konkursowy.

### Gate końcowy

Projekt jest gotowy, gdy:

- Kernel Core jest neutralny domenowo;
- German Law HAT działa jako pierwszy pełny klient, nie jako hardcoded demo;
- nowe HAT-y można dodać przez SDK;
- każdy użytkownik ma prywatne Personal Memory HAT-y;
- zweryfikowana korekta może zostać zatwierdzona i później użyta przez dowolny model;
- żaden model ani HAT nie ma authority do approval, commit lub external action;
- CockroachDB, S3 i external volume mają jednoznaczne role;
- cały `STORE → RETRIEVE → CORRECT → VERIFY → APPROVE → REMEMBER` działa;
- testy awarii, izolacji i bezpieczeństwa przechodzą;
- demo nie używa fikcyjnego prawa ani hardcodowanego pytania.

---

# ŚCIEŻKA KRYTYCZNA — NAJWAŻNIEJSZE STEPY

Do działania pierwszej pełnej wersji bez elementów dodatkowych konieczne są:

```text
0B
→ 1
→ 2
→ 3
→ 4
→ 5
→ 6
→ 7
→ 9
→ 10
→ 11
→ 12
→ 13
→ 14
→ 15
→ 16
→ 17
→ 18
→ 19
→ 20
→ 21
→ 22
→ 23
→ 24
→ 25
→ 26
→ 27
→ 29
→ 30
→ 31
→ 33
→ 34
→ 36
→ 37
→ 38
→ 42
```

Pozostałe stepy wzmacniają kompletność, UX, zarządzanie i rozwój produkcyjny, ale nie mogą podważać ścieżki krytycznej.

---

# ZASADA RAPORTOWANIA PO KAŻDYM STEPIE

Każdy raport Codexa musi kończyć się sekcjami:

1. **Executive Verdict**
2. **Repository Guard**
3. **Starting State**
4. **Implementation**
5. **Files Changed**
6. **Tests and Validation**
7. **Safety and Authority Confirmation**
8. **Commit and Push**
9. **Final Repository State**
10. **Exact Next Step**

Dopuszczalne werdykty:

```text
COMPLETE AND PUSHED
BLOCKED SAFELY — NO COMMIT
FAILED VALIDATION — NOT COMMITTED
```

Nie akceptujemy raportu typu „prawie gotowe”, nieczystego worktree, lokalnego commita bez pushu ani przejścia do następnego stepu bez zamknięcia poprzedniego.
