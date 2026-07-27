-- Memory Patch Step 3 — exact disposable SQL-resource cleanup.
--
-- Before this file runs, the harness must terminate and reap the exact
-- sinkless-changefeed process and close all probe sessions. It must restore
-- every changed cluster setting from its recorded original value; no wildcard
-- setting reset is permitted.

RESET ROLE;
USE defaultdb;

DROP DATABASE IF EXISTS {{DATABASE}} CASCADE;

DROP ROLE IF EXISTS {{ROLE_APP}};
DROP ROLE IF EXISTS {{ROLE_TENANT_B}};
DROP ROLE IF EXISTS {{ROLE_TENANT_A}};
DROP ROLE IF EXISTS {{ROLE_OWNER}};

SELECT rolname
FROM pg_catalog.pg_roles
WHERE rolname IN (
  '{{ROLE_APP}}',
  '{{ROLE_TENANT_B}}',
  '{{ROLE_TENANT_A}}',
  '{{ROLE_OWNER}}'
)
ORDER BY rolname;

-- HARNESS CLEANUP VERIFICATION:
-- * the role query above must return zero rows;
-- * {{DATABASE}} must be absent from SHOW DATABASES;
-- * the TTL job must be absent because its owning disposable database/table is
--   gone;
-- * no changefeed process/session may remain;
-- * restored setting values must equal their captured originals;
-- * stop only the exact local server PID owned by this run;
-- * verify all three loopback ports closed and delete only the exact owned temporary
--   store.
--
-- Never use wildcard role/database deletion, pkill, killall, or broad path
-- removal.
