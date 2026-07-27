-- Memory Patch Step 3 — disposable English and German full-text probes.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_full_text_documents CASCADE;

CREATE TABLE {{RUN_PREFIX}}_full_text_documents (
  document_id STRING PRIMARY KEY,
  body STRING NOT NULL,
  english_vector TSVECTOR AS (to_tsvector('english', body)) STORED,
  german_vector TSVECTOR AS (to_tsvector('german', body)) STORED
);

CREATE INVERTED INDEX {{RUN_PREFIX}}_fts_en_idx
  ON {{RUN_PREFIX}}_full_text_documents (english_vector);

CREATE INVERTED INDEX {{RUN_PREFIX}}_fts_de_idx
  ON {{RUN_PREFIX}}_full_text_documents (german_vector);

INSERT INTO {{RUN_PREFIX}}_full_text_documents
  (document_id, body)
VALUES
  (
    '{{RUN_PREFIX}}_en_high',
    'Foxes foxes jump and jumping quickly'
  ),
  (
    '{{RUN_PREFIX}}_en_low',
    'One fox will jump'
  ),
  (
    '{{RUN_PREFIX}}_de_high',
    'Häuser Häuser und Kinder laufen laufen'
  ),
  (
    '{{RUN_PREFIX}}_de_low',
    'Häuser stehen und Kinder laufen'
  ),
  ('{{RUN_PREFIX}}_en_miss', 'A turtle walks slowly'),
  ('{{RUN_PREFIX}}_de_miss', 'Ein Auto fährt langsam');

SELECT
  document_id,
  english_vector @@ plainto_tsquery('english', 'fox jump') AS matches,
  ts_rank(english_vector, plainto_tsquery('english', 'fox jump')) AS rank
FROM {{RUN_PREFIX}}_full_text_documents
WHERE english_vector @@ plainto_tsquery('english', 'fox jump')
ORDER BY rank DESC, document_id;

SELECT
  document_id,
  german_vector @@ plainto_tsquery('german', 'Häuser laufen') AS matches,
  ts_rank(german_vector, plainto_tsquery('german', 'Häuser laufen')) AS rank
FROM {{RUN_PREFIX}}_full_text_documents
WHERE german_vector @@ plainto_tsquery('german', 'Häuser laufen')
ORDER BY rank DESC, document_id;

SHOW INDEXES FROM {{RUN_PREFIX}}_full_text_documents;

EXPLAIN (OPT, VERBOSE)
SELECT document_id
FROM {{RUN_PREFIX}}_full_text_documents
WHERE english_vector @@ plainto_tsquery('english', 'fox jump')
ORDER BY document_id;

-- HARNESS NEGATIVE:
-- Execute a malformed tsquery separately and require a deterministic SQL
-- rejection. Do not classify an unrelated CLI or decoding crash as evidence.
-- Probe websearch_to_tsquery separately and record its v26.2 unavailability;
-- unsupported syntax is never part of this replayable positive SQL path.
--
-- HARNESS SEMANTICS:
-- Assert exact match sets and deterministic rank/id ordering. Record English
-- and German dictionary behavior independently so one language cannot mask a
-- limitation in the other.
