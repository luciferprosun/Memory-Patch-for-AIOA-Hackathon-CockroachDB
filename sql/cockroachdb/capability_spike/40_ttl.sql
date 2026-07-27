-- Memory Patch Step 3 — disposable Row-Level TTL metadata/execution probe.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_ttl_items CASCADE;

CREATE TABLE {{RUN_PREFIX}}_ttl_items (
  item_id STRING PRIMARY KEY,
  payload STRING NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
) WITH (
  ttl_expiration_expression = 'expires_at',
  ttl_job_cron = '* * * * *'
);

INSERT INTO {{RUN_PREFIX}}_ttl_items (item_id, payload, expires_at)
VALUES
  (
    '{{RUN_PREFIX}}_expired',
    'synthetic expired payload',
    now() - INTERVAL '1 hour'
  ),
  (
    '{{RUN_PREFIX}}_retained',
    'synthetic retained payload',
    now() + INTERVAL '1 day'
  );

SHOW CREATE TABLE {{RUN_PREFIX}}_ttl_items;

SHOW SCHEDULES;

SELECT item_id, payload, expires_at
FROM {{RUN_PREFIX}}_ttl_items
ORDER BY item_id;

-- HARNESS BOUNDED EXECUTION:
-- Metadata registration and physical deletion are distinct matrix rows.
-- On the disposable local runtime only, the harness may set a short valid
-- table-local ttl_job_cron, wait no more than 120 seconds, and assert that
-- `expired` disappears while `retained` remains. If safe deterministic
-- deletion is not observed, physical execution is DEFER, never a metadata PASS.
--
-- HARNESS INTERACTION:
-- When practical, keep the bounded changefeed open while TTL removes `expired`
-- and record whether that system-generated delete is emitted. Otherwise record
-- TTL/changefeed interaction as DEFER with the exact limitation.
