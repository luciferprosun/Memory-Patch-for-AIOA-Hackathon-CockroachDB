# Local Toolchain Bootstrap 1A

## Status

Bootstrap date: 2026-07-24 (Europe/Berlin)

The user-space toolchain and all required base utilities are installed. GitHub CLI authentication and the public account name `luciferprosun` were confirmed without exposing a token.

No incomplete state is presented as a successful bootstrap.

## Repository guard

- Repository: `/home/l/AIOIA_HACKATHONS/Memory-Patch-for-AIOA-Hackathon-CockroachDB`
- Expected origin: `https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB`
- Branch: `main`
- Starting HEAD: `04709a84f8cb8407a6fdf060210403f8e323133f`
- Starting worktree: clean
- Starting divergence from `origin/main`: `0 0`

The configured origin, branch, clean worktree, and synchronization requirements were satisfied before changes began.

## Machine snapshot

Snapshot timestamp: `2026-07-24T10:52:39+02:00`

| Item | Observed value |
|---|---|
| Operating system | Linux Mint 22.2 (Zara), Ubuntu noble base |
| Kernel | Linux 6.17.0-35-generic |
| Architecture | x86_64 |
| Word size | 64 bit |
| Shell | `/bin/bash` |
| RAM | 3.7 GiB total; 1.1 GiB available at initial audit |
| Swap | 4.0 GiB total |
| Root/home filesystem | 57 GiB total; 1.1 GiB available initially; 905 MiB available after user-space installation |

`/` and `/home` are on the same `/dev/mmcblk0p2` filesystem, which was 99% used during this bootstrap.

`$HOME/.local/bin` was already in `PATH`. No shell startup file or backup was created.

## Tools found before installation

| Tool | Initial path | Initial version/state |
|---|---|---|
| git | `/usr/bin/git` | 2.43.0 |
| gh | `/usr/bin/gh` | 2.45.0 |
| curl | `/usr/bin/curl` | 8.5.0 |
| wget | `/usr/bin/wget` | 1.21.4 |
| tar | `/usr/bin/tar` | 1.35 |
| unzip | `/usr/bin/unzip` | 6.00 |
| xz | `/usr/bin/xz` | 5.4.5 |
| gpg | `/usr/bin/gpg` | 2.4.4 |
| less | `/usr/bin/less` | present |
| make | `/usr/bin/make` | 4.3 |
| python3 | `/usr/bin/python3` | 3.12.3 |
| AWS CLI | `/home/l/.local/bin/aws` | AWS CLI 2.35.16 |
| jq | — | missing |
| uv / uvx | — | missing |
| ccloud | — | missing |
| cockroach-sql | — | missing |
| CockroachDB server | — | not installed |

## Base system packages

The following packages were already installed and were not changed:

- ca-certificates `20260601~24.04.1`
- curl `8.5.0-2ubuntu10.11`
- unzip `6.0-28ubuntu4.1`
- xz-utils `5.6.1+really5.4.5-1ubuntu0.3`
- tar `1.35+dfsg-3build1`
- gnupg `2.4.4-2ubuntu17.4`
- less `590-2ubuntu2.1`
- make `4.3-4.1build2`
- git `1:2.43.0-1ubuntu7.3`

`jq` was initially the only missing required base package. A non-interactive `sudo` capability probe failed, so the automated bootstrap did not escalate privileges. The operator subsequently installed `jq` `1.7.1-3ubuntu0.24.04.2` with the native package manager. No broad system upgrade, package removal, or unrelated replacement was performed.

## User-space tools

| Tool | Actual version | Executable/location | Action |
|---|---|---|---|
| uv | 0.11.31 | `/home/l/.local/bin/uv` | installed |
| uvx | 0.11.31 | `/home/l/.local/bin/uvx` | installed |
| CPython | 3.12.3 | `/usr/bin/python3.12` | retained |
| AWS CLI | 2.35.16 | `/home/l/.local/bin/aws` → `/home/l/.local/aws-cli/v2/2.35.16/dist/aws` | retained |
| ccloud | 0.6.12 | `/home/l/.local/bin/ccloud` | installed |
| Cockroach SQL client | v26.2.3 | `/home/l/.local/bin/cockroach-sql` | installed |
| GitHub CLI | 2.45.0 | `/usr/bin/gh` | retained |
| jq | 1.7.1 | `/usr/bin/jq` | installed by operator with the native package manager |

`uv` used a 76 KiB cache under `/home/l/.cache/uv` while verifying the existing system CPython. No project virtual environment or dependencies were created.

## Official sources and integrity

### uv

- Installer: `https://astral.sh/uv/0.11.31/install.sh`
- Installer downloaded to a temporary directory and inspected before execution.
- Declared installer version: `0.11.31`
- Installer SHA-256: `bd9a2739c49251c71fd3706ac00b1bb8582ea138433c6e52840de4aba646e46a`
- Install mode: `UV_UNMANAGED_INSTALL=/home/l/.local/bin`
- Temporary installer removed.

### AWS CLI

- Existing working AWS CLI v2 was retained, as required.
- No AWS package was downloaded or reinstalled.
- Only `aws --version` was executed.
- The reproducible bootstrap script uses the official `awscli.amazonaws.com` archive and detached PGP signature if AWS CLI v2 is absent. It validates the official signing-key fingerprint `FB5DB77FD5C118B80511ADA8A6310ACC4672475C`.

### ccloud

- Archive: `https://binaries.cockroachdb.com/ccloud/ccloud_linux-amd64_0.6.12.tar.gz`
- Archive structure inspected before installation.
- Cockroach Labs does not publish a checksum beside this archive; the downloaded official artifact was pinned locally.
- Archive SHA-256: `a0d26bd1dd2f904a8464cadb2f0c062afa8cb68b5aadd9717dd7109dc9ad61b2`
- Installed binary SHA-256: `944c7a35f9fe6b166dea991040399ac4e1cf0c754d0514fd57d7c4333c5d4cb2`
- Embedded version string: `0.6.12`

The supported `ccloud version` command unexpectedly attempted to retrieve public client configuration from `cockroachlabs.cloud` before printing its version. DNS was blocked by the execution sandbox, so no remote request reached Cockroach Labs and no organization or account was contacted. `ccloud --version` is unsupported by this release. Subsequent verification is deliberately offline and uses the pinned binary hash and embedded version.

### Cockroach SQL-only client

- Archive: `https://binaries.cockroachdb.com/cockroach-sql-v26.2.3.linux-amd64.tgz`
- Published checksum: `https://binaries.cockroachdb.com/cockroach-sql-v26.2.3.linux-amd64.tgz.sha256sum`
- Published and verified SHA-256: `b917799627b9da4f21532e248ce7f859c6ede7bbda2777110209d15ae9d7d386`
- Verified build tag: `v26.2.3`
- Build type: `release`
- Only the SQL client executable was installed. No CockroachDB server was installed or started.

## Authentication and cloud boundary

`gh auth status`, checked with network access after an isolated-network check produced a false negative, confirms the active account `luciferprosun`. `gh api user --jq '.login'` independently resolves the same public username. No token value was read or recorded, and no interactive login was initiated by the bootstrap.

Deferred manual actions:

- CockroachDB account login;
- CockroachDB cluster creation;
- CockroachDB Managed MCP authorization;
- AWS authentication;
- AWS infrastructure creation.

No AWS login, profile creation, credential read, AWS API request, CockroachDB login, cluster creation, database connection, Managed MCP configuration, or paid resource creation was performed.

## Repository files

This step creates or extends only:

- `.gitignore`
- `docs/operations/LOCAL_TOOLCHAIN_BOOTSTRAP_1A.md`
- `docs/architecture/PROJECT_BOUNDARY.md`
- `scripts/bootstrap_toolchain.sh`
- `scripts/verify_toolchain.sh`
- `tooling/versions.env`

No application dependency, application code, infrastructure, schema, migration, database, secret file, GitHub Action, or German-law corpus material is created.

## Validation state

`scripts/verify_toolchain.sh` is intentionally strict. After the operator installed `jq`, the complete toolchain verification succeeded. GitHub authentication also passed when checked with network access. Final secret scanning, commit, and push occur only after the verifier succeeds.

The AOIA-Core path named in the task, `/home/l/AIOA_PRODUCTION/repos/AOIA-Core`, was absent at validation time. It was not modified. No other repository was modified.
