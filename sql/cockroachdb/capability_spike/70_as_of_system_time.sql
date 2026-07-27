-- Memory Patch Step 3 — disposable AS OF SYSTEM TIME historical-read probe.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_aost_items CASCADE;

CREATE TABLE {{RUN_PREFIX}}_aost_items (
  item_id STRING PRIMARY KEY,
  payload STRING NOT NULL,
  version_number INT8 NOT NULL
);

INSERT INTO {{RUN_PREFIX}}_aost_items (item_id, payload, version_number)
VALUES ('{{RUN_PREFIX}}_history_row', 'synthetic version one', 1);

SELECT cluster_logical_timestamp() AS version_one_hlc;

-- HARNESS BARRIER:
-- Capture version_one_hlc exactly, then execute and commit:
--   UPDATE {{RUN_PREFIX}}_aost_items
--   SET payload = 'synthetic version two', version_number = 2
--   WHERE item_id = '{{RUN_PREFIX}}_history_row';
--
-- After the update:
-- * a current read must return version two;
-- * a separately rendered, safely quoted
--     SELECT ... AS OF SYSTEM TIME '<captured-hlc>'
--   must return version one;
-- * repeat the historical read and require the same result;
-- * attempt a safely bounded invalid/future historical read and record its
--   deterministic rejection.
--
-- HARNESS BOUNDARY:
-- Record the MVCC GC-window dependency. AOST is bounded historical access and
-- does not replace durable audit snapshots.
