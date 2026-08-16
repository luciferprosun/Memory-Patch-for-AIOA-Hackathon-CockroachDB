# AIOA Memory Patch - Jury Preparation and Known Limitations

## Working jury deployment

- Jury app:
  `https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws`
- Direct demo:
  `https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/memory`
- Login:
  `https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/memory/login`
- Judge user: `demo-judge`
- Password: supplied separately through the private submission testing-
  credentials field; it is not stored in Git or this document.
- Frozen AOIA-Core source:
  [`360e900b66396a19fc09cccf69641cc015691ad8`](https://github.com/luciferprosun/AOIA-Core/commit/360e900b66396a19fc09cccf69641cc015691ad8)
- Immutable AOIA-Core release:
  [`hackathon-jury-final-2026-08-15`](https://github.com/luciferprosun/AOIA-Core/releases/tag/hackathon-jury-final-2026-08-15)

The public browser smoke passed once with Cognito login, 36 CockroachDB
records, 31 current/applicable records, `CORRECTION_REQUIRED`, two Gemma calls,
no Repair call, verified Final delivery, and logout. The deployment is hosted
on AWS and does not depend on the developer computer being powered on.

## Two-minute preparation before judging

1. Open
   [`/health/live`](https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/health/live)
   and confirm HTTP 200 with `status: LIVE`.
2. Open
   [`/health/ready`](https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/health/ready)
   and confirm HTTP 200 with:
   - `status: READY`;
   - `source_sha: 360e900b66396a19fc09cccf69641cc015691ad8`;
   - `knowledge_records: 36`;
   - `applicable_records: 31`.
3. Open the jury app in a private browser window. The base URL redirects to the
   bounded Cognito login.
4. Sign in as `demo-judge` using the password supplied privately with the
   submission. Keep that authenticated window open.
5. Do not edit the prompt, model, data, Cognito configuration, AWS service, or
   application before the presentation.

## Exact Memory Patch demo flow

1. Leave **Critical Prompt Loop OFF**.
2. Turn **German Law Knowledge ON**.
3. Enable the recording demo preset if it is not already enabled.
4. Use the exact prefilled NachwG question from the recording.
5. Start one run and wait for the stage indicator to finish.

Expected trace:

```text
Gemma Primary -> Primary received -> CockroachDB retrieval -> temporal audit
-> HAT verdict -> Gemma Final -> deterministic Final validation
-> verified browser response
```

The successful public trace was:

```text
primary -> primary-received -> retrieving -> temporal-audit -> verdict
-> finalizing -> validating-final -> final-verified -> completed
```

Expected result:

- classification `CORRECTION_REQUIRED`;
- 36 retrieved records and 31 current/applicable records;
- two provider calls: Primary and Final;
- Repair `0` under normal operation;
- verified Final visible in the browser.

## How to present the earlier Critical Prompt Loop module

Explain it before starting the Memory Patch run:

> The previous module, Critical Prompt Loop, repeatedly audited a model answer
> from multiple reasoning perspectives. This demo adds epistemic control over
> time-sensitive external knowledge. We keep the Critical Prompt Loop off here
> so the audience can see the separate Memory Patch path clearly.

If you demonstrate Critical Prompt Loop itself, leave German Law Knowledge
OFF. The frozen build intentionally prevents both modules from being enabled
together.

## HAT and provider startup behavior

- HAT is not a continuously running background service. It begins after Gemma
  Primary returns and CockroachDB evidence has been retrieved. A pause at
  `primary` is usually provider latency, not a HAT crash.
- The service fails closed if the configured provider or exact Gemma model is
  unavailable. It does not silently switch models.
- Only one run can be active. Do not click Start again while the first run is
  queued or running.
- `GEMMA_PRIMARY_FAILED` means the first provider call failed.
- `COCKROACH_RETRIEVAL_FAILED` means the CockroachDB evidence stage failed.
- `HAT_AUDIT_FAILED` means the deterministic audit stopped safely.
- `GEMMA_FINAL_FAILED` means the corrected model-authored Final was not
  generated.
- `FINAL_RESPONSE_VERIFICATION_FAILED` means the model answer did not satisfy
  the frozen deterministic contract.
- The recording preset permits at most one Repair call, but the five recording
  acceptance runs and the public AWS smoke used Repair `0`.

## If the live jury run fails

1. Do not start a second run while the first is active.
2. Record the displayed failing stage and sanitized error code.
3. Check `/health/ready` once.
4. If readiness is not HTTP 200 or the count is not exactly 36/31, stop. Do not
   modify application code or legal data.
5. If readiness is green but Gemma timed out, wait 60 seconds. Use at most one
   operator-approved retry during the actual jury session.
6. Do not tune the prompt, switch the model, alter retries, or patch HAT live.
7. If retrieval, HAT, or Final verification fails again, use the completed
   video as the authoritative demonstration artifact.

## Integrity wording for the jury

Use this exact description:

> Gemma authors both the Primary and corrected Final. CockroachDB supplies the
> versioned evidence, HAT audits the actual Primary, and Python deterministically
> validates the Final contract before the browser receives it.

Do not claim that no local oracle exists. The frozen source contains
`audit.oracle` requirements that constrain and validate the structured Final;
the oracle is not the author of the legal answer.

## Known limitations

- The repository contains only the exact historical prompt files that were
  available for preservation. Most implementation-session prompts were not
  retained and have not been reconstructed.
- Provider latency can cause visible pauses at Primary or Final.
- The local disposable CockroachDB pgwire instance remained unreliable after
  reboot. The public AWS build uses the verified hosted CockroachDB Cloud path.
- The public ECR scan had no critical or high findings, but retained five
  medium and one low finding for this frozen hackathon build.
- CloudWatch application logs retain for seven days and use AWS service-side
  encryption rather than a customer-managed KMS key.
- The bounded demo does not claim production HA, DR, or an SLA.
- The app is frozen to match the video. Do not polish the UI, tune prompts,
  change HAT, change the schema, or edit legal data before judging.

## Polish operator checklist

- Sprawdź health i dokładne wartości SHA oraz 36/31.
- Otwórz zwykły adres jury w oknie prywatnym i zaloguj `demo-judge`.
- Dla Memory Patch: Critical Prompt Loop OFF, German Law Knowledge ON.
- Użyj dokładnie gotowego pytania z nagrania i kliknij Start tylko raz.
- Pauza na `primary` lub `finalizing` może oznaczać oczekiwanie na Gemmę.
- Nie zmieniaj promptu, modelu, HAT, bazy ani AWS przed oceną.

FROZEN. VIDEO IS DONE. DO NOT CHANGE THE APPLICATION BEFORE JUDGING.
