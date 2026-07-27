-- Memory Patch Step 4 — identity, HAT, and ownership roots.
-- Forward-only CockroachDB migration. It grants no roles or authority.

CREATE SCHEMA IF NOT EXISTS memory_patch;

CREATE TABLE memory_patch.schema_migrations (
  migration_id STRING PRIMARY KEY,
  checksum_sha256 STRING NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL,
  runner_version STRING NOT NULL,
  CONSTRAINT schema_migrations_id_not_blank
    CHECK (btrim(migration_id) <> ''),
  CONSTRAINT schema_migrations_checksum_sha256
    CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT schema_migrations_runner_not_blank
    CHECK (btrim(runner_version) <> '')
);

CREATE TABLE memory_patch.tenants (
  tenant_id STRING PRIMARY KEY,
  display_name STRING,
  metadata JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT tenants_id_not_blank CHECK (btrim(tenant_id) <> ''),
  CONSTRAINT tenants_display_name_not_blank
    CHECK (display_name IS NULL OR btrim(display_name) <> ''),
  CONSTRAINT tenants_metadata_object
    CHECK (jsonb_typeof(metadata) = 'object'),
  CONSTRAINT tenants_time_order CHECK (updated_at >= created_at)
);

CREATE TABLE memory_patch.users (
  tenant_id STRING NOT NULL,
  user_id STRING NOT NULL,
  display_name STRING,
  metadata JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, user_id),
  CONSTRAINT users_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id)
    ON DELETE RESTRICT,
  CONSTRAINT users_id_not_blank CHECK (btrim(user_id) <> ''),
  CONSTRAINT users_display_name_not_blank
    CHECK (display_name IS NULL OR btrim(display_name) <> ''),
  CONSTRAINT users_metadata_object CHECK (jsonb_typeof(metadata) = 'object'),
  CONSTRAINT users_time_order CHECK (updated_at >= created_at)
);

CREATE TABLE memory_patch.hat_manifests (
  hat_id STRING NOT NULL,
  hat_version STRING NOT NULL,
  schema_version STRING NOT NULL,
  display_name STRING NOT NULL,
  manifest_hash STRING NOT NULL,
  capabilities JSONB NOT NULL,
  approval_authority STRING NOT NULL,
  commit_authority STRING NOT NULL,
  canonical_write_authority STRING NOT NULL,
  external_action_authority STRING NOT NULL,
  allows_private_memory_access BOOL NOT NULL,
  allows_user_code BOOL NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (hat_id, hat_version),
  CONSTRAINT hat_manifests_hat_id_not_blank CHECK (btrim(hat_id) <> ''),
  CONSTRAINT hat_manifests_hat_version_not_blank
    CHECK (btrim(hat_version) <> ''),
  CONSTRAINT hat_manifests_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT hat_manifests_display_name_not_blank
    CHECK (btrim(display_name) <> ''),
  CONSTRAINT hat_manifests_hash
    CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT hat_manifests_capabilities_array
    CHECK (jsonb_typeof(capabilities) = 'array'),
  CONSTRAINT hat_manifests_no_approval_authority
    CHECK (approval_authority = 'NONE'),
  CONSTRAINT hat_manifests_no_commit_authority
    CHECK (commit_authority = 'NONE'),
  CONSTRAINT hat_manifests_no_canonical_write_authority
    CHECK (canonical_write_authority = 'NONE'),
  CONSTRAINT hat_manifests_no_external_action_authority
    CHECK (external_action_authority = 'NONE'),
  CONSTRAINT hat_manifests_no_private_memory_access
    CHECK (allows_private_memory_access = false),
  CONSTRAINT hat_manifests_no_user_code
    CHECK (allows_user_code = false)
);

CREATE TABLE memory_patch.personal_memory_spaces (
  tenant_id STRING NOT NULL,
  user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  state STRING NOT NULL,
  display_name STRING,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  export_requested_at TIMESTAMPTZ,
  deletion_requested_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, user_id, personal_memory_space_id),
  UNIQUE (tenant_id, personal_memory_space_id),
  CONSTRAINT personal_memory_spaces_user_fk
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_spaces_id_not_blank
    CHECK (btrim(personal_memory_space_id) <> ''),
  CONSTRAINT personal_memory_spaces_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT personal_memory_spaces_state
    CHECK (
      state IN (
        'EMPTY',
        'CONFIGURED',
        'ACTIVE',
        'SUSPENDED',
        'ARCHIVED',
        'DELETED_PENDING',
        'DELETED'
      )
    ),
  CONSTRAINT personal_memory_spaces_display_name
    CHECK (
      (state = 'EMPTY' AND display_name IS NULL)
      OR
      (
        state IN ('CONFIGURED', 'ACTIVE', 'SUSPENDED', 'ARCHIVED')
        AND display_name IS NOT NULL
        AND btrim(display_name) <> ''
      )
      OR
      state IN ('DELETED_PENDING', 'DELETED')
    ),
  CONSTRAINT personal_memory_spaces_time_order
    CHECK (updated_at >= created_at),
  CONSTRAINT personal_memory_spaces_deletion_request
    CHECK (
      state <> 'DELETED_PENDING'
      OR deletion_requested_at IS NOT NULL
    ),
  CONSTRAINT personal_memory_spaces_deleted_at
    CHECK (
      (state = 'DELETED' AND deleted_at IS NOT NULL)
      OR (state <> 'DELETED' AND deleted_at IS NULL)
    )
);

CREATE TABLE memory_patch.personal_memory_model_bindings (
  tenant_id STRING NOT NULL,
  user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  model_binding_id STRING NOT NULL,
  bound_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (
    tenant_id,
    user_id,
    personal_memory_space_id,
    model_binding_id
  ),
  CONSTRAINT personal_memory_model_bindings_space_fk
    FOREIGN KEY (tenant_id, user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id,
      user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_model_bindings_id_not_blank
    CHECK (btrim(model_binding_id) <> '')
);

CREATE TABLE memory_patch.hat_scopes (
  tenant_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  target_scope STRING NOT NULL,
  knowledge_hat_id STRING,
  knowledge_hat_version STRING,
  owner_user_id STRING,
  personal_memory_space_id STRING,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, hat_scope_id),
  UNIQUE (tenant_id, hat_scope_id, target_scope),
  UNIQUE (
    tenant_id,
    hat_scope_id,
    target_scope,
    owner_user_id,
    personal_memory_space_id
  ),
  UNIQUE (
    tenant_id,
    hat_scope_id,
    target_scope,
    knowledge_hat_id
  ),
  UNIQUE (
    tenant_id,
    hat_scope_id,
    knowledge_hat_id
  ),
  CONSTRAINT hat_scopes_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id)
    ON DELETE RESTRICT,
  CONSTRAINT hat_scopes_manifest_fk
    FOREIGN KEY (knowledge_hat_id, knowledge_hat_version)
    REFERENCES memory_patch.hat_manifests (hat_id, hat_version)
    ON DELETE RESTRICT,
  CONSTRAINT hat_scopes_personal_space_fk
    FOREIGN KEY (tenant_id, owner_user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id,
      user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT hat_scopes_id_not_blank CHECK (btrim(hat_scope_id) <> ''),
  CONSTRAINT hat_scopes_exact_ownership
    CHECK (
      (
        target_scope = 'SHARED_KNOWLEDGE_HAT'
        AND knowledge_hat_id IS NOT NULL
        AND btrim(knowledge_hat_id) <> ''
        AND knowledge_hat_version IS NOT NULL
        AND btrim(knowledge_hat_version) <> ''
        AND owner_user_id IS NULL
        AND personal_memory_space_id IS NULL
      )
      OR
      (
        target_scope = 'USER_PERSONAL_HAT'
        AND knowledge_hat_id IS NULL
        AND knowledge_hat_version IS NULL
        AND owner_user_id IS NOT NULL
        AND btrim(owner_user_id) <> ''
        AND personal_memory_space_id IS NOT NULL
        AND btrim(personal_memory_space_id) <> ''
      )
    )
);

CREATE INDEX hat_scopes_personal_owner_idx
  ON memory_patch.hat_scopes (
    tenant_id,
    owner_user_id,
    personal_memory_space_id
  )
  WHERE target_scope = 'USER_PERSONAL_HAT';

CREATE INDEX hat_scopes_shared_hat_idx
  ON memory_patch.hat_scopes (
    tenant_id,
    knowledge_hat_id,
    knowledge_hat_version
  )
  WHERE target_scope = 'SHARED_KNOWLEDGE_HAT';
