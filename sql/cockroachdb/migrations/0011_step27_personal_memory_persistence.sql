-- Memory Patch Step 27 - owner-private Personal Memory HAT configuration.
-- This migration adds no patch, approval, activation, retrieval, provider,
-- canonical-evidence, commit, or external-execution authority.

CREATE TABLE memory_patch.personal_memory_quota_policies (
  tenant_id STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  quota_policy_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  quota_policy_version STRING NOT NULL,
  maximum_total_spaces INT8 NOT NULL,
  maximum_active_spaces INT8 NOT NULL,
  maximum_archived_spaces INT8 NOT NULL,
  maximum_bytes INT8 NOT NULL,
  maximum_personal_sources INT8 NOT NULL,
  maximum_active_memory_patches INT8 NOT NULL,
  maximum_session_memory_bytes INT8 NOT NULL,
  maximum_ingestion_jobs INT8 NOT NULL,
  maximum_embedding_or_index_bytes INT8 NOT NULL,
  maximum_model_bindings_per_space INT8 NOT NULL,
  policy_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, owner_user_id, quota_policy_id),
  UNIQUE (tenant_id, owner_user_id, quota_policy_id, policy_digest),
  CONSTRAINT personal_memory_quota_policies_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_quota_policies_identity
    CHECK (
      btrim(quota_policy_id) <> ''
      AND char_length(quota_policy_id) <= 255
      AND btrim(quota_policy_version) <> ''
      AND char_length(quota_policy_version) <= 255
    ),
  CONSTRAINT personal_memory_quota_policies_schema
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT personal_memory_quota_policies_limits
    CHECK (
      maximum_total_spaces >= 0
      AND maximum_active_spaces >= 0
      AND maximum_archived_spaces >= 0
      AND maximum_bytes >= 0
      AND maximum_personal_sources >= 0
      AND maximum_active_memory_patches >= 0
      AND maximum_session_memory_bytes >= 0
      AND maximum_ingestion_jobs >= 0
      AND maximum_embedding_or_index_bytes >= 0
      AND maximum_model_bindings_per_space BETWEEN 0 AND 64
    ),
  CONSTRAINT personal_memory_quota_policies_digest
    CHECK (policy_digest ~ '^[0-9a-f]{64}$')
);

ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN state_version INT8 NOT NULL DEFAULT 0;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN configuration_version INT8 NOT NULL DEFAULT 0;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN quota_policy_id STRING;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN quota_policy_digest STRING;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN configuration_digest STRING;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD CONSTRAINT personal_memory_spaces_s27_versions
    CHECK (state_version >= 0 AND configuration_version >= 0);
ALTER TABLE memory_patch.personal_memory_spaces
  ADD CONSTRAINT personal_memory_spaces_s27_configuration_tuple
    CHECK (
      (
        quota_policy_id IS NULL
        AND quota_policy_digest IS NULL
        AND configuration_digest IS NULL
      )
      OR
      (
        quota_policy_id IS NOT NULL
        AND btrim(quota_policy_id) <> ''
        AND quota_policy_digest ~ '^[0-9a-f]{64}$'
        AND configuration_digest ~ '^[0-9a-f]{64}$'
      )
    );
ALTER TABLE memory_patch.personal_memory_spaces
  ADD CONSTRAINT personal_memory_spaces_s27_quota_fk
    FOREIGN KEY (
      tenant_id,
      user_id,
      quota_policy_id,
      quota_policy_digest
    )
    REFERENCES memory_patch.personal_memory_quota_policies (
      tenant_id,
      owner_user_id,
      quota_policy_id,
      policy_digest
    )
    ON DELETE RESTRICT;

ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN provider_id STRING;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN model_id STRING;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN model_revision STRING;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN binding_mode STRING;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN enabled BOOL;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN binding_version INT8;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD COLUMN binding_digest STRING;
ALTER TABLE memory_patch.personal_memory_model_bindings
  ADD CONSTRAINT personal_memory_model_bindings_s27_typed_tuple
    CHECK (
      (
        provider_id IS NULL
        AND model_id IS NULL
        AND model_revision IS NULL
        AND binding_mode IS NULL
        AND enabled IS NULL
        AND binding_version IS NULL
        AND binding_digest IS NULL
      )
      OR
      (
        provider_id IS NOT NULL
        AND btrim(provider_id) <> ''
        AND char_length(provider_id) <= 128
        AND model_id IS NOT NULL
        AND btrim(model_id) <> ''
        AND char_length(model_id) <= 128
        AND model_revision IS NOT NULL
        AND btrim(model_revision) <> ''
        AND char_length(model_revision) <= 128
        AND binding_mode = 'EXACT_MODEL'
        AND enabled IS NOT NULL
        AND binding_version >= 1
        AND binding_digest ~ '^[0-9a-f]{64}$'
      )
    );

CREATE OR REPLACE FUNCTION memory_patch.guard_personal_memory_step27_update()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (OLD).quota_policy_id IS NOT NULL OR (NEW).quota_policy_id IS NOT NULL THEN
    IF (NEW).updated_at < (OLD).updated_at THEN
      RAISE EXCEPTION 'personal memory update time cannot move backwards'
        USING ERRCODE = '23514';
    END IF;
    IF (NEW).state IS DISTINCT FROM (OLD).state THEN
      IF (NEW).state_version <> (OLD).state_version + 1 THEN
        RAISE EXCEPTION 'personal memory state version must advance exactly once'
          USING ERRCODE = '40001';
      END IF;
      IF NOT (
        ((OLD).state = 'EMPTY' AND (NEW).state IN ('CONFIGURED', 'DELETED_PENDING'))
        OR ((OLD).state = 'CONFIGURED' AND (NEW).state IN ('ACTIVE', 'ARCHIVED', 'DELETED_PENDING'))
        OR ((OLD).state = 'ACTIVE' AND (NEW).state IN ('SUSPENDED', 'ARCHIVED', 'DELETED_PENDING'))
        OR ((OLD).state = 'SUSPENDED' AND (NEW).state IN ('ACTIVE', 'ARCHIVED', 'DELETED_PENDING'))
        OR ((OLD).state = 'ARCHIVED' AND (NEW).state IN ('CONFIGURED', 'DELETED_PENDING'))
        OR ((OLD).state = 'DELETED_PENDING' AND (NEW).state = 'DELETED')
      ) THEN
        RAISE EXCEPTION 'personal memory state transition is forbidden'
          USING ERRCODE = '23514';
      END IF;
    ELSIF (NEW).state_version <> (OLD).state_version THEN
      RAISE EXCEPTION 'personal memory state version changed without transition'
        USING ERRCODE = '40001';
    END IF;
    IF (NEW).display_name IS DISTINCT FROM (OLD).display_name
      OR (NEW).quota_policy_id IS DISTINCT FROM (OLD).quota_policy_id
      OR (NEW).quota_policy_digest IS DISTINCT FROM (OLD).quota_policy_digest
      OR (NEW).configuration_digest IS DISTINCT FROM (OLD).configuration_digest
    THEN
      IF (NEW).configuration_version <> (OLD).configuration_version + 1 THEN
        RAISE EXCEPTION 'personal memory configuration version must advance once'
          USING ERRCODE = '40001';
      END IF;
    ELSIF (NEW).configuration_version <> (OLD).configuration_version THEN
      RAISE EXCEPTION 'personal memory configuration version changed without data'
        USING ERRCODE = '40001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS personal_memory_spaces_s27_state_guard
  ON memory_patch.personal_memory_spaces;
CREATE TRIGGER personal_memory_spaces_s27_state_guard
  BEFORE UPDATE ON memory_patch.personal_memory_spaces
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_personal_memory_step27_update();

ALTER TABLE memory_patch.personal_memory_quota_policies
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_personal_memory_step27_update()
  OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE memory_patch.personal_memory_quota_policies
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
GRANT SELECT, INSERT ON TABLE memory_patch.personal_memory_quota_policies
  TO mp_app_runtime;

ALTER TABLE memory_patch.personal_memory_quota_policies
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_quota_policies
  FORCE ROW LEVEL SECURITY;

CREATE POLICY personal_memory_quota_policies_s27_select
  ON memory_patch.personal_memory_quota_policies
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
  );

CREATE POLICY personal_memory_quota_policies_s27_insert
  ON memory_patch.personal_memory_quota_policies
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
  );
