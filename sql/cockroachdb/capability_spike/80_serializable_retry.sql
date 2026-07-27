-- Memory Patch Step 3 — disposable SERIALIZABLE and retry-signal setup.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_retry_counter CASCADE;

CREATE TABLE {{RUN_PREFIX}}_retry_counter (
  counter_id STRING PRIMARY KEY,
  value INT8 NOT NULL
);

INSERT INTO {{RUN_PREFIX}}_retry_counter (counter_id, value)
VALUES ('{{RUN_PREFIX}}_shared_counter', 0);

SHOW default_transaction_isolation;
SHOW transaction_isolation;

BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SHOW transaction_isolation;
COMMIT;

-- HARNESS NATURAL CONCURRENCY:
-- Run at most 10 bounded attempts using two independent sessions and an
-- explicit client-release barrier plus an in-transaction overlap delay:
-- * both begin SERIALIZABLE transactions;
-- * both read shared-counter before either writes;
-- * both attempt a deterministic increment and commit;
-- * record which commit succeeded, any client-visible SQLSTATE, transparent
--   server retry behavior, and the final counter invariant;
-- * rollback/close every failed session before backoff.
--
-- HARNESS SYNTHETIC FALLBACK:
-- Only if natural contention does not reliably expose client-visible 40001,
-- verify that the pinned runtime documents and exposes a session-local,
-- test-only retry-error injection mechanism. Enable it in one disposable test
-- session, capture SQLSTATE 40001, and reset it immediately. Keep this result
-- separate from the natural-contention row. Never enable an undocumented or
-- global failure injector.
--
-- HARNESS OFFLINE CLASSIFIER:
-- Prove 40001 retryable, 23505 non-retryable, bounded attempts/backoff,
-- deterministic exhaustion, and no open transaction while sleeping. This
-- utility is probe tooling, not the Step 4 persistence adapter.
