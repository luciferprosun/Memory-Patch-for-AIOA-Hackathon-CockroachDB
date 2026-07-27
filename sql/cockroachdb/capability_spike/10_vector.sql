-- Memory Patch Step 3 — disposable VECTOR and vector-index probes.
--
-- The vector index is deliberately created while the table is empty. Prefix
-- columns are query-planning dimensions only; they are not authorization.

USE {{DATABASE}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_vector_items CASCADE;

CREATE TABLE {{RUN_PREFIX}}_vector_items (
  item_id STRING PRIMARY KEY,
  tenant_id STRING NOT NULL,
  hat_id STRING NOT NULL,
  embedding VECTOR(3) NOT NULL
);

CREATE VECTOR INDEX {{RUN_PREFIX}}_vector_l2_idx
  ON {{RUN_PREFIX}}_vector_items (
    tenant_id,
    hat_id,
    embedding vector_l2_ops
  );

INSERT INTO {{RUN_PREFIX}}_vector_items
  (item_id, tenant_id, hat_id, embedding)
VALUES
  ('{{RUN_PREFIX}}_a_origin', 'tenant-a', 'hat-alpha', '[0,0,0]'),
  ('{{RUN_PREFIX}}_a_x_near', 'tenant-a', 'hat-alpha', '[1,0,0]'),
  ('{{RUN_PREFIX}}_a_y_far', 'tenant-a', 'hat-alpha', '[0,2,0]'),
  ('{{RUN_PREFIX}}_a_diagonal', 'tenant-a', 'hat-alpha', '[1,1,0]'),
  ('{{RUN_PREFIX}}_a_other_hat', 'tenant-a', 'hat-beta', '[0.1,0,0]'),
  ('{{RUN_PREFIX}}_b_other_tenant', 'tenant-b', 'hat-alpha', '[0.1,0,0]');

SELECT item_id, tenant_id, hat_id, embedding
FROM {{RUN_PREFIX}}_vector_items
ORDER BY item_id;

SELECT
  item_id,
  embedding <-> '[0,0,0]'::VECTOR(3) AS euclidean_distance
FROM {{RUN_PREFIX}}_vector_items
WHERE tenant_id = 'tenant-a' AND hat_id = 'hat-alpha'
ORDER BY euclidean_distance, item_id;

SELECT
  item_id,
  embedding <=> '[1,0,0]'::VECTOR(3) AS cosine_distance
FROM {{RUN_PREFIX}}_vector_items
WHERE
  tenant_id = 'tenant-a'
  AND hat_id = 'hat-alpha'
  AND item_id != '{{RUN_PREFIX}}_a_origin'
ORDER BY cosine_distance, item_id;

SELECT
  item_id,
  embedding <#> '[1,0,0]'::VECTOR(3) AS negative_inner_product
FROM {{RUN_PREFIX}}_vector_items
WHERE tenant_id = 'tenant-a' AND hat_id = 'hat-alpha'
ORDER BY negative_inner_product, item_id;

EXPLAIN (OPT, VERBOSE)
SELECT item_id
FROM {{RUN_PREFIX}}_vector_items
WHERE tenant_id = 'tenant-a' AND hat_id = 'hat-alpha'
ORDER BY embedding <-> '[0,0,0]'::VECTOR(3), item_id
LIMIT 3;

SHOW INDEXES FROM {{RUN_PREFIX}}_vector_items;

-- HARNESS SETTING:
-- Inspect any vector-index feature setting first. On the disposable local
-- runtime only, record its original value, enable it when the pinned release
-- requires that prerequisite, and restore the exact original value in finally.
--
-- HARNESS NEGATIVE:
-- * insert a two-dimensional vector into embedding and require deterministic
--   dimension-mismatch rejection;
-- * insert malformed vector text and require deterministic rejection;
-- * execute the same nearest-neighbour query without tenant/HAT predicates to
--   prove that prefix columns do not enforce authorization.
--
-- HARNESS PLAN:
-- Record index existence, index eligibility, optimizer selection, and result
-- correctness as separate observations. Never force a hint to claim normal
-- optimizer selection.
