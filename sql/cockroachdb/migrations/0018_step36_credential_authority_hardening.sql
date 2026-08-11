-- Memory Patch Step 36 - database capability separation and fail-closed
-- authority composition. This migration adds no credentials, secret values,
-- external execution, later-roadmap test harnesses, or Step 37 behavior.

-- STEP36_CLUSTER_ROLE_DDL_BEGIN
CREATE ROLE IF NOT EXISTS mp_source_publication_worker;
CREATE ROLE IF NOT EXISTS mp_audit_reader;

ALTER ROLE mp_source_publication_worker
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_audit_reader
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;

-- Reassert every privileged runtime capability as non-login and non-admin.
ALTER ROLE mp_app_runtime
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_personal_memory_commit_helper
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_human_reviewer
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_review_service
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;

REVOKE admin FROM mp_source_publication_worker;
REVOKE admin FROM mp_audit_reader;
REVOKE admin FROM mp_app_runtime;
REVOKE admin FROM mp_personal_memory_commit_helper;
REVOKE admin FROM mp_human_reviewer;
REVOKE admin FROM mp_review_service;

-- Capability roles never inherit owner, admin, or another runtime capability.
REVOKE mp_schema_owner FROM mp_source_publication_worker;
REVOKE mp_security_owner FROM mp_source_publication_worker;
REVOKE mp_app_runtime FROM mp_source_publication_worker;
REVOKE mp_personal_memory_commit_helper FROM mp_source_publication_worker;
REVOKE mp_human_reviewer FROM mp_source_publication_worker;
REVOKE mp_review_service FROM mp_source_publication_worker;
REVOKE mp_audit_reader FROM mp_source_publication_worker;

REVOKE mp_schema_owner FROM mp_audit_reader;
REVOKE mp_security_owner FROM mp_audit_reader;
REVOKE mp_app_runtime FROM mp_audit_reader;
REVOKE mp_personal_memory_commit_helper FROM mp_audit_reader;
REVOKE mp_human_reviewer FROM mp_audit_reader;
REVOKE mp_review_service FROM mp_audit_reader;
REVOKE mp_source_publication_worker FROM mp_audit_reader;

REVOKE mp_source_publication_worker FROM mp_app_runtime;
REVOKE mp_source_publication_worker FROM mp_personal_memory_commit_helper;
REVOKE mp_source_publication_worker FROM mp_human_reviewer;
REVOKE mp_source_publication_worker FROM mp_review_service;
REVOKE mp_source_publication_worker FROM mp_request_context_setter;
REVOKE mp_audit_reader FROM mp_app_runtime;
REVOKE mp_audit_reader FROM mp_personal_memory_commit_helper;
REVOKE mp_audit_reader FROM mp_human_reviewer;
REVOKE mp_audit_reader FROM mp_review_service;
REVOKE mp_audit_reader FROM mp_request_context_setter;
-- STEP36_CLUSTER_ROLE_DDL_END

-- A dedicated publication LOGIN may additionally inherit the trusted context
-- setter, but it must not inherit any other runtime or owner capability.
CREATE OR REPLACE FUNCTION memory_patch.step36_source_publisher_authorized()
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_source_publication_worker', 'MEMBER'
         )
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_app_runtime', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_personal_memory_commit_helper', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_human_reviewer', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_review_service', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_audit_reader', 'MEMBER'
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step36_audit_reader_authorized()
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_audit_reader', 'MEMBER'
         )
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_app_runtime', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_personal_memory_commit_helper', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_human_reviewer', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_review_service', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_source_publication_worker', 'MEMBER'
    )
$$;

-- A mistakenly composed app/helper LOGIN must not pass the Step 30 capability
-- predicate merely because it is also a member of the helper role.
CREATE OR REPLACE FUNCTION memory_patch.step30_commit_helper_authorized()
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_personal_memory_commit_helper', 'MEMBER'
         )
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_app_runtime', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_human_reviewer', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_review_service', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_source_publication_worker', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_audit_reader', 'MEMBER'
    )
$$;

-- Preserve Step 34 authorization data checks while rejecting a LOGIN that
-- combines reviewer authority with an unrelated runtime capability.
CREATE OR REPLACE FUNCTION memory_patch.step34_reviewer_authorized(
  p_tenant_id STRING,
  p_case_type STRING,
  p_owner_user_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_human_reviewer', 'MEMBER'
         )
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_app_runtime', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_personal_memory_commit_helper', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_review_service', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_source_publication_worker', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_audit_reader', 'MEMBER'
    )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.reviewer_authorizations AS review_auth
       WHERE review_auth.tenant_id = p_tenant_id
         AND review_auth.case_type = p_case_type
         AND review_auth.active
         AND (
           review_auth.owner_user_id IS NULL
           OR review_auth.owner_user_id = p_owner_user_id
         )
         AND memory_patch.user_context_matches(
           review_auth.tenant_id, review_auth.reviewer_id
         )
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step34_reviewer_owner_authorized(
  p_tenant_id STRING,
  p_owner_user_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_human_reviewer', 'MEMBER'
         )
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_app_runtime', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_personal_memory_commit_helper', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_review_service', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_source_publication_worker', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_audit_reader', 'MEMBER'
    )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.reviewer_authorizations AS review_auth
       WHERE review_auth.tenant_id = p_tenant_id
         AND review_auth.active
         AND (
           review_auth.owner_user_id IS NULL
           OR review_auth.owner_user_id = p_owner_user_id
         )
         AND memory_patch.user_context_matches(
           review_auth.tenant_id, review_auth.reviewer_id
         )
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step34_review_service_authorized(
  p_tenant_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_review_service', 'MEMBER'
         )
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_app_runtime', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_personal_memory_commit_helper', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_human_reviewer', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_source_publication_worker', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_audit_reader', 'MEMBER'
    )
    AND memory_patch.tenant_context_matches(p_tenant_id)
$$;

-- Both the table grant and RLS policy are narrowed below. These triggers are
-- an independent guard against a future accidentally composed LOGIN.
CREATE OR REPLACE FUNCTION
  memory_patch.guard_step36_source_publication_authority()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF NOT memory_patch.step36_source_publisher_authorized() THEN
    RAISE EXCEPTION 'dedicated source publication authority required'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER source_registry_entries_s36_publication_authority
BEFORE UPDATE ON memory_patch.source_registry_entries
FOR EACH ROW
EXECUTE FUNCTION memory_patch.guard_step36_source_publication_authority();

CREATE TRIGGER source_publication_events_s36_authority
BEFORE INSERT ON memory_patch.source_publication_events
FOR EACH ROW
EXECUTE FUNCTION memory_patch.guard_step36_source_publication_authority();

ALTER FUNCTION memory_patch.step36_source_publisher_authorized()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step36_audit_reader_authorized()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_commit_helper_authorized()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_reviewer_authorized(
  STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_reviewer_owner_authorized(
  STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_review_service_authorized(STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step36_source_publication_authority()
  OWNER TO mp_schema_owner;

REVOKE ALL ON FUNCTION
  memory_patch.step36_source_publisher_authorized(),
  memory_patch.step36_audit_reader_authorized(),
  memory_patch.guard_step36_source_publication_authority()
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper, mp_human_reviewer, mp_review_service,
  mp_source_publication_worker, mp_audit_reader;
GRANT EXECUTE ON FUNCTION
  memory_patch.step36_source_publisher_authorized(),
  memory_patch.guard_step36_source_publication_authority()
TO mp_source_publication_worker;
GRANT EXECUTE ON FUNCTION memory_patch.step36_audit_reader_authorized()
TO mp_audit_reader;

-- Preserve Step 32's app-policy evaluation grant while the strengthened
-- predicate still returns false for the app role.
REVOKE ALL ON FUNCTION memory_patch.step30_commit_helper_authorized()
FROM PUBLIC, mp_request_context_setter, mp_human_reviewer, mp_review_service,
  mp_source_publication_worker, mp_audit_reader;
GRANT EXECUTE ON FUNCTION memory_patch.step30_commit_helper_authorized()
TO mp_app_runtime, mp_personal_memory_commit_helper;

REVOKE ALL ON FUNCTION
  memory_patch.step34_reviewer_authorized(STRING, STRING, STRING),
  memory_patch.step34_reviewer_owner_authorized(STRING, STRING),
  memory_patch.step34_review_service_authorized(STRING)
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper, mp_human_reviewer, mp_review_service,
  mp_source_publication_worker, mp_audit_reader;
GRANT EXECUTE ON FUNCTION
  memory_patch.step34_reviewer_authorized(STRING, STRING, STRING),
  memory_patch.step34_reviewer_owner_authorized(STRING, STRING)
TO mp_human_reviewer;
GRANT EXECUTE ON FUNCTION
  memory_patch.step34_review_service_authorized(STRING)
TO mp_review_service;

-- Source registration stays with the normal runtime. Only immutable-event
-- publication transitions move to the dedicated publisher capability.
REVOKE UPDATE ON TABLE memory_patch.source_registry_entries
FROM mp_app_runtime;
REVOKE INSERT ON TABLE memory_patch.source_publication_events
FROM mp_app_runtime;

GRANT USAGE ON SCHEMA memory_patch TO mp_source_publication_worker;
GRANT SELECT ON TABLE memory_patch.hat_scopes
TO mp_source_publication_worker;
GRANT SELECT ON TABLE memory_patch.source_provenance_edges
TO mp_source_publication_worker;
GRANT SELECT, UPDATE ON TABLE memory_patch.source_registry_entries
TO mp_source_publication_worker;
GRANT SELECT, INSERT ON TABLE memory_patch.source_publication_events
TO mp_source_publication_worker;
GRANT SELECT, INSERT, UPDATE ON TABLE memory_patch.persistence_operations
TO mp_source_publication_worker;

GRANT EXECUTE ON FUNCTION
  memory_patch.tenant_context_matches(STRING),
  memory_patch.user_context_matches(STRING, STRING),
  memory_patch.scope_context_matches(STRING, STRING, STRING),
  memory_patch.hat_scope_context_matches(STRING, STRING)
TO mp_source_publication_worker;

CREATE POLICY hat_scopes_s36_source_publication_select
  ON memory_patch.hat_scopes
  FOR SELECT TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.scope_context_matches(
      tenant_id, target_scope, owner_user_id
    )
  );

CREATE POLICY source_registry_entries_s36_publication_select
  ON memory_patch.source_registry_entries
  FOR SELECT TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_provenance_edges_s36_publication_select
  ON memory_patch.source_provenance_edges
  FOR SELECT TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_registry_entries_s36_publication_update
  ON memory_patch.source_registry_entries
  FOR UPDATE TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  )
  WITH CHECK (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_publication_events_s36_publication_select
  ON memory_patch.source_publication_events
  FOR SELECT TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_publication_events_s36_publication_insert
  ON memory_patch.source_publication_events
  FOR INSERT TO mp_source_publication_worker
  WITH CHECK (
    memory_patch.step36_source_publisher_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY persistence_operations_s36_publication_select
  ON memory_patch.persistence_operations
  FOR SELECT TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND operation_kind = 'PUBLICATION_STATE_TRANSITION'
    AND CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  );

CREATE POLICY persistence_operations_s36_publication_insert
  ON memory_patch.persistence_operations
  FOR INSERT TO mp_source_publication_worker
  WITH CHECK (
    memory_patch.step36_source_publisher_authorized()
    AND operation_kind = 'PUBLICATION_STATE_TRANSITION'
    AND CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  );

CREATE POLICY persistence_operations_s36_publication_update
  ON memory_patch.persistence_operations
  FOR UPDATE TO mp_source_publication_worker
  USING (
    memory_patch.step36_source_publisher_authorized()
    AND operation_kind = 'PUBLICATION_STATE_TRANSITION'
    AND CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  )
  WITH CHECK (
    memory_patch.step36_source_publisher_authorized()
    AND operation_kind = 'PUBLICATION_STATE_TRANSITION'
    AND CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  );

-- Step 33 append remains bound to the exact business principal that performs
-- the atomic operation. Export and verification receive a distinct read-only
-- owner-private capability.
GRANT USAGE ON SCHEMA memory_patch TO mp_audit_reader;
GRANT SELECT ON TABLE
  memory_patch.audit_events,
  memory_patch.audit_chain_heads
TO mp_audit_reader;
GRANT EXECUTE ON FUNCTION
  memory_patch.tenant_context_matches(STRING),
  memory_patch.user_context_matches(STRING, STRING)
TO mp_audit_reader;

CREATE POLICY audit_events_s36_reader_select
  ON memory_patch.audit_events
  FOR SELECT TO mp_audit_reader
  USING (
    memory_patch.step36_audit_reader_authorized()
    AND user_id IS NOT NULL
    AND memory_patch.user_context_matches(tenant_id, user_id)
  );

CREATE POLICY audit_chain_heads_s36_reader_select
  ON memory_patch.audit_chain_heads
  FOR SELECT TO mp_audit_reader
  USING (
    memory_patch.step36_audit_reader_authorized()
    AND owner_user_id IS NOT NULL
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  );

REVOKE INSERT, UPDATE, DELETE ON TABLE memory_patch.audit_events
FROM mp_audit_reader;
REVOKE INSERT, UPDATE, DELETE ON TABLE memory_patch.audit_chain_heads
FROM mp_audit_reader;
