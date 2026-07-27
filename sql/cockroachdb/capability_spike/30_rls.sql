-- Memory Patch Step 3 — disposable Row-Level Security and FORCE RLS probes.
--
-- Role placeholders are unique, harness-generated SQL identifiers. Their
-- rendered names are also used as synthetic tenant values.

USE {{DATABASE}};

CREATE ROLE {{ROLE_OWNER}};
CREATE ROLE {{ROLE_TENANT_A}};
CREATE ROLE {{ROLE_TENANT_B}};
CREATE ROLE {{ROLE_APP}};

DROP TABLE IF EXISTS {{RUN_PREFIX}}_rls_items CASCADE;

CREATE TABLE {{RUN_PREFIX}}_rls_items (
  item_id STRING PRIMARY KEY,
  tenant_id STRING NOT NULL,
  payload STRING NOT NULL
);

ALTER TABLE {{RUN_PREFIX}}_rls_items OWNER TO {{ROLE_OWNER}};

GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLE {{RUN_PREFIX}}_rls_items
  TO {{ROLE_TENANT_A}}, {{ROLE_TENANT_B}}, {{ROLE_APP}};

INSERT INTO {{RUN_PREFIX}}_rls_items (item_id, tenant_id, payload)
VALUES
  ('{{RUN_PREFIX}}_row_a', '{{ROLE_TENANT_A}}', 'synthetic tenant A payload'),
  ('{{RUN_PREFIX}}_row_b', '{{ROLE_TENANT_B}}', 'synthetic tenant B payload');

ALTER TABLE {{RUN_PREFIX}}_rls_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY {{RUN_PREFIX}}_tenant_policy
  ON {{RUN_PREFIX}}_rls_items
  FOR ALL
  TO {{ROLE_TENANT_A}}, {{ROLE_TENANT_B}}
  USING (tenant_id = current_user())
  WITH CHECK (tenant_id = current_user());

SELECT policyname, permissive, roles, cmd, qual, with_check
FROM pg_catalog.pg_policies
WHERE
  tablename = '{{RUN_PREFIX}}_rls_items'
  AND policyname = '{{RUN_PREFIX}}_tenant_policy';

SELECT
  c.relname,
  c.relrowsecurity,
  c.relforcerowsecurity
FROM pg_catalog.pg_class AS c
WHERE c.relname = '{{RUN_PREFIX}}_rls_items';

SHOW CREATE TABLE {{RUN_PREFIX}}_rls_items;

-- HARNESS ROLE SESSIONS:
-- Use bounded SET ROLE/RESET ROLE blocks or separate non-admin sessions.
-- Tenant A must see only row-a; Tenant B must see only row-b. ROLE_APP has DML
-- grants but no policy and therefore must not obtain cross-tenant visibility.
--
-- HARNESS NEGATIVE:
-- * Tenant A INSERT carrying Tenant B's tenant_id must be rejected by WITH
--   CHECK;
-- * Tenant A UPDATE changing row-a into Tenant B scope must be rejected;
-- * Tenant A DELETE targeting row-b must affect zero rows;
-- * ordinary ROLE_APP must not bypass policy;
-- * root/admin observations must be recorded separately and must not be used as
--   evidence for application-role isolation.
--
-- HARNESS FORCE RLS:
-- 1. SET ROLE {{ROLE_OWNER}} and record owner visibility before FORCE.
-- 2. RESET ROLE, then execute:
--      ALTER TABLE {{RUN_PREFIX}}_rls_items FORCE ROW LEVEL SECURITY;
-- 3. SET ROLE {{ROLE_OWNER}} again and record the changed owner behavior.
-- 4. Confirm both tenant-role policies still behave identically.
-- 5. RESET ROLE and record relforcerowsecurity from pg_catalog.pg_class.
-- 6. Leave FORCE enabled for the focused changefeed/RLS limitation probe; the
--    exact database is removed by 90_cleanup.sql.
--
-- HARNESS ADMIN BOUNDARY:
-- Record table owner, FORCE RLS, root/admin, BYPASSRLS-related catalog state,
-- TRUNCATE, backup/restore, and replication limitations as separate boundaries.
-- This synthetic policy is not a production Memory Patch policy.
