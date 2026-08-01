# German Law HAT package and source-authority boundary 1A

Step 13 installs one trusted `german-law@1.0.0` implementation through the
Step 12 allowlist. It is a deterministic transformation and policy boundary,
not a legal adviser, retriever, publisher, model, or authority.

The immutable manifest supports only German requests and four capabilities:
request normalization, scope derivation, declarative evidence constraints,
and typed source-authority ranking. Jurisdiction is explicit (`DE_FEDERAL`,
`DE_STATE`, or `EU`); language never implies jurisdiction. A missing
`knowledge_as_of` remains an explicit ambiguity and is never replaced with
the current clock.

Authority is multi-dimensional. Authentic promulgation, official
consolidation, court decisions, legislative material, guidance, secondary
material, user documents, and derived summaries retain distinct typed source
classes. A rank never publishes a source and never turns repeated secondary
content into official evidence. Same-class conflicts remain unresolved.

The temporal contract keeps publication, promulgation, adoption, effect,
applicability, decision, retrieval, ingestion, verification, and supersession
timestamps separate. It evaluates only supplied metadata; real corpus
normalization remains Step 15.

Real corpus inventory and source registration remain Step 14 and are not
performed by this package.

Five fixed metadata adapters consume bounded typed metadata. They do not
fetch, run OCR, parse PDF/HTML, execute source content, access the corpus, or
assign final authority. Step 11 remains the generic content parser. The Step
12 registry remains the only runtime enablement boundary, and the Step 9
service remains the publication boundary.

The trusted installed-code allowlist does not claim a Python sandbox.

No database migration is required: Step 12 migration `0009` already stores
the manifest identity, runtime binding, registry state, review receipt, and
append-only lifecycle events.
