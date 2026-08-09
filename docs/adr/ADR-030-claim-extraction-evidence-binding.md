# ADR-030: Exact-span claim extraction and verified-universe evidence binding

## Status

Proposed. It becomes accepted only when the Step 23 closure commit is
reachable on `origin/main`.

## Context

Step 22 preserves an exact, genuinely uncorrected Draft V1. Steps 20 and 21
already freeze the eligible evidence universe and its temporal, freshness, and
conflict policy. Step 24 needs stable correction inputs without allowing a
model, retrieval score, or new source to decide truth.

## Decision

1. Claims bind exact NFC Draft V1 code-point spans; exact claim text is never
   replaced by a normalized form.
2. Claim IDs deterministically bind the Draft V1 hash, inclusive/exclusive
   offsets, and exact text.
3. Extraction is deterministic and model-free. Unsafe clause splitting keeps
   the exact compound claim and marks it `COMPOUND`.
4. Evidence may come only from verified Step 20 items with matching Step 21
   assessments. Every upstream and nested hash is revalidated.
5. Exact normalized assertion equality and an explicit one-negation
   counterpart are the only V1 support/refutation rules. Similarity, vector
   distance, modality count, and rank are never truth authority.
6. Step 21 applicability, freshness, conflicts, and source authority constrain
   candidate verdicts. Supporting and refuting evidence in a material conflict
   is preserved and yields `UNVERIFIED`.
7. `SUPPORTED`, `REFUTED`, and `UNVERIFIED` are Step 23 candidate statuses,
   not final verifier verdicts.
8. A canonical immutable snapshot freezes ordered claims, evidence links, and
   candidate assessments as the exact Step 24 input.
9. Step 23 adds no migration. Existing final claim-verdict storage is not
   overloaded with candidate semantics.
10. Step 24 owns required corrections, prohibited claims, Correction Packet
    construction and integrity. Draft V2 remains later.

Step 24: NOT STARTED.

## Consequences

The future Correction Packet can prove exactly which Draft text and evidence
produced every candidate assessment. Conservative rules intentionally leave
many claims `UNVERIFIED`; later stages may add bounded verification without
changing Step 23 identities or inventing evidence.

## Rejected alternatives

### Let the model self-verify claims

Rejected because model output is candidate data, not evidence authority.

### Treat semantic similarity or retrieval rank as support

Rejected because candidate relevance does not establish truth, publication,
authority, or temporal applicability.

### Retrieve missing evidence during binding

Rejected because Step 23 freezes mappings within the verified Step 20/21
universe; retrieval expansion would create a second weaker scope boundary.

### Persist candidate statuses as final claim verdicts

Rejected because it would blur the Step 23/Step 25 semantic boundary and
misrepresent preliminary evidence candidates as final verification.
