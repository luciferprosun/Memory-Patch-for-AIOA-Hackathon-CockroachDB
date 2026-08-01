# German Law temporal-policy contract 1A

Policy identity: `german-law-temporal-policy-1a`.

The typed record preserves `published_at`, `promulgated_at`, `adopted_at`,
`effective_from`, `effective_to`, `applicable_from`, `applicable_to`,
`decision_date`, `retrieved_at`, `ingested_at`, `verified_at`, and
`superseded_at` separately.

Publication does not imply effect; adoption does not imply applicability;
retrieval and ingestion do not imply legal validity or verification. Missing
metadata stays unknown. Half-open applicability/effect intervals are checked
against the explicit request `knowledge_as_of`; invalid intervals conflict,
future starts are not yet applicable, and elapsed ends are expired. Open
intervals remain explicit. Supersession never deletes evidence.

This contract does not normalize real documents. That is Step 15.
