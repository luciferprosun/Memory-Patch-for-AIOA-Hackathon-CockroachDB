# Source Repository Snapshot

## Path resolution

The requested path `/home/l/AIOA_PRODUCTION/repos/AOIA-Core` did not exist. No file or repository
was present there. The only canonical production checkout matching the requested
AOIA-Core source was `/home/l/AOIA_PRODUCTION/repos/AOIA-Core`, so discovery used that corrected path. This
correction swaps the transposed `AIOA_PRODUCTION` spelling to the existing
`AOIA_PRODUCTION` directory and does not change repository identity.

## Initial read-only snapshot

- Repository: `/home/l/AOIA_PRODUCTION/repos/AOIA-Core`
- Remote identity: `https://github.com/luciferprosun/AOIA-Core.git`
- Branch: `feature/m2-b0-provider-critic-inert-core`
- HEAD: `7f61a3b167a028d7e34b852cca4ade809ceec571`
- Date window: `2026-06-30T00:00:00+02:00` through `2026-07-24T23:59:59+02:00` (inclusive, Europe/Berlin)

`git status --short`:

```text
 M web/app.js
 M web/operator_config.js
```

`git remote -v`:

```text
origin  https://github.com/luciferprosun/AOIA-Core.git (fetch)
origin  https://github.com/luciferprosun/AOIA-Core.git (push)
```

The two modified files were pre-existing application-code changes. They were
not read as uncommitted documentation, copied, edited, staged, committed or
cleaned. No untracked documentation was present.

## Read-only ref refresh

`git fetch --all --prune` refreshed refs without checking out another branch.
It discovered `origin/demo/knowledge-hat-bridge-german-law-v2-1a`, including
commit `708fe063b3d81f1d61ca2cc7787f94550d52fbd0`. The source branch, HEAD and worktree status remained unchanged.

## Assembly-time comparison

Immediately before writing the target import, source HEAD was still
`7f61a3b167a028d7e34b852cca4ade809ceec571` and `git status --short` still
contained exactly the same two pre-existing modified application files.
