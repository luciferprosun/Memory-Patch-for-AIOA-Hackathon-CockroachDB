-- Memory Patch Step 3 — disposable partial unique-index probe.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_partial_unique_items CASCADE;

CREATE TABLE {{RUN_PREFIX}}_partial_unique_items (
  record_id STRING PRIMARY KEY,
  tenant_id STRING NOT NULL,
  logical_key STRING NOT NULL,
  is_current BOOL NOT NULL,
  payload STRING NOT NULL
);

CREATE UNIQUE INDEX {{RUN_PREFIX}}_one_current_key_idx
  ON {{RUN_PREFIX}}_partial_unique_items (tenant_id, logical_key)
  WHERE is_current;

INSERT INTO {{RUN_PREFIX}}_partial_unique_items
  (record_id, tenant_id, logical_key, is_current, payload)
VALUES
  ('{{RUN_PREFIX}}_a_current', 'tenant-a', 'logical-1', true, 'current A'),
  ('{{RUN_PREFIX}}_a_history_1', 'tenant-a', 'logical-1', false, 'history A1'),
  ('{{RUN_PREFIX}}_a_history_2', 'tenant-a', 'logical-1', false, 'history A2'),
  ('{{RUN_PREFIX}}_b_current', 'tenant-b', 'logical-1', true, 'current B');

SELECT record_id, tenant_id, logical_key, is_current, payload
FROM {{RUN_PREFIX}}_partial_unique_items
WHERE tenant_id = 'tenant-a' AND logical_key = 'logical-1'
ORDER BY is_current DESC, record_id;

SHOW INDEXES FROM {{RUN_PREFIX}}_partial_unique_items;
SHOW CREATE TABLE {{RUN_PREFIX}}_partial_unique_items;

-- HARNESS NEGATIVE:
-- Insert a second `(tenant-a, logical-1, true)` row separately. Require
-- SQLSTATE 23505 and verify that the failed statement leaves exactly one
-- current row. A duplicate outside the predicate and the same logical key in a
-- different tenant must remain allowed.
--
-- HARNESS RETRY BOUNDARY:
-- Record 23505 as a permanent uniqueness result. It is not SQLSTATE 40001 and
-- must not be blindly routed through serialization retry logic.
