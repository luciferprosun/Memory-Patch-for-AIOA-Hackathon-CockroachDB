-- Memory Patch Step 6 — retry-safe persistence operation identity and resume state.
-- This table records persistence workflow facts only. It grants no approval,
-- commit, publication, activation, model, HAT, or external-execution authority.

CREATE TABLE memory_patch.persistence_operations (
  tenant_id STRING NOT NULL,
  operation_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  owner_user_id STRING,
  operation_kind STRING NOT NULL,
  idempotency_key STRING NOT NULL,
  request_digest STRING NOT NULL,
  scope_digest STRING NOT NULL,
  status STRING NOT NULL,
  attempt_count INT8 NOT NULL,
  result_ref STRING,
  result_digest STRING,
  last_sqlstate STRING,
  sanitized_error_code STRING,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  origin_kind STRING,
  origin_system STRING,
  origin_version STRING,
  adapter_version STRING,
  artifact_kind STRING,
  external_ref STRING,
  PRIMARY KEY (tenant_id, operation_id),
  CONSTRAINT persistence_operations_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id)
    ON DELETE RESTRICT,
  CONSTRAINT persistence_operations_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT persistence_operations_operation_id
    CHECK (
      btrim(operation_id) <> ''
      AND char_length(operation_id) <= 255
    ),
  CONSTRAINT persistence_operations_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT persistence_operations_owner
    CHECK (
      owner_user_id IS NULL
      OR (
        btrim(owner_user_id) <> ''
        AND char_length(owner_user_id) <= 255
      )
    ),
  CONSTRAINT persistence_operations_kind
    CHECK (
      btrim(operation_kind) <> ''
      AND char_length(operation_kind) <= 128
    ),
  CONSTRAINT persistence_operations_idempotency_key
    CHECK (
      btrim(idempotency_key) <> ''
      AND char_length(idempotency_key) <= 512
    ),
  CONSTRAINT persistence_operations_request_digest
    CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT persistence_operations_scope_digest
    CHECK (scope_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT persistence_operations_status
    CHECK (
      status IN (
        'PENDING',
        'IN_PROGRESS',
        'COMPLETED',
        'INTERRUPTED',
        'FAILED_FINAL'
      )
    ),
  CONSTRAINT persistence_operations_attempt_count
    CHECK (attempt_count >= 0),
  CONSTRAINT persistence_operations_result_ref
    CHECK (
      result_ref IS NULL
      OR (
        btrim(result_ref) <> ''
        AND char_length(result_ref) <= 1024
      )
    ),
  CONSTRAINT persistence_operations_result_digest
    CHECK (
      result_digest IS NULL
      OR result_digest ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT persistence_operations_sqlstate
    CHECK (
      last_sqlstate IS NULL
      OR last_sqlstate ~ '^[0-9A-Z]{5}$'
    ),
  CONSTRAINT persistence_operations_error_code
    CHECK (
      sanitized_error_code IS NULL
      OR sanitized_error_code ~ '^[A-Z0-9][A-Z0-9_:-]{0,63}$'
    ),
  CONSTRAINT persistence_operations_completion_shape
    CHECK (
      (
        status = 'COMPLETED'
        AND result_digest IS NOT NULL
        AND completed_at IS NOT NULL
        AND last_sqlstate IS NULL
        AND sanitized_error_code IS NULL
      )
      OR
      (
        status <> 'COMPLETED'
        AND result_ref IS NULL
        AND result_digest IS NULL
        AND completed_at IS NULL
      )
    ),
  CONSTRAINT persistence_operations_time_order
    CHECK (
      updated_at >= created_at
      AND (
        completed_at IS NULL
        OR (
          completed_at >= created_at
          AND completed_at <= updated_at
        )
      )
    ),
  CONSTRAINT persistence_operations_external_tuple
    CHECK (
      (
        origin_kind IS NULL
        AND origin_system IS NULL
        AND origin_version IS NULL
        AND adapter_version IS NULL
        AND artifact_kind IS NULL
        AND external_ref IS NULL
      )
      OR
      (
        origin_kind IS NOT NULL
        AND btrim(origin_kind) <> ''
        AND char_length(origin_kind) <= 128
        AND origin_system IS NOT NULL
        AND btrim(origin_system) <> ''
        AND char_length(origin_system) <= 255
        AND origin_version IS NOT NULL
        AND btrim(origin_version) <> ''
        AND char_length(origin_version) <= 128
        AND adapter_version IS NOT NULL
        AND btrim(adapter_version) <> ''
        AND char_length(adapter_version) <= 128
        AND artifact_kind IS NOT NULL
        AND btrim(artifact_kind) <> ''
        AND char_length(artifact_kind) <= 128
        AND external_ref IS NOT NULL
        AND btrim(external_ref) <> ''
        AND char_length(external_ref) <= 1024
      )
    )
);

CREATE UNIQUE INDEX persistence_operations_shared_idempotency_uq
  ON memory_patch.persistence_operations (
    tenant_id,
    operation_kind,
    idempotency_key
  )
  WHERE owner_user_id IS NULL;

CREATE UNIQUE INDEX persistence_operations_private_idempotency_uq
  ON memory_patch.persistence_operations (
    tenant_id,
    owner_user_id,
    operation_kind,
    idempotency_key
  )
  WHERE owner_user_id IS NOT NULL;

CREATE UNIQUE INDEX persistence_operations_external_identity_uq
  ON memory_patch.persistence_operations (
    tenant_id,
    origin_kind,
    origin_system,
    origin_version,
    adapter_version,
    artifact_kind,
    external_ref
  )
  WHERE external_ref IS NOT NULL;

CREATE OR REPLACE FUNCTION memory_patch.guard_persistence_operation_identity()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).operation_id IS DISTINCT FROM (OLD).operation_id
    OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
    OR (NEW).owner_user_id IS DISTINCT FROM (OLD).owner_user_id
    OR (NEW).operation_kind IS DISTINCT FROM (OLD).operation_kind
    OR (NEW).idempotency_key IS DISTINCT FROM (OLD).idempotency_key
    OR (NEW).request_digest IS DISTINCT FROM (OLD).request_digest
    OR (NEW).scope_digest IS DISTINCT FROM (OLD).scope_digest
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
    OR (NEW).origin_kind IS DISTINCT FROM (OLD).origin_kind
    OR (NEW).origin_system IS DISTINCT FROM (OLD).origin_system
    OR (NEW).origin_version IS DISTINCT FROM (OLD).origin_version
    OR (NEW).adapter_version IS DISTINCT FROM (OLD).adapter_version
    OR (NEW).artifact_kind IS DISTINCT FROM (OLD).artifact_kind
    OR (NEW).external_ref IS DISTINCT FROM (OLD).external_ref
  THEN
    RAISE EXCEPTION 'persistence operation identity is immutable'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER persistence_operations_s6_identity_guard
BEFORE UPDATE ON memory_patch.persistence_operations
FOR EACH ROW
EXECUTE FUNCTION memory_patch.guard_persistence_operation_identity();

ALTER TABLE memory_patch.persistence_operations
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_persistence_operation_identity()
  OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE memory_patch.persistence_operations FROM PUBLIC;
REVOKE ALL ON TABLE memory_patch.persistence_operations
  FROM mp_request_context_setter;
REVOKE EXECUTE
  ON FUNCTION memory_patch.guard_persistence_operation_identity()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;

GRANT SELECT, INSERT, UPDATE
  ON TABLE memory_patch.persistence_operations
  TO mp_app_runtime;

ALTER TABLE memory_patch.persistence_operations
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.persistence_operations
  FORCE ROW LEVEL SECURITY;

CREATE POLICY persistence_operations_s6_select
  ON memory_patch.persistence_operations
  FOR SELECT
  TO mp_app_runtime
  USING (
    CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  );

CREATE POLICY persistence_operations_s6_insert
  ON memory_patch.persistence_operations
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  );

CREATE POLICY persistence_operations_s6_update
  ON memory_patch.persistence_operations
  FOR UPDATE
  TO mp_app_runtime
  USING (
    CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  )
  WITH CHECK (
    CASE
      WHEN owner_user_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.user_context_matches(tenant_id, owner_user_id)
    END
  );
