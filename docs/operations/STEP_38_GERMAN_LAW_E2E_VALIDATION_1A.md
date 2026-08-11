# Step 38 German Law End-to-End Validation 1A

## Purpose and current status

This runbook validates the Step 38 integration from exact Step 37 closure
`9888070ab171fd057b17ab3057b3cf868cf704d2`.

The approved OpenRouter/Kimi closure run has been observed. It returned
`PASS_LIVE_COHERENT_LINEAGE` with `closure_eligible=true`, selected
`primary-entry-into-force`, completed the coherent one-runtime, one-database
lineage and exact cleanup, and passed the Step 39 boundary scan with zero
unexpected hits. Its sanitized validation digest is
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
The closure commit is `NOT CREATED`, push is `NOT PERFORMED`, and Step 39 is
`NOT STARTED`.

Never run fault or database phases against production/shared infrastructure.
Do not mutate AWS/S3 or the source corpus, rotate a production secret,
auto-approve a real owner action, expose a credential, or enable an external
action.

## Repository preflight

From the repository root inspect:

```text
git status -sb
git branch --show-current
git rev-parse HEAD
git remote -v
git diff --name-only
git rev-list --left-right --count main...origin/main
```

For formal validation require:

- branch `main`, expected remote, and no active Git operation;
- Step 37 closure
  `9888070ab171fd057b17ab3057b3cf868cf704d2` reachable from `origin/main`;
- Step 37 complete, Step 38 not yet closed, Step 39 not started;
- only intended Step 38 changes; and
- complete repository instructions and roadmap read.

Do not update roadmap or `AGENTS.md`, commit, or push while a required live
gate is blocked.

## Exact fixture preflight

The live preflight reads, but does not republish, the existing Step 14-16
artifacts and exact source JSONL. Resolve machine paths through the existing
external configuration or explicit runner arguments.

| Input | Required identity |
| --- | --- |
| HAT | `german-law` / `1.0.0` / `ab6ff572596993d63fdfd148207fcb4593f2672583f9d8dbedcc8f7e0f246109` |
| HAT scope | `german-law-global-1a` |
| source | `de-federal-gii-bjnr1330a0023` / `BJNR1330A0023` |
| version | `legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95` |
| provision I | `323c88960cc5eeca3e2d4b6c3c34630947f85ec82c75e1e398492a319bd13147` |
| provision II | `6a12a5f19d7a4b61d71be5c5583d0a3a41b3111fcf00803892200fc42260d99e` |
| provision III | `fb4de8c3c966f34ccf469bfb56ad31bf9e9681775586fa058465a216f14439a1` |
| Step 14 manifest | `ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89` |
| Step 15 manifest | `7094358f7c9bb6acf62160484a017074da70361c73e4e5bbd7623f700414b125` |
| Step 16 manifest | `6871562b5b17d632c0e15169fefe7186f3fc7d7b5eb59f4140c367bf2c8a37e8` |

Exact provision II must be:

```text
Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten vor.
```

Exact provision III must be:

```text
Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum selben Gegenstand sind nicht mehr anzuwenden.
```

Reject path escape, symlinks where the repository validators forbid them,
missing files, changed manifest/source/version identities, altered UTF-8
bytes, or a mismatched publication state.

## Golden fixture gate

`tests/fixtures/step38_german_law_cases.json` must load as canonical typed
data and contain exactly the expected case kinds:

- primary provision III entry-date completion and wrong-date correction;
- backup `backup-special-case-reservation` over exact provision II;
- supported provision III entry-date answer;
- synthetic historical-unavailable case;
- synthetic equal-rank conflict case; and
- synthetic non-German-law route negative.

The primary question is exactly `Vervollständige den Satz zur BMJErnAnO:
„Diese Anordnung tritt am [Datum] in Kraft.“` The deterministic test double
uses `Diese Anordnung tritt am 1. Januar 2025 in Kraft.` The accepted
correction is the exact source sentence `Diese Anordnung tritt am 1. Januar
2024 in Kraft.` Do not present that synthetic Draft V1 as a real observation
or make an actual provider wrong. If the unchanged real primary answer has no
correctable defect, use the declared provision II backup.

The backup question must be exactly:

```text
Vervollständige nach Abschnitt II der BMJErnAnO den Satz, indem du „nicht“ einsetzt oder die Lücke leer lässt: „Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter I. genannten Beamtinnen und Beamten ___ vor.“
```

Its correction condition must be
`SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED`. The response-shape gate
accepts only the complete exact provision II sentence or the same complete
sentence with `nicht` immediately before `vor`. The canonical sentence is a
correct, no-defect response. The exact `nicht vor` sentence is the only
selectable backup defect and must bind a full-sentence `REFUTES` link. A
placeholder, one-word response, paraphrase, quotation, heading, prefix,
suffix, explanation, or additional sentence must fail closed as
`STEP38_BACKUP_RESPONSE_SHAPE_INVALID`.

## Focused offline checks

Run without a database or hosted provider call:

```text
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step38_german_law_e2e -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step38_corrected_claim_bridge -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest \
    tests.test_step23_claim_evidence_binding \
    tests.test_step24_correction_packet \
    tests.test_step25_draft_v2_layered_verifier \
    tests.test_step26_verified_answer_output \
    tests.test_step29_personal_memory_patch_proposal \
    tests.test_step31_active_patch_retrieval -q

python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
npm run check:assets
```

The focused checks must prove:

- exact I/II/III hash projection and rejection of changed bytes;
- narrow legal-reference splitting: only `I`/`II`/`III` plus horizontal
  whitespace and lowercase continuation remains one span; newline, `IV+`, and
  uppercase continuation remain boundaries;
- nominal coordination around capitalized tokens remains non-clausal for
  `und`/`oder`/`sowie`/`and`/`or`, while genuine clause coordination remains
  `COMPOUND`;
- exact provision II correct/negated full-sentence Step 23 outcomes are
  `SUPPORTED` and `REFUTED`, respectively;
- typed provision III date projection with no pre-existing metadata or model
  inference claim;
- Draft V1 evidence blindness;
- citation-to-original-link-to-bundle-item-to-exact-span reconstruction;
- `DraftV2TargetProjection` derived solely from the verified packet/context,
  canonical reconstruction before any provider call, exact generated-output
  whitelisting, and tamper/detachment negatives;
- Draft V2-only input augmentation and
  `EvidenceBoundProviderInputReceipt` binding;
- a full reconstructible corrected-evidence proof that Step 25 independently
  revalidates, including tamper and detached-proof negatives;
- original Step 23 `REFUTES` lineage preserved through the versioned Step 29
  reference;
- legacy Step 25/29 hashes and JSON unchanged when the new path is absent;
- Verified Answer gating, owner isolation, cross-model reuse, canonical
  conflict suppression, audit, review, UI, and recovery component contracts.

Run the deterministic orchestrator lane with its actual checked-in interface:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_step38_german_law_e2e_validation.py --offline
```

Optional explicit path arguments are:

```text
--external-env PATH
--cockroach-binary PATH
--step14-bundle-root PATH
--step15-bundle-root PATH
--step16-bundle-root PATH
--source-root PATH
```

An offline success must be exactly classified
`PASS_OFFLINE_NOT_CLOSURE` with `closure_eligible=false`. It is a deterministic
contract proof, not a real-model or coherent database E2E.

## Approved-live attempt

The runner's default mode is the bounded live coherent-lineage mode. It uses
only the approved Step 38 identity:

```text
provider_id=openrouter
adapter_version=openrouter-chat-completions-step38-1a
model_id=moonshotai/kimi-k2
model_declared_version=moonshotai/kimi-k2
endpoint_class=openrouter-public-chat-completions-v1
api_origin=https://openrouter.ai
chat_completions_path=/api/v1/chat/completions
config_digest=52e163ebef09076c135bc7c0783917bc1515666456253a2a62b4a8822630e15e
immutable_model_revision=false
```

Take the provider identity digest from the sanitized controlled-validation
result; do not predict or hand-edit it in the runbook.

Inject the credential only through the repository's approved runtime secret
boundary. Never print it, put it on the command line, commit it, or expose it
to a child that does not need provider capability. No alternate model,
provider fallback, tools, web, function calls, or code execution is allowed.
The runtime consumes `OPENROUTER_API_KEY` through the purpose-bound credential
loader, removes it from the ambient environment before child work, and passes
it only to the minimal provider re-exec environment.

Command shape:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_step38_german_law_e2e_validation.py \
  --external-env .local/external-data.env
```

The runner first verifies exact I/II/III source bytes and the fixture-bound
temporal receipt. It then attempts the primary and, only when the declared
primary defect is absent, the backup through the approved provider policy.
For the backup, verify the exact complete-sentence shape before Step 23. A
correct canonical completion must stop as a truthful no-defect observation;
it must never be forced into a correction. Only the exact full sentence with
`nicht vor` may continue through `REFUTES`, Correction Packet, Draft V2, and
verification. Missing dedicated credential, exhausted retry, HTTP `429`,
invalid backup shape, detached target projection, non-exact Draft V2 output,
failed layered verification, or absent Verified Answer must stop safely.

The final live observation returned `PASS_LIVE_COHERENT_LINEAGE`, selected
`primary-entry-into-force`, and is bound by validation digest
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
No raw response body is retained, no fake result is relabeled as real, and
closure is not inferred from configuration alone.

## Coherent live topology and closure gate

The live runner owns one disposable CockroachDB v26.2.4 runtime and one
database. In that database it executes real Step 18-21 primary retrieval,
captures the real Step 22 Draft V1 and Steps 23-26 verified lineage outside any
retried transaction, runs a natural later related retrieval, and executes the
Steps 27-35 candidate/proposal/approval/commit/activation/reuse/audit/review/UI
lineage plus the representative Step 37 activation acknowledgement-loss spot
check.

Primary and later retrieval proofs, the verified upstream lineage, Personal
Memory scenario, audit chain, review case, UI projections, and recovery
observation must bind the same runtime and database digests. Any detached
identity, incomplete cleanup, authority violation, duplicate semantic side
effect, or unexpected Step 39 production-bridge hit makes
`closure_eligible=false`. Separate component JSON outputs may never be
stitched into a full E2E claim.

Before the Draft V2 call, inspect that the supplied
`DraftV2TargetProjection` equals a canonical rebuild from the typed Correction
Packet and `EvidenceBoundCorrectionContext`. It must render only canonical,
unique, exact `REFUTES` excerpts as atomic citation-bearing lines and record
omission-safe corrections without fabricating facts. The provider response
must equal the projection's expected output byte for byte; headings, prefixes,
paraphrases, extra text, and prohibited claims fail closed. The input receipt
and before/after trace must bind the projection hash.

When the backup is selected, also inspect that provision II remained one
Unicode-exact span. `unter I. genannten` must not split at the Roman legal
reference, and the nominal pairs `Ernennung und Entlassung` and `Beamtinnen
und Beamten` must not make the claim compound. The correct full sentence must
have one exact `SUPPORTS` link and no RequiredCorrection. The `nicht vor`
counterpart must have one exact `REFUTES` link over the full canonical
provision II span and one renderable correction target.

## Corrected-evidence inspection

For the primary correction, inspect that `CorrectedEvidenceProof` is
reconstructible, not an opaque assertion. It must bind and allow Step 25 to
revalidate:

- version, request hash, and Correction Packet hash;
- target Draft V2 claim ID/hash/citation-stripped text SHA-256;
- satisfied correction IDs and packet citation ID/hash;
- `EvidenceBoundCorrectionContext` hash as audit lineage;
- the full original Step 23 `ClaimEvidenceLink`, including its `REFUTES`
  relation, offsets, Step 20 item identity, source/version/chunk/content
  identity, and exact span SHA-256; and
- proof and signal hashes derived from the complete typed object.

The proof stores no extra raw evidence text. Step 25 must independently verify
the request/target binding, nested link hash and relation, packet
citation/link identities, every satisfied correction's exact `REFUTES`
replacement fact, and citation-stripped target SHA-256 equality with the link
span SHA-256. The context hash is bound audit lineage and grants no authority
alone. Changing any link, offset, citation, target claim, correction, digest,
or proof field must fail. Merely returning `SUPPORTS` from an object
implementing a protocol is not sufficient.

## Personal Memory, audit, review, UI, and recovery

A coherent closure run must use synthetic Tenant A/User A, Tenant A/User B,
and Tenant B/User C and exercise real Step 28-35 service boundaries. Required
state order is:

```text
DETECTED -> PROPOSED -> EVIDENCE_BOUND -> VALIDATED -> AWAITING_APPROVAL
         -> APPROVED -> COMMITTED -> ACTIVE
```

The Step 29 `PersonalMemoryCorrectedClaimEvidenceReference` must retain the
original refuting link and bind the separately verified Draft V2 target.
Approval must be an explicit owner action; Commit Helper cannot approve.
Another owner/tenant and a disallowed model must not retrieve or mutate the
patch. Canonical conflict must suppress memory.

The same coherent run must verify the Step 33 audit chain and sanitized
export, produce a Step 34 review-required fallback without publication,
surface the owner-scoped Step 35 view model, and run a representative Step 37
acknowledgement-loss recovery spot check with zero duplicates or authority
violations.

## Evidence, regression, and static scans

The live/coherent gates passed with validation digest
`b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042`.
The closer must freeze the exact sanitized result at
`docs/evidence/e2e/step38-german-law-full-e2e-validation.json` and derive the
non-authoritative compact trace at
`docs/evidence/e2e/step38-german-law-demo-trace.json`. The artifacts may
contain only safe identities, hashes, classifications, counts, reason codes,
cleanup, and canonical digests. They must contain no credential, raw provider
response, opaque session, private Personal Memory text, machine path, or
unbounded corpus extract.

After a coherent run passes, execute the full repository regression, contract
validator, compileall, frontend checks, focused Steps 13-37 regressions, and
secret scans required by the Step 38 prompt. Review every static hit rather
than treating the regex exit status alone as proof.

## Cleanup and verdict

Gracefully stop only exact owned disposable processes and remove only their
validated temporary stores, databases, roles, ports, sessions, and fixture
files. Cleanup failure is validation failure. Never use broad recursive
deletion or kill an unverified process.

The observed worktree validation verdict is:

```text
PASS_LIVE_COHERENT_LINEAGE
CLOSURE_ELIGIBLE=true
PRIMARY_CASE_USED=primary-entry-into-force
COHERENT_RUNTIME=PASS
STEP39_BOUNDARY=PASS; UNEXPECTED_HITS=0
CLEANUP=COMPLETE
GIT_CLOSURE_COMMIT=NOT CREATED
PUSH_RESULT=NOT PERFORMED
STEP39_STATUS=NOT STARTED
```

If any required future rerun is offline, detached, fails, or cannot reproduce
the coherent closure proof, it must still fail closed and must not authorize a
commit or push. The passing worktree result does not itself create Git
reachability, and it does not authorize Step 39 implementation.
