# Step 24 Correction Packet Validation 1A

## Preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
```

Require clean `main`, local/origin equality, Step 23 complete and pushed, and
Step 24/25 not started before implementation.

## Focused and full validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step24_correction_packet -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 -m compileall -q src scripts tests
python3 scripts/validate_contracts.py
```

The focused suite covers deterministic IDs/hashes, correction and prohibition
derivation, citation and conflict binding, canonical ordering/replay, evidence
status preservation, tenant/user/HAT detachment, secret-free HMAC receipts,
and the Step 25 boundary.

## Controlled offline validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step24_correction_packet_validation.py
```

Expected top-level status is `PASS`. The runner verifies the committed Step 23
evidence digest, then constructs a bounded typed Step 20-23 fixture. It proves:

- supported claim retention;
- refuted claim correction and prohibition;
- unverified qualification;
- temporal and source-authority correction;
- conflict preservation;
- exact citation binding;
- byte-identical packet replay;
- snapshot, packet, and HMAC tamper rejection;
- tenant detachment rejection;
- zero model, provider, retrieval, network, AWS, and S3 calls.

The committed Step 23 artifact contains only the real closure/snapshot hashes,
not a serialized production snapshot. The validator therefore reports real
upstream identity as hash-only and labels its typed semantic/edge fixtures as
synthetic. It does not claim a fabricated real-corpus Correction Packet.

## HMAC test setup

The validator creates an in-process `HmacSha256PacketAuthenticator` with a
fixed public non-production 32-byte test vector and the public key ID
`step24-non-production-validation-key`. The vector is intentionally committed
test data, not a secret; its authenticator and bytes are not included in the
evidence artifact. Production operators must inject approved runtime key
material through the future canonical secret boundary. No production secret
is required or read by Step 24.

## Static boundaries

```bash
rg -n \
  "generate.*v2|layered.*verifier|final.*claim.*verdict|verified.*answer" \
  src/aioa_memory_kernel/corrections \
  tests/test_step24_correction_packet.py || true

rg -n \
  "api[_-]?key|authorization:|bearer |password|secret_key|private_key|aws_secret|presigned" \
  src/aioa_memory_kernel/corrections \
  docs/evidence/modeling/step24-correction-packet-validation.json \
  docs/architecture/CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_1A.md \
  docs/operations/STEP_24_CORRECTION_PACKET_VALIDATION_1A.md \
  docs/audits/STEP_24_CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_CLOSURE_1A.md \
  || true
```

Review explanatory documentation hits. Secret values, provider calls, model
calls, Step 25 functionality, and execution APIs are forbidden.

## Persistence and cleanup

No database process, port, temporary runtime, provider, or external service is
started. Cleanup is `NOT_REQUIRED`. No migration is added. Existing Step 4
tables remain unchanged and are not written because complete durable upstream
lineage and explicit legacy route/action-policy columns are not present in the
Step 23 snapshot.

Step 25: NOT STARTED.
