# ADR-020: German Law is a trusted policy HAT, not a legal authority

## Decision

Install `german-law@1.0.0` through the Step 12 trusted catalog. Keep its
request, jurisdiction, temporal, source-class, and metadata-adapter contracts
inside the domain package. Keep Kernel Core domain-neutral.

Use typed source classes and explicit limitations instead of one universal
numeric score. Treat source metadata parsing as a candidate transformation,
not verification. Preserve authentic promulgation, consolidation, court,
legislative-history, guidance, secondary, user, and derived distinctions.

## Consequences

The HAT cannot answer, retrieve, publish, approve, commit, act externally,
access Personal Memory, or load code dynamically. Real corpus inventory is
Step 14; real temporal normalization is Step 15. Step 13 adds no migration and
does not claim a Python sandbox.
