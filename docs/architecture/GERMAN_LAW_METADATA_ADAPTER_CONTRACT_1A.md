# German Law metadata-adapter contract 1A

Adapter identity: `german-law-metadata-adapters-1a`.

The fixed registry contains adapters for federal promulgation, consolidated
federal law, official court decisions, authentic EU acts, and legislative
materials. Each accepts a bounded mapping already supplied by trusted
application composition, constructs immutable typed metadata, and records
findings such as a missing identifier.

Adapters never fetch URLs, read a corpus, parse PDF, run OCR, execute HTML,
call a model, publish, or assign final authority. Unsupported source classes
fail closed. Step 11 owns decoding and parsing; Step 13 adapters only bridge
bounded metadata into the policy.
