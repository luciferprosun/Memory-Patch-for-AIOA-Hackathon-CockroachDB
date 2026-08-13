# Legacy Critical Prompt Compatibility Layer 1A

## Outcome

D2 freezes the Critical Prompt Loop decision as
`DISABLED_WITH_ARCHIVAL_VIEW`. The optional view exposes verified AOIA-Core
source provenance and the historically versioned workflow shape. It does not
execute the old system, make a model call, or claim that missing historical
prompt and output bytes form a replay.

Memory Patch remains the default and the only production-authority mode. D2
does not create Step 44, start D3, mutate AOIA-Core, copy AOIA-Core bytes into
the runtime, add a provider path, or touch AWS.

## Decision from D0 and D1

D0 found reusable presentation ideas and exact historical source code, but no
complete exact-byte execution artifact suitable for a truthful replay. It
also did not authorize a live compatibility adapter. D1 therefore shipped a
disabled `LEGACY_VIEW_ONLY` shell. D2 retains that safety decision and makes
the allowed archival view precise:

- server capability family: `DISABLED | ARCHIVAL_VIEW`;
- hosted default: `DISABLED`;
- `AIOA_DEMO_LEGACY_MODE_ENABLED=1`: enables archival metadata only;
- browser values cannot select replay or live execution because neither value
  exists in the server enum;
- no legacy prompt input, run route, controller, provider, replay payload,
  database mutation, or Personal Memory write exists.

## Exact AOIA-Core provenance

Source repository:
`https://github.com/luciferprosun/AOIA-Core.git`. D2 inspected it read-only and
revalidated all eight source blobs against both Git object identity and
SHA-256. No source byte was copied into Memory Patch.

| Commit | Path | Git blob | SHA-256 | D2 classification |
|---|---|---|---|---|
| `eda1449e6a63b6a41d8bc16409aa31a128176804` | `runtime/run_web.sh` | `521dc4c5c51d4386fd8cebf0d3462bbe2d596201` | `56e4e37b5b69c960e791c21b806aa8a217679f2159e3b190eba2083390e1193c` | `REJECT_RUNTIME`; launcher provenance only |
| `eda1449e6a63b6a41d8bc16409aa31a128176804` | `runtime/webapp.py` | `be3cd5b2f45390a509e88fed42f0cce871b332e2` | `cab5d5f4cbced96070b8572cd9a6fe9da4478591d4e415a302cf4a2dae1143ef` | `REJECT_RUNTIME`; visual reference only |
| `eda1449e6a63b6a41d8bc16409aa31a128176804` | `runtime/orchestra_live_smoke_cli.py` | `fb3ff9c79f8f8544e740a075761b478244de89f5` | `a8e80ae44c6786af0a957a1eab13422aa1b7f75519f958a4010b13350f76eb6b` | `REFERENCE_ONLY`; bounded orchestration |
| `46695cde96d12a52e20bea82ebe2e1798b7451fd` | `runtime/webapp.py` | `ec0952c75ecb884902fb5e7874c8f4c936b7a2da` | `96845111f4025f30feca85d0f2d6564af9106724bd12d670fafd7eb0379deacd` | `PORT_PRESENTATION`; status/review motifs |
| `51abb9faab2d07d21003a345c747b90b8eac5703` | `apps/aoia_desktop_demo/critical_review.py` | `ca2ea240d24525fb72ee9caccf475ecfff751625` | `0109969c6fbb7ccaca69a91f42a961ea7bc9806f150443a5e6009502ac801615` | `REFERENCE_ONLY`; bounded review origin |
| `5ec74f85256c260dadbc795143eb132b4119aab6` | `apps/aoia_desktop_demo/critical_review.py` | `07fabcf104eac4de63aa7f60d6af79fcdd7c37e4` | `cf5c48a3ce236df994844659aa46f3e1d4359f6d62774e7148526dc66f184964` | `REFERENCE_ONLY`; completed five-call flow |
| `5ec74f85256c260dadbc795143eb132b4119aab6` | `apps/aoia_desktop_demo/ui/cockpit_state.py` | `8960081a8e21ee76d59607afe5d235aec928d949` | `7912240599abb0afe34c5cda6eaf2b3e5db134eacd475031bed1a4e1e2a1ed43` | `PORT_PRESENTATION`; closed observer labels |
| `5ec74f85256c260dadbc795143eb132b4119aab6` | `apps/aoia_desktop_demo/ui/main_window.py` | `b2d08e863a34512e93baf11b8114cedc2a183725` | `7737f28681219495cacc0213614794d7f343933de9541619d99a60196eb9c94d` | `REJECT_RUNTIME`; Tkinter layout reference |

The exact role labels proven by `cockpit_state.py` are `Logic & Claims`,
`Safety & Authority`, and `Evidence & Consistency`. The completed controller
proves a bounded historical shape of one main draft, three sequential
observers, and one final revision. It also proves no retry or fallback in that
path. These are source-code facts, not proof of an exact past execution.

## Why this is not replay or live compatibility

The exact versioned execution prompt was not found. Complete exact-byte main
draft, observer outputs, combined review, and final revision were also not
found as one provenance-bound artifact. D2 therefore creates no replay bundle
and computes no hashes for unavailable content.

The old `ThreadingHTTPServer`, Tkinter UI, controller, provider client, auth
assumptions, and local launcher remain rejected. A live port would require a
new current-provider orchestration and a whole-run cost reservation. D0 did
not authorize that work, and it is unnecessary for the jury evolution story.

## One-application integration

```text
Browser
  -> existing OIDC + PKCE and allowed-judge policy
  -> existing durable owner session
  -> existing FastAPI/Jinja2/HTMX app
       -> GET /memory/demo
          -> Memory Patch current view (default)
          -> Critical Prompt archival metadata (optional)
  -> no legacy mutation route
  -> no legacy provider route
```

`LegacyArchiveManifest` is immutable, bounded, canonical-JSON hash-bound, and
contains only public repository metadata. Its source paths must be relative,
cannot traverse, and carry exact commit, Git blob, byte length, and SHA-256.
The manifest digest is
`a614ca538c74ca804cec225593dcf437ae0d4b8dc9ca30f90d366bc0526c5ee9`.
If the optional archive is missing or fails validation, the selector fails
closed and Memory Patch remains operational.

The template permanently says `ARCHIVAL VIEW`, `NOT LIVE`, `NOT A REPLAY`, and
`0 PROVIDER CALLS`. Jinja autoescaping and the current CSP apply to every
value. No unsafe HTML, legacy JavaScript, external CDN, browser storage, or
local-machine source path is introduced.

## Authority firewall

| Capability | Memory Patch current mode | Critical Prompt archival view |
|---|---:|---:|
| Current production authority | Yes, through existing typed services | No |
| Canonical evidence write | Existing authority only | No |
| Route/HAT or source policy change | Existing policy only | No |
| Reviewer authority | Existing separated service only | No |
| Personal Memory proposal/write | Existing current flow only | No |
| Approval, Commit Helper, activation | Existing separated flow only | No |
| Provider/model execution | Guarded current flow only | No path |
| External action execution | Existing policy only | No |

Mode selection is a non-mutating authenticated GET. It cannot change owner,
tenant, session, route, evidence, audit history, Personal Memory, or provider
budget. No D2 run exists, so no new audit event is created that could
misrepresent metadata viewing as a historical or live execution.

## Cost and resource bounds

- Historical source-code call shape: exactly five calls.
- D2 executable legacy minimum/maximum: `0/0` calls.
- D2 actual paid calls: `0`.
- Legacy concurrency, queue and run timeout: not applicable; no run exists.
- Manifest references: eight current entries, hard maximum sixteen.
- Each referenced source is bounded to 1,000,000 bytes during offline
  verification; only metadata is served.
- No prompt, response, run history, replay bytes, model cache, process, or
  database row is retained by D2.
- The existing one-worker 4 GB profile and current provider ledger are
  unchanged.

## Validation and D3 boundary

The offline validator is
`scripts/run_d2_legacy_critical_compatibility_validation.py`. With a read-only
AOIA-Core object store it verifies all exact source bytes. Runtime tests prove
OIDC/session reuse, GET-only routing, browser non-escalation, XSS and prompt
injection inertness, current-mode independence, and zero business/provider
mutation. The closure record is
[`D2_LEGACY_CRITICAL_PROMPT_COMPATIBILITY_CLOSURE_1A.md`](../audits/D2_LEGACY_CRITICAL_PROMPT_COMPATIBILITY_CLOSURE_1A.md).

D3 may bind only the current Memory Patch stages to the already verified live
runtime. D3 must not revisit or silently upgrade the legacy classification.
