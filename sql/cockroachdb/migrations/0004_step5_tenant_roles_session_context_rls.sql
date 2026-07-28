-- Memory Patch Step 5 — SQL principals, transaction-bound request context,
-- least-privilege grants, and fail-closed tenant/user row isolation.
--
-- The four fixed roles are cluster-scoped NOLOGIN roles. A trusted application
-- boundary may receive both mp_app_runtime and mp_request_context_setter
-- membership through a separately authenticated login. Models, HATs, and
-- providers never receive SQL principals.

-- STEP5_CLUSTER_ROLE_DDL_BEGIN
CREATE ROLE IF NOT EXISTS mp_schema_owner;
CREATE ROLE IF NOT EXISTS mp_security_owner;
CREATE ROLE IF NOT EXISTS mp_app_runtime;
CREATE ROLE IF NOT EXISTS mp_request_context_setter;

ALTER ROLE mp_schema_owner
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_security_owner
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_app_runtime
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_request_context_setter
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;

REVOKE admin FROM mp_schema_owner;
REVOKE admin FROM mp_security_owner;
REVOKE admin FROM mp_app_runtime;
REVOKE admin FROM mp_request_context_setter;
REVOKE mp_schema_owner FROM mp_app_runtime;
REVOKE mp_security_owner FROM mp_app_runtime;
REVOKE mp_request_context_setter FROM mp_app_runtime;
REVOKE mp_schema_owner FROM mp_request_context_setter;
REVOKE mp_security_owner FROM mp_request_context_setter;
-- STEP5_CLUSTER_ROLE_DDL_END

-- STEP5_DATABASE_PHASE_1_BEGIN
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA memory_patch FROM PUBLIC;
REVOKE CREATE ON SCHEMA memory_patch
  FROM mp_app_runtime, mp_request_context_setter;
ALTER SCHEMA memory_patch OWNER TO mp_schema_owner;
GRANT CREATE ON SCHEMA memory_patch TO mp_security_owner;
GRANT USAGE ON SCHEMA memory_patch
  TO mp_app_runtime, mp_request_context_setter;

CREATE TABLE IF NOT EXISTS memory_patch.request_contexts (
  database_principal STRING NOT NULL,
  backend_pid INT8 NOT NULL,
  transaction_started_at TIMESTAMPTZ NOT NULL,
  tenant_id STRING NOT NULL,
  user_id STRING,
  access_mode STRING NOT NULL,
  context_set_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (database_principal, backend_pid),
  CONSTRAINT request_contexts_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id)
    ON DELETE RESTRICT,
  CONSTRAINT request_contexts_user_fk
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT request_contexts_principal_not_blank
    CHECK (btrim(database_principal) <> ''),
  CONSTRAINT request_contexts_backend_pid_positive
    CHECK (backend_pid > 0),
  CONSTRAINT request_contexts_tenant_not_blank
    CHECK (btrim(tenant_id) <> ''),
  CONSTRAINT request_contexts_exact_mode
    CHECK (
      (
        access_mode = 'TENANT_SHARED'
        AND user_id IS NULL
      )
      OR
      (
        access_mode = 'USER_PRIVATE'
        AND user_id IS NOT NULL
        AND btrim(user_id) <> ''
      )
    )
);

CREATE OR REPLACE FUNCTION memory_patch.set_request_context(
  p_tenant_id STRING,
  p_user_id STRING,
  p_access_mode STRING
)
RETURNS BOOL
LANGUAGE PLpgSQL
SECURITY DEFINER
AS $$
BEGIN
  IF NOT pg_catalog.pg_has_role(
    session_user,
    'mp_request_context_setter',
    'MEMBER'
  ) THEN
    RAISE EXCEPTION 'trusted request-context setter required'
      USING ERRCODE = '42501';
  END IF;

  IF p_tenant_id IS NULL OR pg_catalog.btrim(p_tenant_id) = '' THEN
    RAISE EXCEPTION 'non-empty tenant context required'
      USING ERRCODE = '22023';
  END IF;

  IF p_access_mode = 'TENANT_SHARED' THEN
    IF p_user_id IS NOT NULL THEN
      RAISE EXCEPTION 'tenant-shared context must not carry a user'
        USING ERRCODE = '22023';
    END IF;
  ELSIF p_access_mode = 'USER_PRIVATE' THEN
    IF p_user_id IS NULL OR pg_catalog.btrim(p_user_id) = '' THEN
      RAISE EXCEPTION 'user-private context requires a non-empty user'
        USING ERRCODE = '22023';
    END IF;
  ELSE
    RAISE EXCEPTION 'unsupported request-context access mode'
      USING ERRCODE = '22023';
  END IF;

  DELETE FROM memory_patch.request_contexts
   WHERE database_principal = session_user
     AND backend_pid = pg_catalog.pg_backend_pid();

  INSERT INTO memory_patch.request_contexts (
    database_principal,
    backend_pid,
    transaction_started_at,
    tenant_id,
    user_id,
    access_mode,
    context_set_at
  )
  VALUES (
    session_user,
    pg_catalog.pg_backend_pid(),
    pg_catalog.transaction_timestamp(),
    p_tenant_id,
    p_user_id,
    p_access_mode,
    pg_catalog.statement_timestamp()
  );

  RETURN true;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.clear_request_context()
RETURNS BOOL
LANGUAGE PLpgSQL
SECURITY DEFINER
AS $$
BEGIN
  IF NOT pg_catalog.pg_has_role(
    session_user,
    'mp_request_context_setter',
    'MEMBER'
  ) THEN
    RAISE EXCEPTION 'trusted request-context setter required'
      USING ERRCODE = '42501';
  END IF;

  DELETE FROM memory_patch.request_contexts
   WHERE database_principal = session_user
     AND backend_pid = pg_catalog.pg_backend_pid();

  RETURN true;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.tenant_context_matches(
  p_tenant_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY DEFINER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.request_contexts AS request_context
     WHERE request_context.database_principal = session_user
       AND request_context.backend_pid = pg_catalog.pg_backend_pid()
       AND request_context.transaction_started_at
         = pg_catalog.transaction_timestamp()
       AND request_context.tenant_id = p_tenant_id
       AND request_context.access_mode
         IN ('TENANT_SHARED', 'USER_PRIVATE')
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.user_context_matches(
  p_tenant_id STRING,
  p_user_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY DEFINER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.request_contexts AS request_context
     WHERE request_context.database_principal = session_user
       AND request_context.backend_pid = pg_catalog.pg_backend_pid()
       AND request_context.transaction_started_at
         = pg_catalog.transaction_timestamp()
       AND request_context.tenant_id = p_tenant_id
       AND request_context.user_id = p_user_id
       AND request_context.access_mode = 'USER_PRIVATE'
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.scope_context_matches(
  p_tenant_id STRING,
  p_target_scope STRING,
  p_owner_user_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT CASE
    WHEN p_target_scope = 'SHARED_KNOWLEDGE_HAT'
      AND p_owner_user_id IS NULL
    THEN memory_patch.tenant_context_matches(p_tenant_id)
    WHEN p_target_scope = 'USER_PERSONAL_HAT'
      AND p_owner_user_id IS NOT NULL
    THEN memory_patch.user_context_matches(
      p_tenant_id,
      p_owner_user_id
    )
    ELSE false
  END
$$;

CREATE OR REPLACE FUNCTION memory_patch.hat_scope_context_matches(
  p_tenant_id STRING,
  p_hat_scope_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.hat_scopes AS hat_scope
     WHERE hat_scope.tenant_id = p_tenant_id
       AND hat_scope.hat_scope_id = p_hat_scope_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.kernel_run_context_matches(
  p_tenant_id STRING,
  p_kernel_run_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.kernel_runs AS kernel_run
     WHERE kernel_run.tenant_id = p_tenant_id
       AND kernel_run.kernel_run_id = p_kernel_run_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.draft_context_matches(
  p_tenant_id STRING,
  p_draft_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.drafts AS draft
     WHERE draft.tenant_id = p_tenant_id
       AND draft.draft_id = p_draft_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.claim_context_matches(
  p_tenant_id STRING,
  p_claim_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.claims AS claim
     WHERE claim.tenant_id = p_tenant_id
       AND claim.claim_id = p_claim_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.evidence_item_context_matches(
  p_tenant_id STRING,
  p_evidence_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.evidence_items AS evidence_item
     WHERE evidence_item.tenant_id = p_tenant_id
       AND evidence_item.evidence_id = p_evidence_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.evidence_bundle_context_matches(
  p_tenant_id STRING,
  p_evidence_bundle_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.evidence_bundles AS evidence_bundle
     WHERE evidence_bundle.tenant_id = p_tenant_id
       AND evidence_bundle.evidence_bundle_id = p_evidence_bundle_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.correction_packet_context_matches(
  p_tenant_id STRING,
  p_packet_hash STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.correction_packets AS correction_packet
     WHERE correction_packet.tenant_id = p_tenant_id
       AND correction_packet.packet_hash = p_packet_hash
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.proposal_context_matches(
  p_tenant_id STRING,
  p_proposal_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.memory_patch_proposals AS proposal
     WHERE proposal.tenant_id = p_tenant_id
       AND proposal.proposal_id = p_proposal_id
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_personal_memory_space_identity()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).user_id IS DISTINCT FROM (OLD).user_id
    OR (NEW).personal_memory_space_id
      IS DISTINCT FROM (OLD).personal_memory_space_id
    OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
  THEN
    RAISE EXCEPTION 'personal memory identity columns are immutable'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_memory_item_identity()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).memory_item_id IS DISTINCT FROM (OLD).memory_item_id
    OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
    OR (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
    OR (NEW).target_scope IS DISTINCT FROM (OLD).target_scope
    OR (NEW).visibility IS DISTINCT FROM (OLD).visibility
    OR (NEW).trust_class IS DISTINCT FROM (OLD).trust_class
    OR (NEW).content_kind IS DISTINCT FROM (OLD).content_kind
    OR (NEW).content IS DISTINCT FROM (OLD).content
    OR (NEW).scope_dimensions IS DISTINCT FROM (OLD).scope_dimensions
    OR (NEW).evidence_references IS DISTINCT FROM (OLD).evidence_references
    OR (NEW).source_patch_id IS DISTINCT FROM (OLD).source_patch_id
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
  THEN
    RAISE EXCEPTION 'memory item identity and evidence columns are immutable'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$$;

-- STEP5_DATABASE_PHASE_1_END

-- STEP5_DATABASE_PHASE_2_BEGIN
ALTER TABLE memory_patch.schema_migrations
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.tenants
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.users
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.hat_manifests
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.personal_memory_spaces
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.personal_memory_model_bindings
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.hat_scopes
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.knowledge_sources
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.source_snapshots
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.knowledge_versions
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.knowledge_chunks
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.chunk_search_documents
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.kernel_runs
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.routing_decisions
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.action_policy_decisions
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.drafts
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.claims
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.evidence_items
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.evidence_bundles
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.evidence_bundle_items
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.claim_verdicts
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.correction_packets
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.correction_requirements
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.memory_patch_proposals
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.memory_patch_approvals
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.memory_patch_commits
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.patch_transition_records
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.memory_items
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.audit_events
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.request_contexts
  OWNER TO mp_security_owner;

-- STEP5_DATABASE_PHASE_2_END

-- STEP5_DATABASE_PHASE_3_BEGIN
ALTER FUNCTION memory_patch.set_request_context(STRING, STRING, STRING)
  OWNER TO mp_security_owner;
ALTER FUNCTION memory_patch.clear_request_context()
  OWNER TO mp_security_owner;
ALTER FUNCTION memory_patch.tenant_context_matches(STRING)
  OWNER TO mp_security_owner;
ALTER FUNCTION memory_patch.user_context_matches(STRING, STRING)
  OWNER TO mp_security_owner;
ALTER FUNCTION memory_patch.scope_context_matches(STRING, STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.hat_scope_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.kernel_run_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.draft_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.claim_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.evidence_item_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.evidence_bundle_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.correction_packet_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.proposal_context_matches(STRING, STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_personal_memory_space_identity()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_memory_item_identity()
  OWNER TO mp_schema_owner;

DROP TRIGGER IF EXISTS personal_memory_spaces_s5_identity_guard
  ON memory_patch.personal_memory_spaces;
CREATE TRIGGER personal_memory_spaces_s5_identity_guard
  BEFORE UPDATE ON memory_patch.personal_memory_spaces
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_personal_memory_space_identity();
DROP TRIGGER IF EXISTS memory_items_s5_identity_guard
  ON memory_patch.memory_items;
CREATE TRIGGER memory_items_s5_identity_guard
  BEFORE UPDATE ON memory_patch.memory_items
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_memory_item_identity();

-- STEP5_DATABASE_PHASE_3_END

-- STEP5_DATABASE_PHASE_4_BEGIN
REVOKE ALL ON ALL TABLES IN SCHEMA memory_patch
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA memory_patch
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memory_patch
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;

GRANT SELECT ON TABLE memory_patch.tenants, memory_patch.users
  TO mp_security_owner;

GRANT SELECT ON TABLE memory_patch.hat_manifests
  TO mp_app_runtime;

GRANT SELECT ON TABLE
  memory_patch.tenants,
  memory_patch.users,
  memory_patch.personal_memory_spaces,
  memory_patch.personal_memory_model_bindings,
  memory_patch.hat_scopes,
  memory_patch.knowledge_sources,
  memory_patch.source_snapshots,
  memory_patch.knowledge_versions,
  memory_patch.knowledge_chunks,
  memory_patch.chunk_search_documents,
  memory_patch.kernel_runs,
  memory_patch.routing_decisions,
  memory_patch.action_policy_decisions,
  memory_patch.drafts,
  memory_patch.claims,
  memory_patch.evidence_items,
  memory_patch.evidence_bundles,
  memory_patch.evidence_bundle_items,
  memory_patch.claim_verdicts,
  memory_patch.correction_packets,
  memory_patch.correction_requirements,
  memory_patch.memory_patch_proposals,
  memory_patch.memory_patch_approvals,
  memory_patch.memory_patch_commits,
  memory_patch.patch_transition_records,
  memory_patch.memory_items,
  memory_patch.audit_events
TO mp_app_runtime;

GRANT INSERT ON TABLE
  memory_patch.personal_memory_spaces,
  memory_patch.personal_memory_model_bindings,
  memory_patch.knowledge_sources,
  memory_patch.source_snapshots,
  memory_patch.knowledge_versions,
  memory_patch.knowledge_chunks,
  memory_patch.chunk_search_documents,
  memory_patch.kernel_runs,
  memory_patch.routing_decisions,
  memory_patch.action_policy_decisions,
  memory_patch.drafts,
  memory_patch.claims,
  memory_patch.evidence_items,
  memory_patch.evidence_bundles,
  memory_patch.evidence_bundle_items,
  memory_patch.claim_verdicts,
  memory_patch.correction_packets,
  memory_patch.correction_requirements,
  memory_patch.memory_items,
  memory_patch.audit_events
TO mp_app_runtime;

GRANT UPDATE ON TABLE
  memory_patch.personal_memory_spaces,
  memory_patch.memory_items
TO mp_app_runtime;

GRANT DELETE ON TABLE
  memory_patch.personal_memory_model_bindings
TO mp_app_runtime;

REVOKE ALL ON TABLE
  memory_patch.schema_migrations,
  memory_patch.request_contexts
FROM PUBLIC, mp_app_runtime, mp_request_context_setter;

REVOKE ALL ON FUNCTION
  memory_patch.set_request_context(STRING, STRING, STRING),
  memory_patch.clear_request_context(),
  memory_patch.tenant_context_matches(STRING),
  memory_patch.user_context_matches(STRING, STRING),
  memory_patch.scope_context_matches(STRING, STRING, STRING),
  memory_patch.hat_scope_context_matches(STRING, STRING),
  memory_patch.kernel_run_context_matches(STRING, STRING),
  memory_patch.draft_context_matches(STRING, STRING),
  memory_patch.claim_context_matches(STRING, STRING),
  memory_patch.evidence_item_context_matches(STRING, STRING),
  memory_patch.evidence_bundle_context_matches(STRING, STRING),
  memory_patch.correction_packet_context_matches(STRING, STRING),
  memory_patch.proposal_context_matches(STRING, STRING),
  memory_patch.guard_personal_memory_space_identity(),
  memory_patch.guard_memory_item_identity()
FROM PUBLIC, mp_app_runtime, mp_request_context_setter;

GRANT EXECUTE ON FUNCTION
  memory_patch.set_request_context(STRING, STRING, STRING),
  memory_patch.clear_request_context()
TO mp_request_context_setter;

GRANT EXECUTE ON FUNCTION
  memory_patch.tenant_context_matches(STRING),
  memory_patch.user_context_matches(STRING, STRING),
  memory_patch.scope_context_matches(STRING, STRING, STRING),
  memory_patch.hat_scope_context_matches(STRING, STRING),
  memory_patch.kernel_run_context_matches(STRING, STRING),
  memory_patch.draft_context_matches(STRING, STRING),
  memory_patch.claim_context_matches(STRING, STRING),
  memory_patch.evidence_item_context_matches(STRING, STRING),
  memory_patch.evidence_bundle_context_matches(STRING, STRING),
  memory_patch.correction_packet_context_matches(STRING, STRING),
  memory_patch.proposal_context_matches(STRING, STRING)
TO mp_app_runtime;

REVOKE CREATE ON SCHEMA memory_patch FROM mp_security_owner;

ALTER DEFAULT PRIVILEGES FOR ALL ROLES IN SCHEMA memory_patch
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ALL ROLES IN SCHEMA memory_patch
  REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ALL ROLES IN SCHEMA memory_patch
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- STEP5_DATABASE_PHASE_4_END

-- STEP5_DATABASE_PHASE_5_BEGIN
ALTER TABLE memory_patch.tenants
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.tenants
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.users
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.users
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_spaces
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_spaces
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_model_bindings
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.hat_scopes
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.hat_scopes
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.knowledge_sources
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.knowledge_sources
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_snapshots
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_snapshots
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.knowledge_versions
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.knowledge_versions
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.knowledge_chunks
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.knowledge_chunks
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.chunk_search_documents
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.chunk_search_documents
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.kernel_runs
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.kernel_runs
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.routing_decisions
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.routing_decisions
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.action_policy_decisions
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.action_policy_decisions
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.drafts
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.drafts
  FORCE ROW LEVEL SECURITY;

-- STEP5_DATABASE_PHASE_5_END

-- STEP5_DATABASE_PHASE_6_BEGIN
ALTER TABLE memory_patch.claims
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.claims
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.evidence_items
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.evidence_items
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.evidence_bundles
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.evidence_bundles
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.evidence_bundle_items
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.evidence_bundle_items
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.claim_verdicts
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.claim_verdicts
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.correction_packets
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.correction_packets
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.correction_requirements
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.correction_requirements
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_proposals
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_proposals
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_approvals
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_approvals
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_commits
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_commits
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.patch_transition_records
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.patch_transition_records
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_items
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_items
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_events
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_events
  FORCE ROW LEVEL SECURITY;

-- STEP5_DATABASE_PHASE_6_END

-- STEP5_DATABASE_PHASE_7_BEGIN
CREATE POLICY IF NOT EXISTS tenants_s5_select
  ON memory_patch.tenants
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.tenant_context_matches(tenant_id));

CREATE POLICY IF NOT EXISTS users_s5_select
  ON memory_patch.users
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, user_id));

CREATE POLICY IF NOT EXISTS personal_memory_spaces_s5_select
  ON memory_patch.personal_memory_spaces
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY IF NOT EXISTS personal_memory_spaces_s5_insert
  ON memory_patch.personal_memory_spaces
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY IF NOT EXISTS personal_memory_spaces_s5_update
  ON memory_patch.personal_memory_spaces
  FOR UPDATE
  TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, user_id))
  WITH CHECK (memory_patch.user_context_matches(tenant_id, user_id));

CREATE POLICY IF NOT EXISTS personal_memory_model_bindings_s5_select
  ON memory_patch.personal_memory_model_bindings
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY IF NOT EXISTS personal_memory_model_bindings_s5_insert
  ON memory_patch.personal_memory_model_bindings
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY IF NOT EXISTS personal_memory_model_bindings_s5_delete
  ON memory_patch.personal_memory_model_bindings
  FOR DELETE
  TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, user_id));

CREATE POLICY IF NOT EXISTS hat_scopes_s5_select
  ON memory_patch.hat_scopes
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.scope_context_matches(
      tenant_id,
      target_scope,
      owner_user_id
    )
  );

CREATE POLICY IF NOT EXISTS knowledge_sources_s5_select
  ON memory_patch.knowledge_sources
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS knowledge_sources_s5_insert
  ON memory_patch.knowledge_sources
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY IF NOT EXISTS source_snapshots_s5_select
  ON memory_patch.source_snapshots
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS source_snapshots_s5_insert
  ON memory_patch.source_snapshots
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY IF NOT EXISTS knowledge_versions_s5_select
  ON memory_patch.knowledge_versions
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS knowledge_versions_s5_insert
  ON memory_patch.knowledge_versions
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY IF NOT EXISTS knowledge_chunks_s5_select
  ON memory_patch.knowledge_chunks
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS knowledge_chunks_s5_insert
  ON memory_patch.knowledge_chunks
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY IF NOT EXISTS chunk_search_documents_s5_select
  ON memory_patch.chunk_search_documents
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS chunk_search_documents_s5_insert
  ON memory_patch.chunk_search_documents
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

-- STEP5_DATABASE_PHASE_7_END

-- STEP5_DATABASE_PHASE_8_BEGIN
CREATE POLICY IF NOT EXISTS kernel_runs_s5_select
  ON memory_patch.kernel_runs
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY IF NOT EXISTS kernel_runs_s5_insert
  ON memory_patch.kernel_runs
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (memory_patch.user_context_matches(tenant_id, user_id));

CREATE POLICY IF NOT EXISTS routing_decisions_s5_select
  ON memory_patch.routing_decisions
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND (
      selected_hat_scope_id IS NULL
      OR memory_patch.hat_scope_context_matches(
        tenant_id,
        selected_hat_scope_id
      )
    )
  );
CREATE POLICY IF NOT EXISTS routing_decisions_s5_insert
  ON memory_patch.routing_decisions
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND (
      selected_hat_scope_id IS NULL
      OR memory_patch.hat_scope_context_matches(
        tenant_id,
        selected_hat_scope_id
      )
    )
  );

CREATE POLICY IF NOT EXISTS action_policy_decisions_s5_select
  ON memory_patch.action_policy_decisions
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
  );
CREATE POLICY IF NOT EXISTS action_policy_decisions_s5_insert
  ON memory_patch.action_policy_decisions
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
  );

CREATE POLICY IF NOT EXISTS drafts_s5_select
  ON memory_patch.drafts
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
  );
CREATE POLICY IF NOT EXISTS drafts_s5_insert
  ON memory_patch.drafts
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
  );

CREATE POLICY IF NOT EXISTS claims_s5_select
  ON memory_patch.claims
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND memory_patch.draft_context_matches(tenant_id, draft_id)
  );
CREATE POLICY IF NOT EXISTS claims_s5_insert
  ON memory_patch.claims
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND memory_patch.draft_context_matches(tenant_id, draft_id)
  );

CREATE POLICY IF NOT EXISTS evidence_items_s5_select
  ON memory_patch.evidence_items
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS evidence_items_s5_insert
  ON memory_patch.evidence_items
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY IF NOT EXISTS evidence_bundles_s5_select
  ON memory_patch.evidence_bundles
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS evidence_bundles_s5_insert
  ON memory_patch.evidence_bundles
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

-- STEP5_DATABASE_PHASE_8_END

-- STEP5_DATABASE_PHASE_9_BEGIN
CREATE POLICY IF NOT EXISTS evidence_bundle_items_s5_select
  ON memory_patch.evidence_bundle_items
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.evidence_bundle_context_matches(
      tenant_id,
      evidence_bundle_id
    )
    AND memory_patch.evidence_item_context_matches(
      tenant_id,
      evidence_id
    )
  );
CREATE POLICY IF NOT EXISTS evidence_bundle_items_s5_insert
  ON memory_patch.evidence_bundle_items
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.evidence_bundle_context_matches(
      tenant_id,
      evidence_bundle_id
    )
    AND memory_patch.evidence_item_context_matches(
      tenant_id,
      evidence_id
    )
  );

CREATE POLICY IF NOT EXISTS claim_verdicts_s5_select
  ON memory_patch.claim_verdicts
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.claim_context_matches(tenant_id, claim_id));
CREATE POLICY IF NOT EXISTS claim_verdicts_s5_insert
  ON memory_patch.claim_verdicts
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (memory_patch.claim_context_matches(tenant_id, claim_id));

CREATE POLICY IF NOT EXISTS correction_packets_s5_select
  ON memory_patch.correction_packets
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND memory_patch.draft_context_matches(tenant_id, draft_v1_id)
    AND memory_patch.hat_scope_context_matches(
      tenant_id,
      selected_hat_scope_id
    )
  );
CREATE POLICY IF NOT EXISTS correction_packets_s5_insert
  ON memory_patch.correction_packets
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.kernel_run_context_matches(tenant_id, kernel_run_id)
    AND memory_patch.draft_context_matches(tenant_id, draft_v1_id)
    AND memory_patch.hat_scope_context_matches(
      tenant_id,
      selected_hat_scope_id
    )
  );

CREATE POLICY IF NOT EXISTS correction_requirements_s5_select
  ON memory_patch.correction_requirements
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.correction_packet_context_matches(
      tenant_id,
      packet_hash
    )
    AND memory_patch.claim_context_matches(tenant_id, claim_id)
  );
CREATE POLICY IF NOT EXISTS correction_requirements_s5_insert
  ON memory_patch.correction_requirements
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.correction_packet_context_matches(
      tenant_id,
      packet_hash
    )
    AND memory_patch.claim_context_matches(tenant_id, claim_id)
  );

CREATE POLICY IF NOT EXISTS memory_patch_proposals_s5_select
  ON memory_patch.memory_patch_proposals
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.scope_context_matches(
      tenant_id,
      target_scope,
      owner_user_id
    )
  );

CREATE POLICY IF NOT EXISTS memory_patch_approvals_s5_select
  ON memory_patch.memory_patch_approvals
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.scope_context_matches(
      tenant_id,
      target_scope,
      owner_user_id
    )
  );

CREATE POLICY IF NOT EXISTS memory_patch_commits_s5_select
  ON memory_patch.memory_patch_commits
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.scope_context_matches(
      tenant_id,
      target_scope,
      owner_user_id
    )
  );

CREATE POLICY IF NOT EXISTS patch_transition_records_s5_select
  ON memory_patch.patch_transition_records
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.proposal_context_matches(tenant_id, proposal_id)
  );

CREATE POLICY IF NOT EXISTS memory_items_s5_select
  ON memory_patch.memory_items
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS memory_items_s5_insert
  ON memory_patch.memory_items
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
CREATE POLICY IF NOT EXISTS memory_items_s5_update
  ON memory_patch.memory_items
  FOR UPDATE
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  )
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY IF NOT EXISTS audit_events_s5_select
  ON memory_patch.audit_events
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.tenant_context_matches(tenant_id)
    AND (
      user_id IS NULL
      OR memory_patch.user_context_matches(tenant_id, user_id)
    )
    AND (
      kernel_run_id IS NULL
      OR memory_patch.kernel_run_context_matches(
        tenant_id,
        kernel_run_id
      )
    )
  );
CREATE POLICY IF NOT EXISTS audit_events_s5_insert
  ON memory_patch.audit_events
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.tenant_context_matches(tenant_id)
    AND (
      user_id IS NULL
      OR memory_patch.user_context_matches(tenant_id, user_id)
    )
    AND (
      kernel_run_id IS NULL
      OR memory_patch.kernel_run_context_matches(
        tenant_id,
        kernel_run_id
      )
    )
  );

COMMENT ON TABLE memory_patch.request_contexts IS
  'Security-internal transaction context; no ordinary runtime table access.';
-- STEP5_DATABASE_PHASE_9_END
