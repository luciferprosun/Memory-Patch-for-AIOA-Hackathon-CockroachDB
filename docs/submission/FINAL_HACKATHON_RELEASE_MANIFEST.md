# Memory Patch for AIOA - Final Hackathon Release Manifest

## 1. Purpose and repository boundary

This is the canonical post-Step43 jury addendum for the CockroachDB x AWS
Build with Agentic Memory Hackathon. It joins the historical Step43 evidence,
the later work completed within the hackathon period, and the exact integrated
runtime used for the recorded and publicly deployed jury demo.

Two public repositories have distinct roles:

- [Memory Patch for AIOA](https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB)
  is the hackathon component, CockroachDB implementation, architecture,
  validation, and evidence repository containing this manifest.
- [AOIA-Core commit `360e900b66396a19fc09cccf69641cc015691ad8`](https://github.com/luciferprosun/AOIA-Core/commit/360e900b66396a19fc09cccf69641cc015691ad8)
  is the exact integrated source used for the recorded and deployed demo. It is
  frozen by the
  [`hackathon-jury-final-2026-08-15`](https://github.com/luciferprosun/AOIA-Core/releases/tag/hackathon-jury-final-2026-08-15)
  release.

Commit `360e900b66396a19fc09cccf69641cc015691ad8` is not claimed to exist in
the Memory Patch repository.

## 2. Hackathon chronology and calculated Git range

- Official submission period: `2026-06-30T14:00:00Z` through
  `2026-08-18T21:00:00Z`.
- First Memory Patch repository commit:
  `04709a84f8cb8407a6fdf060210403f8e323133f`,
  `2026-07-24T08:59:40+02:00`.
- Historical Step43 cutoff:
  `b9dda5eba15aea41edeb8498c4fe524037bd0a07`,
  `2026-08-13T00:16:17+02:00`.
- Audited pre-closure head:
  `2c5ae71cf578e7eba9e40a104f25b9694e67caf5`,
  `2026-08-16T00:47:05+02:00`.
- Final hackathon commit range: the first repository commit above through the
  commit containing this manifest. The containing commit's immutable SHA is
  recorded by Git history and the final operator report; a Git commit cannot
  embed its own SHA without changing that SHA.

Counts were recalculated from Git at closure, not copied from the earlier
audit:

| Measure | At audited pre-closure head | After this closure commit |
| --- | ---: | ---: |
| Repository commits in the hackathon work history | 69 | 70 |
| Commits strictly after the Step43 cutoff | 17 | 18 |
| Files added under `docs/` after Step43 | 24 | 27 |

The three final additions under `docs/` are this manifest, the jury preparation
guide, and the architecture PNG. The root `LICENSE` is deliberately not counted
as a file under `docs/`.

## 3. Historical Step43 evidence boundary

The archive at
[`Raporty Hackathon CockroachDB/`](<../../Raporty Hackathon CockroachDB/README.md>)
ends at Step43 and remains a historical snapshot. Its manifest was not
regenerated to make later work appear earlier.

- Step43 snapshot paths present: **302/302 PASS**.
- Historical Git-backed blob hashes: **257/257 PASS**.
- Historical manifest bytes:
  `43286ca960ce95819946a76f668937bcc14213fd6aa012caf2bba2a047911684`.
- The manifest's sidecar verification remains **PASS**.

The five known current files below legitimately changed after their historical
pins. Each historical hash is still reproducible from its recorded Git commit.
`Historical snapshot != current final submission state` is an expected
provenance distinction, not an integrity failure.

| File | Historical commit | Historical SHA-256 | Current final SHA-256 |
| --- | --- | --- | --- |
| `README.md` | `b9dda5eba15aea41edeb8498c4fe524037bd0a07` | `6cec1b83e3b97a1be31e8c88754292d164e6f003ec3bef4c87071064e6689ba7` | `5e2139414ad78a3527b90016f0becf12df82482cfacb7856cc7ad9a2ec187954` |
| `docs/SUBMISSION_INDEX_1A.md` | `b9dda5eba15aea41edeb8498c4fe524037bd0a07` | `33bf51b26a41009f9814633b7133bca3b7ec7f25918622fea0fd574b5463abc7` | `8d374bdc225670d04e11b1de3f63617693ba712ff9387fea01923072900151dd` |
| `docs/adr/ADR-010-cockroachdb-v26-2-version-pin.md` | `3e8c499fbcb2bb905fce451a163f913030ecacce` | `266d5803d0ef9259ebdb643d2b23f3fac475ac4efa981d1051ee394525582d9e` | `dee8269f7b007d7edffea446b42645ee608e160b15b3a7bf2902ddb531f108d3` |
| `docs/evidence/cockroachdb-v26-2/capability-matrix.json` | `3e8c499fbcb2bb905fce451a163f913030ecacce` | `b7439ca2966086497f80c5c56a50f762f8678d8300c99a33e4e7d8628cf5acf4` | `5c2178d4ce2a1dd90247109f5c7011b603d928d2b5bb2fa979835285a2055763` |
| `docs/evidence/cockroachdb-v26-2/runtime-fingerprint.json` | `3e8c499fbcb2bb905fce451a163f913030ecacce` | `556ac8af1e095e7e66b1cf98340504d5502186d91f83dd2925bcf1f63bef46ba` | `34c656f929b67478c0519449e5b1fcce225c97acd8948cebd076269add64370b` |

## 4. Work completed after Step43

All work below followed the historical Step43 snapshot and was committed
during the submission period:

- Jury archive curation and explicit post-roadmap review boundaries.
- A bounded unified AIOA runtime and cockpit shell.
- A read-only legacy Critical Prompt Loop compatibility view.
- The live Memory Patch jury flow and same-origin form validation.
- AWS container packaging, immutable-image controls, CloudFormation, staged
  IAM propagation, ECS Express Mode deployment, health binding, and final
  assigned-origin handling.
- Bounded Amazon Cognito judge authentication with self-registration disabled.
- CockroachDB v26.2.5 final runtime pin and updated capability evidence.
- A read-only authenticated `ccloud` CLI control-plane release gate.
- Recovery and freeze of the exact AOIA-Core recording build, public AWS
  deployment, one bounded semantic smoke, and this final jury closure.

Key repository evidence:

- [Post-roadmap runtime closure](../audits/POST_ROADMAP_DEMO_RUNTIME_CLOSURE_1A.md)
- [Live jury flow architecture](../architecture/LIVE_MEMORY_PATCH_JURY_FLOW_1A.md)
- [AWS unified runtime architecture](../architecture/AWS_UNIFIED_AIOA_DEMO_RUNTIME_1A.md)
- [D4 AWS deployment runbook](../operations/D4_AWS_DEMO_DEPLOYMENT_1A.md)
- [CloudFormation template](../../infra/cloudformation/d4-aws-demo-runtime-1a.json)
- [Post-roadmap public runtime proof](../audits/POST_ROADMAP_R6_HEALTH_GOLDEN_PATH_RUNTIME_PROOF_1A.md)
- [ccloud control-plane gate](../evidence/cockroachdb-cloud/ccloud-control-plane-gate-1a.json)
- [Devpost component integration](DEVPOST_COMPONENT_INTEGRATION_1A.md)

## 5. Exact recorded and deployed demo provenance

| Identity | Frozen value |
| --- | --- |
| AOIA-Core source SHA | `360e900b66396a19fc09cccf69641cc015691ad8` |
| AOIA-Core release branch | `release/hackathon-jury-final` |
| AOIA-Core immutable tag | `hackathon-jury-final-2026-08-15` |
| Sanitized ECR image identity | `memory-patch-aioa-demo-1a@sha256:2b746c7cb67fd37341b2486c78f52c1211e6231285ca04579ca0d659c1bbae96` |
| Image/source SHA match | **PASS** |
| Application source changed after freeze | **NO** |
| Recording acceptance | German Law OFF PASS; German Law ON 5/5 PASS |
| Frozen recording tests | 26/26 PASS |
| Additional deployment adapter tests | 4/4 PASS |

The final ON path is:

```text
Browser -> Gemma Primary -> HAT audits the actual Primary
-> CockroachDB retrieval -> temporal/current resolution
-> correction requirement -> Gemma Final
-> deterministic Final verification -> verified browser response
```

The public semantic smoke used two provider calls, Gemma Primary and Gemma
Final, and zero Repair calls. Gemma authored the corrected Final semantics.
Python and the renderer did not insert the legal answer, and no static
final-answer oracle authored the correction. The disclosed `audit.oracle`
contract constrains and verifies the structured Final; it does not author it.

## 6. Final CockroachDB environment

- CockroachDB Cloud sanitized cluster: `fluid-lemur`.
- Plan/provider/region: Serverless, GCP, `europe-west3`.
- Database: `memory_patch_demo_1a`.
- Final CockroachDB version: **v26.2.5**.
- TLS: `verify-full`, PASS.
- Migration state: `UP_TO_DATE`, 19 canonical migrations.
- German-law knowledge: 36 total, 31 current/applicable,
  5 superseded/historical, 0 temporal conflicts.
- Read-only authenticated ccloud release gate: PASS.

Historical evidence that truthfully records v26.2.4 remains unchanged. The
v26.2.5 value above describes the final environment.

## 7. Final AWS environment and public entry points

- Region: `eu-central-1`.
- Stack: `memory-patch-aioa-demo-1a`.
- Service: `memory-patch-aioa-demo-1a`.
- Service state at release: active, one desired task, one running task,
  completed rollout.
- CloudWatch log group: `/aws/ecs/memory-patch-aioa-demo-1a`, seven-day
  retention.
- Public origin:
  `https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws`.
- Direct demo:
  `https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/memory`.
- Login:
  `https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/memory/login`.
- Safe judge identifier: `demo-judge`.
- Judge password: supplied only through the private submission testing-
  credentials field and never stored in this repository.
- Self-registration: disabled.
- Login/session/logout smoke: PASS.
- `GET /health/live`: HTTP 200.
- `GET /health/ready`: HTTP 200.
- Bootstrap/example origin retained in final configuration: **NO**.

The public service is hosted by AWS and does not depend on the developer's
computer or home network remaining online.

## 8. Final verification and publication safety

The documentation-only closure validation records:

- historical manifest sidecar: PASS;
- historical paths: 302/302 PASS;
- historical Git hashes: 257/257 PASS;
- local Markdown links: PASS;
- targeted control tests: 48/48 PASS;
- prior full regression at the audited baseline: 2366/2366 PASS;
- public `/health/live`: HTTP 200;
- public `/health/ready`: HTTP 200;
- root MIT license: present;
- new/modified public-file secret scan: PASS;
- secret values retrieved during this closure: **NO**;
- application/runtime or infrastructure source modified: **NO**;
- AWS, Cognito, or CockroachDB state modified: **NO**;
- additional provider calls during this documentation closure: `0`.

## 9. Final submission artifacts

- [Root README](../../README.md)
- [Submission index](../SUBMISSION_INDEX_1A.md)
- [Copy-ready submission package](HACKATHON_SUBMISSION_PACKAGE_1A.md)
- [Jury preparation and known limitations](JURY_PREPARATION_AND_KNOWN_LIMITATIONS.md)
- [AWS and CockroachDB architecture diagram](../architecture/Memory_Patch_AWS_CockroachDB_Architecture.png)
- [Historical Step43 archive](<../../Raporty Hackathon CockroachDB/README.md>)
- [MIT License](../../LICENSE)
- Recorded demo video: delivered through the hackathon submission platform;
  the repository contains the [video script](../demo/YOUTUBE_DEMO_SCRIPT_1A.md)
  but does not fabricate a repository-hosted video URL.

## 10. Known limitations

- Most exact prompt files from historical implementation sessions were not
  retained. Only the available exact-byte prompt artifacts are indexed in the
  Step43 archive; missing prompt transcripts were not reconstructed.
- Provider latency can pause the live flow at Primary or Final.
- The local disposable CockroachDB pgwire path was unreliable after reboot;
  the public build uses the verified hosted CockroachDB Cloud path.
- The frozen ECR scan reported no critical or high findings and retained five
  medium and one low finding.
- Application logs use AWS service-side encryption and seven-day retention,
  not a customer-managed log-encryption key.
- The bounded hackathon deployment does not claim production HA, DR, legal
  correctness beyond its fixture, or a production SLA.
- Critical Prompt Loop and German Law Knowledge are intentionally demonstrated
  separately.

## 11. Rollback identity

```text
SOURCE_SHA=360e900b66396a19fc09cccf69641cc015691ad8
IMAGE_DIGEST=sha256:2b746c7cb67fd37341b2486c78f52c1211e6231285ca04579ca0d659c1bbae96
AWS_STACK=memory-patch-aioa-demo-1a
PUBLIC_ORIGIN=https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws
```

Do not delete the frozen image before judging is complete.

FROZEN. VIDEO IS DONE. DO NOT CHANGE THE APPLICATION BEFORE JUDGING.
