-- Memory Patch Step 3 — disposable CockroachDB runtime identity probe.
--
-- The harness replaces each declared identifier placeholder only after
-- validating it as a generated SQL identifier. This file contains no
-- credentials or production object names.

SELECT version() AS server_version;
SHOW CLUSTER SETTING version;
SHOW default_transaction_isolation;
SHOW transaction_isolation;

-- The harness compares all identity values with the immutable pin here and
-- refuses to create any probe object on a mismatch.
CREATE DATABASE {{DATABASE}};
USE {{DATABASE}};

SELECT
  current_database() AS database_name,
  current_user AS session_user,
  now() AS observed_at;

-- HARNESS IDENTITY:
-- * compare the client build tag, SELECT version(), and cluster version with
--   the immutable version pin before any later SQL file is executed;
-- * record the finalized cluster version separately from the executable build;
-- * record the loopback-only listener and sanitized start command outside SQL;
-- * abort the live run on any patch-version mismatch.
