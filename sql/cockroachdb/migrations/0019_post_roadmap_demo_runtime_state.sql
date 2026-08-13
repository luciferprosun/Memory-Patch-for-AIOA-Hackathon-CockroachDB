-- Post-roadmap demo runtime R4 - durable, bounded owner UI sessions.
--
-- The browser receives only an opaque random handle.  This table stores its
-- SHA-256 digest and the minimum server-side OIDC/session state.  It stores no
-- ID/access/refresh token, provider key, database credential, or helper key.

CREATE OR REPLACE FUNCTION memory_patch.r4_session_runtime_authorized()
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(session_user, 'mp_app_runtime', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(session_user, 'admin', 'MEMBER')
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_schema_owner', 'MEMBER'
    )
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_security_owner', 'MEMBER'
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
    AND NOT pg_catalog.pg_has_role(
      session_user, 'mp_audit_reader', 'MEMBER'
    )
$$;

CREATE TABLE memory_patch.owner_ui_sessions (
  session_handle_hash STRING PRIMARY KEY,
  record_kind STRING NOT NULL,
  capacity_slot INT8 NOT NULL,
  pending_slot INT8 NULL,
  owner_slot INT8 NULL,
  tenant_id STRING NULL,
  owner_user_id STRING NULL,
  oidc_subject STRING NULL,
  display_name STRING NULL,
  oidc_state STRING NULL,
  oidc_nonce STRING NULL,
  pkce_verifier STRING NULL,
  return_path STRING NULL,
  csrf_token STRING NULL,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT owner_ui_sessions_handle_hash_ck CHECK (
    session_handle_hash ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT owner_ui_sessions_record_kind_ck CHECK (
    record_kind IN ('OIDC_PENDING', 'AUTHENTICATED')
  ),
  CONSTRAINT owner_ui_sessions_capacity_slot_ck CHECK (
    capacity_slot >= 0 AND capacity_slot < 256
  ),
  CONSTRAINT owner_ui_sessions_pending_slot_ck CHECK (
    pending_slot IS NULL OR (pending_slot >= 0 AND pending_slot < 128)
  ),
  CONSTRAINT owner_ui_sessions_owner_slot_ck CHECK (
    owner_slot IS NULL OR (owner_slot >= 0 AND owner_slot < 8)
  ),
  CONSTRAINT owner_ui_sessions_time_ck CHECK (expires_at > created_at),
  CONSTRAINT owner_ui_sessions_shape_ck CHECK (
    (
      record_kind = 'OIDC_PENDING'
      AND pending_slot IS NOT NULL
      AND owner_slot IS NULL
      AND tenant_id IS NULL
      AND owner_user_id IS NULL
      AND oidc_subject IS NULL
      AND display_name IS NULL
      AND oidc_state IS NOT NULL
      AND oidc_nonce IS NOT NULL
      AND pkce_verifier IS NOT NULL
      AND return_path IS NOT NULL
      AND csrf_token IS NULL
    )
    OR
    (
      record_kind = 'AUTHENTICATED'
      AND pending_slot IS NULL
      AND owner_slot IS NOT NULL
      AND tenant_id IS NOT NULL
      AND owner_user_id IS NOT NULL
      AND oidc_subject IS NOT NULL
      AND display_name IS NOT NULL
      AND oidc_state IS NULL
      AND oidc_nonce IS NULL
      AND pkce_verifier IS NULL
      AND return_path IS NULL
      AND csrf_token IS NOT NULL
    )
  ),
  CONSTRAINT owner_ui_sessions_field_bounds_ck CHECK (
    (tenant_id IS NULL OR octet_length(tenant_id) BETWEEN 1 AND 255)
    AND (owner_user_id IS NULL OR octet_length(owner_user_id) BETWEEN 1 AND 255)
    AND (oidc_subject IS NULL OR octet_length(oidc_subject) BETWEEN 1 AND 255)
    AND (display_name IS NULL OR octet_length(display_name) BETWEEN 1 AND 255)
    AND (oidc_state IS NULL OR octet_length(oidc_state) BETWEEN 32 AND 128)
    AND (oidc_nonce IS NULL OR octet_length(oidc_nonce) BETWEEN 32 AND 128)
    AND (pkce_verifier IS NULL OR octet_length(pkce_verifier) BETWEEN 43 AND 128)
    AND (return_path IS NULL OR octet_length(return_path) BETWEEN 1 AND 1024)
    AND (csrf_token IS NULL OR octet_length(csrf_token) BETWEEN 32 AND 128)
  )
) WITH (
  ttl_expiration_expression = 'expires_at',
  ttl_job_cron = '* * * * *'
);

CREATE UNIQUE INDEX owner_ui_sessions_capacity_uq
  ON memory_patch.owner_ui_sessions (capacity_slot);
CREATE UNIQUE INDEX owner_ui_sessions_pending_slot_uq
  ON memory_patch.owner_ui_sessions (pending_slot)
  WHERE record_kind = 'OIDC_PENDING';
CREATE UNIQUE INDEX owner_ui_sessions_owner_slot_uq
  ON memory_patch.owner_ui_sessions (tenant_id, owner_user_id, owner_slot)
  WHERE record_kind = 'AUTHENTICATED';
CREATE INDEX owner_ui_sessions_expiry_idx
  ON memory_patch.owner_ui_sessions (expires_at);

ALTER TABLE memory_patch.owner_ui_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.owner_ui_sessions FORCE ROW LEVEL SECURITY;

CREATE POLICY owner_ui_sessions_r4_select
  ON memory_patch.owner_ui_sessions
  FOR SELECT TO mp_app_runtime
  USING (memory_patch.r4_session_runtime_authorized());
CREATE POLICY owner_ui_sessions_r4_insert
  ON memory_patch.owner_ui_sessions
  FOR INSERT TO mp_app_runtime
  WITH CHECK (memory_patch.r4_session_runtime_authorized());
CREATE POLICY owner_ui_sessions_r4_delete
  ON memory_patch.owner_ui_sessions
  FOR DELETE TO mp_app_runtime
  USING (memory_patch.r4_session_runtime_authorized());

REVOKE ALL ON TABLE memory_patch.owner_ui_sessions FROM PUBLIC;
REVOKE ALL ON TABLE memory_patch.owner_ui_sessions FROM mp_app_runtime;
GRANT SELECT, INSERT, DELETE ON TABLE memory_patch.owner_ui_sessions
  TO mp_app_runtime;

REVOKE ALL ON FUNCTION memory_patch.r4_session_runtime_authorized() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memory_patch.r4_session_runtime_authorized()
  TO mp_app_runtime;

ALTER FUNCTION memory_patch.r4_session_runtime_authorized()
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.owner_ui_sessions OWNER TO mp_schema_owner;
