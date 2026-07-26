# Memory Patch for AIOA — repository instructions

## Canonical production line

Before any production implementation, audit, or release task, read
`docs/roadmap/PRODUCTION_ROADMAP.md` completely. It is the authoritative
production order and scope for this repository. If another roadmap or an older
desktop copy differs, the repository copy controls unless the user explicitly
replaces it.

Execute one roadmap step per task. Do not start the next step until the current
step is either `COMPLETE AND PUSHED` or the user explicitly defers it. Do not
silently combine, skip, renumber, or reinterpret steps.

Include a canonical-roadmap checkpoint update only in the intended step
commit. Treat that update as completion evidence only after validation,
commit, push to `origin/main`, and the closing report all succeed. A checked
box without a reachable pushed commit is not completion evidence.

## Repository guard

The production repository is this repository. Its expected branch is `main`
and its expected remote is
`https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB.git`.

At the start of every production task, verify:

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git diff --name-only
git rev-list --left-right --count main...origin/main
```

Stop safely if the branch is wrong, unrelated worktree changes exist, an
unexpected Git operation is active, or the task's required starting commit
does not match.

## Current verified implementation savepoint

At roadmap adoption:

- Step 0A is complete and pushed at
  `b3d555ec230a894b541e3570347fcf086511df2a`.
- Step 0B is complete and pushed at
  `870145c78d9e6bf02e318bdca2327eb808f381b7`.
- Step 0C is an accepted architecture baseline from the source roadmap.
- Step 1 is complete and pushed at
  `3f6d341bc3ceb964a2b25d4913a0695595dbd7d0`.
- The exact next implementation task is Step 2, Kernel Contract Re-Audit and
  Authority Invariant Closure 1A.

The repository HEAD may advance after this adoption record. Confirm completion
through Git history and the canonical roadmap rather than assuming this
adoption-time hash remains HEAD.

## Authority boundary

Provider or model output is never authority. HATs, models, Critic Loop,
previews, registry state, policy state, risk flags, and sandbox eligibility do
not grant approval, commit, execution, external-action, or Control Write
authority. Preserve human approval, hash binding, tenant isolation, gates,
fail-closed behavior, and audit evidence throughout the roadmap.
