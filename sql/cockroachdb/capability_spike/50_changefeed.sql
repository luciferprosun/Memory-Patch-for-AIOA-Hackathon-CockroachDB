-- Memory Patch Step 3 — disposable sinkless changefeed setup.
--
-- The streaming statement is intentionally launched by the harness in its own
-- bounded process. It must use the current syntax verified for the pinned
-- runtime, capture only synthetic rows, and be terminated before cleanup.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_changefeed_items CASCADE;

CREATE TABLE {{RUN_PREFIX}}_changefeed_items (
  item_id STRING PRIMARY KEY,
  tenant_id STRING NOT NULL,
  payload STRING NOT NULL,
  revision INT8 NOT NULL
);

INSERT INTO {{RUN_PREFIX}}_changefeed_items
  (item_id, tenant_id, payload, revision)
VALUES
  ('{{RUN_PREFIX}}_initial', 'tenant-a', 'synthetic initial scan row', 1);

SHOW CLUSTER SETTING kv.rangefeed.enabled;
SHOW CREATE TABLE {{RUN_PREFIX}}_changefeed_items;

-- HARNESS STREAM:
-- 1. Record the original kv.rangefeed.enabled value.
-- 2. Enable it only on the disposable local runtime if the pinned release
--    requires that prerequisite.
-- 3. Start the currently supported sinkless changefeed form for
--    {{RUN_PREFIX}}_changefeed_items in a dedicated bounded process, requesting
--    an initial scan and resolved timestamps where supported.
-- 4. In a separate session execute deterministic INSERT, UPDATE, and DELETE:
--      INSERT item_id='{{RUN_PREFIX}}_inserted', tenant_id='tenant-a', revision=1;
--      UPDATE that row to payload='synthetic updated row', revision=2;
--      DELETE that row.
-- 5. Require real initial/insert/update/delete evidence where the selected
--    output envelope exposes those events.
-- 6. Terminate and reap the exact stream process; never leave it running.
-- 7. Restore kv.rangefeed.enabled to its recorded original value in finally.
--
-- HARNESS RLS LIMITATION:
-- In a separate focused negative probe, attempt the verified changefeed form
-- against {{RUN_PREFIX}}_rls_items from 30_rls.sql. Record the live rejection or
-- unfiltered administrative behavior together with official limitations.
-- Never treat changefeed transport as tenant-filtered authorization.
--
-- HARNESS EXTERNAL SINK:
-- Kafka, cloud storage, and other external sinks are outside this local spike
-- and must be classified DEFER rather than simulated.
