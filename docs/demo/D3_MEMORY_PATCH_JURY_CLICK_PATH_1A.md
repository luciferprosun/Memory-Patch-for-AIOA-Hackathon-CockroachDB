# D3 Memory Patch Jury Click Path 1A

## Current mode

1. Sign in through the configured judge OIDC flow.
2. Open `/memory/demo`.
3. Keep **Memory Patch - Current** selected; it is the default.
4. Read the two exact guided German Law questions and their SHA-256 identities.
5. Start `primary-entry-into-force` once.
6. Follow the progressive trace from the evidence-blind Draft V1 to the
   evidence/temporal/claim stages.
7. If the model exposed a material defect, show the real Correction Packet,
   Draft V2, layered verification, and Step 26 Verified Answer.
8. If the Draft V1 was already correct, show `CORRECTION_NOT_REQUIRED`; never
   manufacture a defect. Use only `backup-special-case-reservation` if a
   correction demonstration is still needed.
9. Use the Personal Memory panel only for records that really exist. Approval
   is an explicit owner action and remains separate from commit/activation.
10. Show an ACTIVE patch only with its real patch hash and model binding; state
    that it is private context, not retraining and not canonical evidence.

Status polling stops at `COMPLETED` or `BLOCKED`. Reloading the page does not
submit a run. Reusing the same form idempotency identity does not create a
second run or provider request.

## Legacy comparison

Switch to **Critical Prompt Loop - Legacy** only for the origin story. D2's
classification remains `DISABLED_WITH_ARCHIVAL_VIEW`: it is an immutable
source-provenance view, not live and not replay. It cannot feed the current
answer, write evidence, approve memory, or call the provider.

The concise jury contrast is:

- Legacy: a model checked and revised model output.
- Current: canonical evidence, temporal reasoning, deterministic correction,
  layered verification, private owner-approved memory, and explicit authority
  separation.

## Truth labels

The D3 browser may display `LIVE`, `DETERMINISTIC_TEST`, `BLOCKED`, `NOT RUN`,
or `NOT APPLICABLE`. The controlled validation uses
`DETERMINISTIC_TEST`; the hosted application uses `LIVE`. Neither label changes
the evidence or verification authority. Replay is never labelled live.

The UI is a hackathon demonstration of a bounded German federal-law fixture,
not general legal advice.
