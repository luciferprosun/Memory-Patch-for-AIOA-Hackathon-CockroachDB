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

## Zweryfikowany checkpoint przy przyjęciu roadmapy

- `Step 0A`: `COMPLETE AND PUSHED` — commit `b3d555ec230a894b541e3570347fcf086511df2a`.
- `Step 0B`: `COMPLETE AND PUSHED` — commit `870145c78d9e6bf02e318bdca2327eb808f381b7`.
- `Step 0C`: przyjęty jako ukończony baseline architektoniczny z roadmapy źródłowej.
- `Step 1`: `COMPLETE AND PUSHED` — commit `3f6d341bc3ceb964a2b25d4913a0695595dbd7d0`.
- `Step 2`: `COMPLETE AND PUSHED` — commit `807b459b3d0270bd84c5590df6e7abf3e4f9842b`.
- Dokładny następny krok: `Step 3 — CockroachDB v26.2 Capability Spike and Version Pin 1A`.

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

- [ ] **Step 3 — CockroachDB v26.2 Capability Spike and Version Pin 1A**
  Zweryfikować na realnym środowisku: `VECTOR`, vector index, full-text search, RLS, `FORCE ROW LEVEL SECURITY`, Row-Level TTL, changefeed, partial unique indexes, `AS OF SYSTEM TIME`, transakcje `SERIALIZABLE` i błędy `40001`.
  Wynik: macierz PASS/FAIL/DEFER i przypięta wersja klastra.

- [ ] **Step 4 — CockroachDB Logical Schema and Migration Foundation 1A**
  Tabele dla tenantów, użytkowników, HAT-ów, źródeł, snapshotów, wersji, chunków, kernel runs, routing decisions, evidence bundles, packets, drafts, verdicts, personal memory, patch proposals, approvals, committed patches i audit events.

- [ ] **Step 5 — Tenant Roles, Session Context and Row-Level Security 1A**
  Role SQL, tenant context, RLS, `FORCE ROW LEVEL SECURITY`, brak `BYPASSRLS`, negatywne testy cross-tenant i cross-user.

- [ ] **Step 6 — Persistence Adapters, Idempotency and Transaction Retry Foundation 1A**
  Adapter CockroachDB, krótkie transakcje, idempotency keys, obsługa `40001`, immutable evidence IDs, resume states i brak transakcji otwartych podczas wywołań modeli lub AWS.

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

- [ ] **Step 7 — S3 Snapshot Authority and Object Lock Adapter 1A**
  Versioning, Object Lock Governance dla development/hackathon, `s3_version_id`, SHA-256, content length, retention metadata, prywatne i globalne klasy snapshotów, bez twierdzenia o cross-system ACID.

- [ ] **Step 8 — External Volume Runtime Adapter and Fail-Closed Policy 1A**
  Integracja przygotowanego `LSC_DATA`: identity check, marker, read/write, free space, operation-specific failure policy i zakaz fallbacku dużych zapisów na dysk systemowy.

- [ ] **Step 9 — Source Registry, Provenance and Publication States 1A**
  Rejestr źródeł, authority level, licencja/status, jurysdykcje lub inne scope dimensions, parser version, transformation version, hash lineage i publication eligibility.

- [ ] **Step 10 — Idempotent S3–CockroachDB Ingestion Saga 1A**
  Stany: `REGISTERED → ACQUIRED_LOCAL → HASH_VERIFIED → SNAPSHOT_UPLOAD_PENDING → SNAPSHOT_UPLOADED → SNAPSHOT_LOCK_VERIFIED → PARSED → VALIDATED → PUBLISHED`, z retry, reconciliation, quarantine i cleanup orphanów.

- [ ] **Step 11 — Generic Parsing, Normalization and Chunking Pipeline 1A**
  Neutralne parser contracts, dokumenty, sekcje, char ranges, chunk IDs, deterministic chunking, prompt-injection flags, quarantine i testowe źródła syntetyczne.

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

- [ ] **Step 12 — HAT Registry, Manifest Validation and Runtime Boundary 1A**
  Rejestr HAT-ów, kompatybilność wersji, scope dimensions, required capabilities, source policies, no-execution enforcement i bezpieczne ładowanie tylko zatwierdzonych manifestów.

- [ ] **Step 13 — German Law HAT Package and Source Authority Policy 1A**
  Manifest German Law HAT, domeny, języki, jurysdykcje, hierarchia źródeł urzędowych i wtórnych, polityka temporalna, parser adapters i contract tests.
  Bez wybierania końcowego pytania testowego.

- [ ] **Step 14 — German Law Corpus Inventory, Deduplication and Source Registration 1A**
  Pełna inwentaryzacja posiadanej biblioteki, hashe, duplikaty, near-duplicates, źródła urzędowe, prywatność, licensing, quarantine i mapping do source registry.

- [ ] **Step 15 — German Law Temporal and Jurisdictional Normalization 1A**
  `published_at`, `effective_from`, `effective_to`, `retrieved_at`, `ingested_at`, `superseded_at`, `verified_at`, zakres jurysdykcji, wersje i konflikty.

- [ ] **Step 16 — German Law HAT Publication and Corpus Verification 1A**
  Publikacja zweryfikowanych wersji, provenance chain, parser/chunk coverage, walidacja temporalna i raport braków.
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

- [ ] **Step 17 — Axis A Router, Axis B Policy Gate and Evidence Status 1A**
  `PASS_THROUGH / HAT_ASSIST / HAT_ENFORCE / AMBIGUOUS`, niezależne `ALLOW / DENY_ACTION / REQUIRE_CONFIRMATION`, osobne evidence status i answer status.

- [ ] **Step 18 — Exact and Full-Text Retrieval Baseline 1A**
  Exact identifiers, statute/section lookup, German full-text, keyword search, source authority, tenant/HAT isolation i twarde scope filters.

- [ ] **Step 19 — Embedding Generation and Vector Retrieval Foundation 1A**
  Przypięty model embeddingowy, wersja i dimension, batch generation, cache na external volume, rekordy w CockroachDB, capability proof i resource limits.

- [ ] **Step 20 — Hybrid Retrieval, Evidence Bundle and Deterministic Ranking 1A**
  Łączenie exact, full-text i vector candidates, hard filters przed finalnym dopuszczeniem, deterministic ordering, diversity, context budget i zamrożony Evidence Bundle.

- [ ] **Step 21 — Temporal Resolver, Conflict Detection and Freshness Policy 1A**
  Historyczne i bieżące pytania, future-effective, repealed/superseded, conflicting evidence, stale sources, insufficient evidence i completeness fallback.

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

- [ ] **Step 22 — Provider-Neutral Model Adapter and Draft V1 1A**
  Oryginalne pytanie bez correction evidence, model metadata, timeout/retry, Draft V1 hash i brak dostępu modelu do bazy lub authority credentials.

- [ ] **Step 23 — Claim Extraction and Evidence Binding 1A**
  Claims z Draft V1, span references, claim IDs, evidence mappings, `SUPPORTED / REFUTED / UNVERIFIED` candidates i zamrożenie tego, co faktycznie trafi do packetu.

- [ ] **Step 24 — Correction Packet Construction and Integrity 1A**
  Canonical JSON, ordered evidence, required corrections, prohibited claims, conflicts, citations, policy, scope, packet hash/HMAC boundary i deterministic replay.

- [ ] **Step 25 — Draft V2 Generation and Layered Claim Verifier 1A**
  Draft V1 + packet → Draft V2; schema checks, deterministic fact/date/source checks, semantic claim verification i brak automatycznego zaufania do modelu-verifiera.

- [ ] **Step 26 — Verified Answer Assembly and Fail-Closed Output 1A**
  Zwrócenie tylko zweryfikowanej odpowiedzi; dla `HAT_ENFORCE` brak fallbacku do znanego błędnego Draft V1; retry raz, potem human review lub bounded failure.

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

- [ ] **Step 27 — Personal Memory HAT Persistence, Quotas and Model Bindings 1A**
  Puste sloty, konfiguracja, aktywacja, quota policy, model bindings, owner isolation, archive/export/delete contracts i brak zależności pamięci od Gemmy.

- [ ] **Step 28 — Knowledge Hub and Critic Prompt Loop Correction Candidate Bridge 1A**
  Critic Loop i Kernel mogą zgłaszać Correction Candidate Envelope, ale nie aprobować, commitować ani aktywować pamięci.

- [ ] **Step 29 — Personal Memory Patch Proposal and Evidence Validation 1A**
  `DETECTED → PROPOSED → EVIDENCE_BOUND → VALIDATED → AWAITING_APPROVAL`, dedup, conflict check, stale evidence check i owner scope.

- [ ] **Step 30 — User Approval, Commit Helper and Activation 1A**
  Oddzielne approval i technical commit, revalidation hashy, dedicated credentials, replay protection, `APPROVED → COMMITTED → ACTIVE`.

- [ ] **Step 31 — Active Patch Retrieval and Cross-Model Reuse 1A**
  Użycie aktywnego patcha przy późniejszym pytaniu, scope/temporal checks, ta sama pamięć dla Gemmy lub innego modelu, bez traktowania patcha jako canonical evidence.

- [ ] **Step 32 — Supersession, Revocation, Export, Deletion and Shared Promotion 1A**
  Konflikt z nowszą wiedzą, stale marking, supersession, revocation, user export/delete oraz osobny, de-identyfikowany personal-to-shared review flow.

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

- [ ] **Step 33 — Audit Ledger, Hash Chain and Idempotent Export 1A**
  Append-oriented events, sequence, previous hash, dedup keys, bounded private metadata, at-least-once changefeed handling, gap detection i S3 audit mirror.

- [ ] **Step 34 — Human Review Workspace and Draft Comparison UI 1A**
  Query, route, policy, evidence, Draft V1, Correction Packet, Draft V2, claim verdicts, patch proposal, approval i committed state.

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
