# Devpost Component Integration 1A

## Selected gallery components

CockroachDB:

- **Distributed Vector Indexing**
- **ccloud CLI**

AWS:

- **AWS Lambda**
- **Amazon ECS / EKS** (the project uses ECS, not EKS)
- **Amazon S3**
- **Other AWS service**: Amazon ECR, Amazon Cognito, AWS Secrets Manager,
  AWS KMS, Amazon CloudWatch Logs, AWS CloudFormation, and AWS IAM

The project does not claim Amazon Bedrock, Amazon SageMaker, or Amazon Bedrock
Agents.

## What the agent actually does

### CockroachDB Distributed Vector Indexing

When a trusted HAT route requires knowledge, the retrieval service creates a
query embedding with the pinned local E5 model. CockroachDB stores immutable,
lineage-bound `VECTOR(384)` embeddings and performs L2 distance retrieval with
the distributed vector index. Candidate generation is restricted by tenant,
HAT, scope, source, and RLS before results are admitted. Exact, full-text, and
vector candidates are then fused deterministically, deduplicated by lineage,
and passed through temporal, supersession, freshness, and conflict resolution.
The selected evidence constrains correction and verification; the vector
index never becomes semantic authority by itself.

### ccloud CLI

The AI-assisted release workflow executes an authenticated, fail-closed
`ccloud` control-plane gate against the exact hosted cluster used by the jury
runtime. It consumes JSON from `ccloud cluster list` and `ccloud cluster info`,
requires one exact cluster match, and verifies provider, Serverless plan,
region, ready state, CockroachDB version, and the hashed SQL endpoint identity.
A mismatch blocks later migration, import, or deployment operations. The
evidence records control-plane reads: `2`; control-plane mutations: `0`;
database writes: `0`; provider calls: `0`. Raw cluster IDs, user identity,
DSNs, passwords, and tokens are excluded.

This is operational integration, not CLI initialization: the agent actually
uses the returned control-plane state to decide whether the release target is
safe. The executable gate, offline tests, runbook, and sanitized live receipt
are committed with the project.

### AWS

Amazon ECS Express Mode with AWS Fargate runs the public HTTPS jury workload
from an immutable Amazon ECR image pinned by digest. The task starts only with
the exact CockroachDB and provider configuration injected server-side from AWS
Secrets Manager under a dedicated KMS key. Amazon Cognito supplies bounded
judge authentication; its AWS Lambda pre-token-generation trigger binds the
fixed demo tenant and immutable subject ownership claims before the agent
session is accepted. CloudWatch Logs records bounded runtime events.

Amazon S3 is part of the evidence-ingestion path rather than a decorative
bucket. Versioning and Object Lock retain exact source snapshot versions;
checksum, version ID, retention, and metadata are verified before the source
can progress through publication lineage. CloudFormation defines both the S3
authority boundary and the ECS runtime with reviewed, reproducible
infrastructure as code.

## Copy-ready Devpost answer

Memory Patch uses two CockroachDB tools in active workflows. Distributed
Vector Indexing is part of retrieval: a HAT-scoped query is embedded with a
pinned local E5 model, matched against lineage-bound `VECTOR(384)` records in
CockroachDB using L2 distance, fused with exact and full-text candidates, and
then filtered by temporal, authority, tenant, and conflict rules before the
model receives correction evidence. The AI-assisted release workflow also
uses the authenticated ccloud CLI as a fail-closed control-plane gate. It
reads JSON from `cluster list` and `cluster info` to verify the exact hosted
cluster, provider, region, Serverless plan, ready state, version, and hashed
endpoint identity; any mismatch blocks migration or deployment, and the gate
has zero control-plane or database mutations.

The public application runs as a bounded container on Amazon ECS Express Mode
with Fargate, pulled from an immutable digest-pinned Amazon ECR image. Amazon
S3 Versioning and Object Lock preserve exact evidence snapshots used by the
ingestion and provenance workflow. Amazon Cognito and a Lambda claims trigger
bind the judge identity and tenant, Secrets Manager and KMS inject runtime
credentials server-side, CloudWatch Logs provides operational evidence, and
CloudFormation makes the complete AWS environment reproducible. CockroachDB
remains the persistent memory and retrieval system of record; AWS runs and
secures the agent around it.

## Public evidence

- [Vector migration](../../sql/cockroachdb/migrations/0010_step19_embedding_vector_retrieval.sql)
- [Vector repository query](../../src/aioa_memory_kernel/embeddings/repository.py)
- [ccloud executable gate](../../scripts/run_ccloud_control_plane_gate.py)
- [ccloud gate runbook](../operations/CCLOUD_CONTROL_PLANE_RELEASE_GATE_1A.md)
- [ccloud live receipt](../evidence/cockroachdb-cloud/ccloud-control-plane-gate-1a.json)
- [AWS runtime template](../../infra/cloudformation/d4-aws-demo-runtime-1a.json)
- [S3 Object Lock template](../../infra/cloudformation/step7-s3-snapshot-authority-1a.json)
