# D2 Legacy Critical Prompt Compatibility Closure 1A

## Outcome

`D2 COMPLETE - READY FOR D3`

D2 starts from D1 commit
`037369bbcda155bf65c7740dc11872e52d0070f4`, preserves the D0
`LEGACY_VIEW_ONLY` safety decision, and closes the compatibility layer as
`DISABLED_WITH_ARCHIVAL_VIEW`. Memory Patch remains the default/current
production-authority mode. Critical Prompt Loop is optional historical source
metadata, not a replay and not a live model workflow.

The architecture is documented in
[`LEGACY_CRITICAL_PROMPT_COMPATIBILITY_LAYER_1A.md`](../architecture/LEGACY_CRITICAL_PROMPT_COMPATIBILITY_LAYER_1A.md),
and the machine-readable result is
[`d2-legacy-critical-compatibility-validation-1a.json`](../evidence/demo/d2-legacy-critical-compatibility-validation-1a.json).

## D0 and D1 handoff

- D0 verdict: `READY FOR D1`.
- D0 legacy result: exact source provenance exists, but no complete exact-byte
  execution trace supports a truthful replay and no live adapter was approved.
- D1 verdict: complete and pushed at
  `037369bbcda155bf65c7740dc11872e52d0070f4`, ready for D2.
- D2 repository start: branch `main`, `HEAD == origin/main`, divergence `0 0`,
  clean worktree, no active Git operation.
- Step 44: not created.

## Implementation

D2 adds a closed server-side `LegacyCompatibilityMode` with only `DISABLED`
and `ARCHIVAL_VIEW`. The existing D1 binary configuration flag maps to those
values and cannot enable replay/live behavior. The immutable
`LegacyArchiveManifest` binds eight exact AOIA-Core source references, the
three fixed historical observer roles, the five-call historical code shape,
and explicit missing-byte statuses to canonical SHA-256
`a614ca538c74ca804cec225593dcf437ae0d4b8dc9ca30f90d366bc0526c5ee9`.

The existing authenticated `GET /memory/demo` route renders a provenance
panel inside the same cockpit. It visibly states `ARCHIVAL VIEW`, `NOT LIVE`,
`NOT A REPLAY`, and `0 PROVIDER CALLS`. There is no prompt input, run endpoint,
legacy controller, provider client, database operation, replay bundle, or
Personal Memory bridge. Optional archive failure disables only that view;
current mode stays available.

AOIA-Core remained read-only. D2 copied zero external source bytes and added
no runtime dependency on another checkout. The old server, Tkinter UI,
provider client, controller authority, authentication assumptions, and retry
logic remain rejected.

## Authority and security proof

Legacy has zero canonical-evidence, route/HAT, reviewer, source-publication,
approval, commit, activation, Personal Memory, and external-execution
authority. It owns no provider, database, backend, session, or Commit Helper
object. Both views continue through the single current OIDC/judge/session
boundary; the legacy page is one authenticated GET and has no mutation.

Browser attempts to select `REPLAY`, `LIVE_BOUNDED`, or an arbitrary role fail
closed to the current view and are not reflected. POST to the cockpit is not
allowed. XSS and prompt-injection payloads remain inert, Jinja autoescaping and
CSP remain active, and no privileged secret or local filesystem path reaches
the HTML. A missing/integrity-invalid archive cannot take down current mode.

## Validation

| Gate | Result |
|---|---|
| D1/D2 cockpit, authority, evidence, and controlled loopback | `22/22 PASS` |
| R4-R7, Step35/36/39/40/41 focused regression | `257/257 PASS` |
| Total unit tests in the D2 campaign | `279/279 PASS` |
| AOIA-Core exact source verification | `8/8` blobs and SHA-256 values `PASS` |
| D2 offline validator | `PASS`; digest `cddf110e9a953dff0645db4b66b9b9c381d901dd9d116c33eb0fd1a48618f5ae` |
| Controlled loopback | live `200`, ready `200`, current `200`, archive `200`, clean shutdown |
| Actual paid provider calls | `0` |
| Authority violations | `0` |
| Cross-owner/session violations | `0`; no owner-scoped legacy run state exists |
| Secret leakage | `0` |
| Production/AWS resources touched | `0` |
| Python compilation, contract validator, UI assets, docs links, `git diff --check` | `PASS` |

The source-verification command used the existing local AOIA-Core Git object
store read-only and compared exact blobs, byte lengths, and SHA-256 values. It
made no provider call, database call, public network service, AWS mutation, or
durable runtime resource.

## Git closure and handoff

The intended separate D2 commit subject is
`feat(demo): add legacy critical prompt compatibility mode 1a`. The containing
Git commit is the final non-self-referential identity for this evidence; its
SHA and push status are reported after creation rather than embedded into its
own bytes.

D3 is not started here. The exact next activity after a successful push is
`D3 - MEMORY PATCH LIVE JURY FLOW 1A`. D4-D6 remain AWS deployment, hosted
Golden Path/freeze, and recording/submission work respectively.
