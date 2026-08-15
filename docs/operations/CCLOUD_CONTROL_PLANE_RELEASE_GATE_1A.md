# ccloud Control-Plane Release Gate 1A

## Purpose

This gate gives the release agent a real, bounded use of the CockroachDB Cloud
control plane. Before a migration, import, or deployment is authorized, the
agent uses `ccloud` JSON output to prove that the intended hosted cluster is
the unique target and that its provider, plan, region, state, CockroachDB
version, and SQL endpoint identity match the approved release configuration.

The result is a fail-closed decision. A mismatch blocks the release operation;
it never triggers a repair, cluster mutation, database write, prompt change,
or fallback to another cluster.

Official references:

- [Get started with ccloud](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started)
- [ccloud command reference](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-reference)

## Exact target

| Property | Required value |
| --- | --- |
| Cluster name | `fluid-lemur` |
| Cloud provider | `GCP` |
| Region | `europe-west3` |
| Plan | `SERVERLESS` |
| State | `CREATED` |
| CockroachDB version | `v26.2.5` |
| SQL DNS SHA-256 | `697f3b8221a81df8c3e29d92d519fee97158574e46e3e5a29965c0dade967ae8` |
| ccloud version | `0.6.12` |
| ccloud binary SHA-256 | `944c7a35f9fe6b166dea991040399ac4e1cf0c754d0514fd57d7c4333c5d4cb2` |

The DNS digest binds the gate to the same sanitized hosted-cluster identity
used by the jury runtime without storing a DSN, SQL username, password, API
key, or raw control-plane cluster ID in evidence.

## Authentication boundary

Authenticate interactively through the official browser flow:

```bash
ccloud auth login
```

The local `ccloud` session is an operator credential. It is never copied into
Git, the container image, AWS, browser JavaScript, evidence, or command-line
arguments. Do not paste a CockroachDB password, authorization code, token, or
API key into a prompt or report.

## Required gate

Run from a clean repository on `main`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/run_ccloud_control_plane_gate.py \
  --cluster-name fluid-lemur \
  --expected-provider GCP \
  --expected-region europe-west3 \
  --expected-plan SERVERLESS \
  --expected-state CREATED \
  --expected-version v26.2.5 \
  --expected-sql-dns-sha256 \
    697f3b8221a81df8c3e29d92d519fee97158574e46e3e5a29965c0dade967ae8 \
  --pretty
```

The implementation executes exactly two control-plane reads:

```text
ccloud cluster list --output json --quiet
ccloud cluster info fluid-lemur --output json --quiet
```

No create, update, delete, SQL, backup mutation, restore, networking mutation,
or user-management command is in the gate. The child environment is reduced
to the small set needed by the CLI, preventing unrelated provider, database,
or AWS credentials from entering the process.

Required verdict:

```text
PASS_READ_ONLY_CCLOUD_CONTROL_PLANE_GATE
```

Any other result blocks the next migration, import, or deployment operation.
The release operator must not substitute a different cluster or weaken an
expectation to obtain a pass.

## Evidence and authority

The sanitized live receipt is stored at
[`ccloud-control-plane-gate-1a.json`](../evidence/cockroachdb-cloud/ccloud-control-plane-gate-1a.json).
It records hashed cluster and endpoint identities, allowlisted readiness
metadata, the two reads, zero mutations, and secret-exposure assertions.

`ccloud` supplies operational readiness evidence only. It cannot determine
legal truth, write a model answer, change HAT routing, approve Personal Memory,
alter canonical source authority, or modify the frozen jury application.
