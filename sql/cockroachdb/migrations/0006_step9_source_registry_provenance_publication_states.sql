-- Memory Patch Step 9 — source registry, provenance, and publication states 1A.
-- Registration and publication are control-plane facts only. They grant no
-- answer, approval, commit, activation, external-action, or execution authority.

ALTER TABLE memory_patch.knowledge_sources
  ADD CONSTRAINT knowledge_sources_s9_registry_identity_uq
  UNIQUE (
    tenant_id,
    source_id,
    hat_scope_id,
    source_kind,
    source_reference
  );

ALTER TABLE memory_patch.source_snapshots
  ADD CONSTRAINT source_snapshots_s9_registry_identity_uq
  UNIQUE (
    tenant_id,
    snapshot_id,
    source_id,
    hat_scope_id,
    content_sha256,
    byte_length
  );

ALTER TABLE memory_patch.knowledge_versions
  ADD CONSTRAINT knowledge_versions_s9_registry_identity_uq
  UNIQUE (
    tenant_id,
    knowledge_version_id,
    source_id,
    hat_scope_id,
    snapshot_id
  );

CREATE TABLE memory_patch.source_registry_entries (
  tenant_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  source_kind STRING NOT NULL,
  source_reference STRING NOT NULL,
  target_scope STRING NOT NULL,
  owner_user_id STRING,
  personal_memory_space_id STRING,
  authority_level STRING NOT NULL,
  authority_basis JSONB NOT NULL,
  license_status STRING NOT NULL,
  license_identifier STRING,
  license_reference STRING,
  access_class STRING NOT NULL,
  redaction_state STRING NOT NULL,
  scope_dimensions JSONB NOT NULL,
  scope_digest STRING NOT NULL,
  parser_name STRING NOT NULL,
  parser_version STRING NOT NULL,
  parser_contract_version STRING NOT NULL,
  transformation_name STRING NOT NULL,
  transformation_version STRING NOT NULL,
  transformation_contract_version STRING NOT NULL,
  origin_kind STRING NOT NULL,
  origin_system STRING NOT NULL,
  origin_version STRING NOT NULL,
  adapter_version STRING NOT NULL,
  external_ref STRING,
  observed_at TIMESTAMPTZ,
  artifact_kind STRING NOT NULL,
  artifact_digest STRING NOT NULL,
  artifact_byte_length INT8,
  artifact_media_type STRING,
  artifact_created_at TIMESTAMPTZ NOT NULL,
  exact_source_bytes BOOL NOT NULL,
  model_generated BOOL NOT NULL,
  snapshot_id STRING,
  knowledge_version_id STRING,
  current_publication_state STRING NOT NULL,
  current_publication_sequence INT8 NOT NULL,
  current_publication_event_digest STRING NOT NULL,
  registry_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, source_id, hat_scope_id),
  UNIQUE (tenant_id, source_id, hat_scope_id, registry_digest),
  CONSTRAINT source_registry_entries_source_fk
    FOREIGN KEY (
      tenant_id,
      source_id,
      hat_scope_id,
      source_kind,
      source_reference
    )
    REFERENCES memory_patch.knowledge_sources (
      tenant_id,
      source_id,
      hat_scope_id,
      source_kind,
      source_reference
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_registry_entries_scope_target_fk
    FOREIGN KEY (tenant_id, hat_scope_id, target_scope)
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      target_scope
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_registry_entries_personal_scope_fk
    FOREIGN KEY (
      tenant_id,
      hat_scope_id,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    )
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_registry_entries_snapshot_fk
    FOREIGN KEY (
      tenant_id,
      snapshot_id,
      source_id,
      hat_scope_id,
      artifact_digest,
      artifact_byte_length
    )
    REFERENCES memory_patch.source_snapshots (
      tenant_id,
      snapshot_id,
      source_id,
      hat_scope_id,
      content_sha256,
      byte_length
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_registry_entries_version_fk
    FOREIGN KEY (
      tenant_id,
      knowledge_version_id,
      source_id,
      hat_scope_id,
      snapshot_id
    )
    REFERENCES memory_patch.knowledge_versions (
      tenant_id,
      knowledge_version_id,
      source_id,
      hat_scope_id,
      snapshot_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_registry_entries_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT source_registry_entries_source_identity
    CHECK (
      btrim(source_kind) <> ''
      AND char_length(source_kind) <= 128
      AND btrim(source_reference) <> ''
      AND char_length(source_reference) <= 1024
    ),
  CONSTRAINT source_registry_entries_authority_level
    CHECK (
      authority_level IN (
        'OFFICIAL_PRIMARY',
        'AUTHORITATIVE_SECONDARY',
        'INFORMATIONAL_SECONDARY',
        'USER_SUPPLIED',
        'DERIVED',
        'UNKNOWN'
      )
    ),
  CONSTRAINT source_registry_entries_authority_basis
    CHECK (
      jsonb_typeof(authority_basis) = 'object'
      AND (
        authority_level = 'UNKNOWN'
        OR authority_basis != '{}'::JSONB
      )
    ),
  CONSTRAINT source_registry_entries_license_status
    CHECK (
      license_status IN (
        'PUBLIC_DOMAIN',
        'CONFIRMED_PERMISSIVE',
        'CONFIRMED_RESTRICTED',
        'PRIVATE_AUTHORIZED',
        'UNKNOWN',
        'PROHIBITED'
      )
    ),
  CONSTRAINT source_registry_entries_license_metadata
    CHECK (
      (
        license_identifier IS NULL
        OR (
          btrim(license_identifier) <> ''
          AND char_length(license_identifier) <= 255
        )
      )
      AND (
        license_reference IS NULL
        OR (
          btrim(license_reference) <> ''
          AND char_length(license_reference) <= 1024
        )
      )
    ),
  CONSTRAINT source_registry_entries_access_class
    CHECK (
      access_class IN ('PUBLIC', 'TENANT_RESTRICTED', 'USER_PRIVATE')
    ),
  CONSTRAINT source_registry_entries_redaction_state
    CHECK (
      redaction_state IN ('NOT_REQUIRED', 'PENDING', 'VERIFIED', 'REJECTED')
    ),
  CONSTRAINT source_registry_entries_exact_scope
    CHECK (
      (
        access_class = 'USER_PRIVATE'
        AND target_scope = 'USER_PERSONAL_HAT'
        AND owner_user_id IS NOT NULL
        AND btrim(owner_user_id) <> ''
        AND personal_memory_space_id IS NOT NULL
        AND btrim(personal_memory_space_id) <> ''
      )
      OR
      (
        access_class IN ('PUBLIC', 'TENANT_RESTRICTED')
        AND target_scope = 'SHARED_KNOWLEDGE_HAT'
        AND owner_user_id IS NULL
        AND personal_memory_space_id IS NULL
      )
    ),
  CONSTRAINT source_registry_entries_scope_dimensions
    CHECK (
      jsonb_typeof(scope_dimensions) = 'object'
      AND octet_length(scope_dimensions::STRING) <= 16384
    ),
  CONSTRAINT source_registry_entries_scope_digest
    CHECK (scope_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT source_registry_entries_parser_identity
    CHECK (
      btrim(parser_name) <> ''
      AND char_length(parser_name) <= 128
      AND btrim(parser_version) <> ''
      AND char_length(parser_version) <= 128
      AND lower(parser_version) <> 'latest'
      AND btrim(parser_contract_version) <> ''
      AND char_length(parser_contract_version) <= 128
      AND lower(parser_contract_version) <> 'latest'
    ),
  CONSTRAINT source_registry_entries_transformation_identity
    CHECK (
      btrim(transformation_name) <> ''
      AND char_length(transformation_name) <= 128
      AND btrim(transformation_version) <> ''
      AND char_length(transformation_version) <= 128
      AND lower(transformation_version) <> 'latest'
      AND btrim(transformation_contract_version) <> ''
      AND char_length(transformation_contract_version) <= 128
      AND lower(transformation_contract_version) <> 'latest'
    ),
  CONSTRAINT source_registry_entries_origin_identity
    CHECK (
      btrim(origin_kind) <> ''
      AND char_length(origin_kind) <= 128
      AND btrim(origin_system) <> ''
      AND char_length(origin_system) <= 255
      AND btrim(origin_version) <> ''
      AND char_length(origin_version) <= 128
      AND lower(origin_version) <> 'latest'
      AND btrim(adapter_version) <> ''
      AND char_length(adapter_version) <= 128
      AND lower(adapter_version) <> 'latest'
      AND (
        external_ref IS NULL
        OR (
          btrim(external_ref) <> ''
          AND char_length(external_ref) <= 1024
        )
      )
    ),
  CONSTRAINT source_registry_entries_artifact_identity
    CHECK (
      btrim(artifact_kind) <> ''
      AND char_length(artifact_kind) <= 128
      AND artifact_digest ~ '^[0-9a-f]{64}$'
      AND (artifact_byte_length IS NULL OR artifact_byte_length >= 0)
      AND (
        artifact_media_type IS NULL
        OR (
          btrim(artifact_media_type) <> ''
          AND char_length(artifact_media_type) <= 255
        )
      )
      AND NOT (exact_source_bytes AND model_generated)
      AND (
        snapshot_id IS NULL
        OR (
          exact_source_bytes
          AND artifact_byte_length IS NOT NULL
        )
      )
      AND (
        knowledge_version_id IS NULL
        OR snapshot_id IS NOT NULL
      )
    ),
  CONSTRAINT source_registry_entries_publication_state
    CHECK (
      current_publication_state IN (
        'REGISTERED',
        'REVIEW_REQUIRED',
        'ELIGIBLE',
        'PUBLISHED',
        'QUARANTINED',
        'WITHDRAWN',
        'REJECTED'
      )
    ),
  CONSTRAINT source_registry_entries_publication_pointer
    CHECK (
      current_publication_sequence >= 0
      AND current_publication_event_digest ~ '^[0-9a-f]{64}$'
      AND (
        (
          current_publication_sequence = 0
          AND current_publication_state = 'REGISTERED'
          AND current_publication_event_digest =
            '6d6e54df2447ab416012f2afbf0cdf857d2055a3ef05a3d9023b8561b20c9693'
        )
        OR (
          current_publication_sequence > 0
          AND current_publication_state <> 'REGISTERED'
          AND current_publication_event_digest <>
            '6d6e54df2447ab416012f2afbf0cdf857d2055a3ef05a3d9023b8561b20c9693'
        )
      )
    ),
  CONSTRAINT source_registry_entries_registry_digest
    CHECK (registry_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT source_registry_entries_time_order
    CHECK (updated_at >= created_at)
);

CREATE TABLE memory_patch.source_provenance_edges (
  tenant_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  edge_id STRING NOT NULL,
  parent_artifact_digest STRING NOT NULL,
  child_artifact_digest STRING NOT NULL,
  edge_kind STRING NOT NULL,
  parser_name STRING NOT NULL,
  parser_version STRING NOT NULL,
  parser_contract_version STRING NOT NULL,
  transformation_name STRING NOT NULL,
  transformation_version STRING NOT NULL,
  transformation_contract_version STRING NOT NULL,
  metadata JSONB NOT NULL,
  edge_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, source_id, hat_scope_id, edge_id),
  UNIQUE (tenant_id, source_id, hat_scope_id, edge_digest),
  CONSTRAINT source_provenance_edges_registry_fk
    FOREIGN KEY (tenant_id, source_id, hat_scope_id)
    REFERENCES memory_patch.source_registry_entries (
      tenant_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_provenance_edges_id
    CHECK (btrim(edge_id) <> '' AND char_length(edge_id) <= 255),
  CONSTRAINT source_provenance_edges_digests
    CHECK (
      parent_artifact_digest ~ '^[0-9a-f]{64}$'
      AND child_artifact_digest ~ '^[0-9a-f]{64}$'
      AND edge_digest ~ '^[0-9a-f]{64}$'
      AND parent_artifact_digest <> child_artifact_digest
    ),
  CONSTRAINT source_provenance_edges_kind
    CHECK (btrim(edge_kind) <> '' AND char_length(edge_kind) <= 128),
  CONSTRAINT source_provenance_edges_parser_identity
    CHECK (
      btrim(parser_name) <> ''
      AND char_length(parser_name) <= 128
      AND btrim(parser_version) <> ''
      AND char_length(parser_version) <= 128
      AND lower(parser_version) <> 'latest'
      AND btrim(parser_contract_version) <> ''
      AND char_length(parser_contract_version) <= 128
      AND lower(parser_contract_version) <> 'latest'
    ),
  CONSTRAINT source_provenance_edges_transformation_identity
    CHECK (
      btrim(transformation_name) <> ''
      AND char_length(transformation_name) <= 128
      AND btrim(transformation_version) <> ''
      AND char_length(transformation_version) <= 128
      AND lower(transformation_version) <> 'latest'
      AND btrim(transformation_contract_version) <> ''
      AND char_length(transformation_contract_version) <= 128
      AND lower(transformation_contract_version) <> 'latest'
    ),
  CONSTRAINT source_provenance_edges_metadata
    CHECK (
      jsonb_typeof(metadata) = 'object'
      AND octet_length(metadata::STRING) <= 16384
    )
);

CREATE TABLE memory_patch.source_publication_events (
  tenant_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  event_id STRING NOT NULL,
  sequence_number INT8 NOT NULL,
  from_state STRING NOT NULL,
  to_state STRING NOT NULL,
  actor_type STRING NOT NULL,
  actor_reference STRING NOT NULL,
  policy_version STRING NOT NULL,
  eligibility_decision_digest STRING NOT NULL,
  reason_codes JSONB NOT NULL,
  reviewer_reference STRING,
  previous_event_digest STRING NOT NULL,
  event_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, source_id, hat_scope_id, event_id),
  UNIQUE (tenant_id, source_id, hat_scope_id, sequence_number),
  UNIQUE (tenant_id, source_id, hat_scope_id, event_digest),
  CONSTRAINT source_publication_events_registry_fk
    FOREIGN KEY (tenant_id, source_id, hat_scope_id)
    REFERENCES memory_patch.source_registry_entries (
      tenant_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_publication_events_id
    CHECK (btrim(event_id) <> '' AND char_length(event_id) <= 255),
  CONSTRAINT source_publication_events_sequence
    CHECK (sequence_number > 0),
  CONSTRAINT source_publication_events_states
    CHECK (
      (
        from_state = 'REGISTERED'
        AND to_state IN ('REVIEW_REQUIRED', 'QUARANTINED', 'REJECTED')
      )
      OR (
        from_state = 'REVIEW_REQUIRED'
        AND to_state IN ('ELIGIBLE', 'QUARANTINED', 'REJECTED')
      )
      OR (
        from_state = 'ELIGIBLE'
        AND to_state IN ('PUBLISHED', 'REVIEW_REQUIRED', 'QUARANTINED')
      )
      OR (
        from_state = 'PUBLISHED'
        AND to_state IN ('WITHDRAWN', 'QUARANTINED')
      )
      OR (
        from_state = 'QUARANTINED'
        AND to_state IN ('REVIEW_REQUIRED', 'REJECTED')
      )
      OR (
        from_state = 'WITHDRAWN'
        AND to_state = 'REVIEW_REQUIRED'
      )
    ),
  CONSTRAINT source_publication_events_actor
    CHECK (
      actor_type IN (
        'TRUSTED_APPLICATION',
        'HUMAN_REVIEWER',
        'MIGRATION_SERVICE'
      )
      AND btrim(actor_reference) <> ''
      AND char_length(actor_reference) <= 255
    ),
  CONSTRAINT source_publication_events_policy
    CHECK (policy_version = 'source-publication-eligibility-1a'),
  CONSTRAINT source_publication_events_eligibility_digest
    CHECK (eligibility_decision_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT source_publication_events_reason_codes
    CHECK (
      jsonb_typeof(reason_codes) = 'array'
      AND jsonb_array_length(reason_codes) <= 32
      AND octet_length(reason_codes::STRING) <= 4096
    ),
  CONSTRAINT source_publication_events_reviewer_reference
    CHECK (
      reviewer_reference IS NULL
      OR (
        btrim(reviewer_reference) <> ''
        AND char_length(reviewer_reference) <= 255
      )
    ),
  CONSTRAINT source_publication_events_chain_digests
    CHECK (
      previous_event_digest ~ '^[0-9a-f]{64}$'
      AND event_digest ~ '^[0-9a-f]{64}$'
      AND previous_event_digest <> event_digest
    )
);

CREATE OR REPLACE FUNCTION memory_patch.guard_source_registry_publication_update()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).source_id IS DISTINCT FROM (OLD).source_id
    OR (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
    OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
    OR (NEW).source_kind IS DISTINCT FROM (OLD).source_kind
    OR (NEW).source_reference IS DISTINCT FROM (OLD).source_reference
    OR (NEW).target_scope IS DISTINCT FROM (OLD).target_scope
    OR (NEW).owner_user_id IS DISTINCT FROM (OLD).owner_user_id
    OR (NEW).personal_memory_space_id
      IS DISTINCT FROM (OLD).personal_memory_space_id
    OR (NEW).authority_level IS DISTINCT FROM (OLD).authority_level
    OR (NEW).authority_basis IS DISTINCT FROM (OLD).authority_basis
    OR (NEW).license_status IS DISTINCT FROM (OLD).license_status
    OR (NEW).license_identifier IS DISTINCT FROM (OLD).license_identifier
    OR (NEW).license_reference IS DISTINCT FROM (OLD).license_reference
    OR (NEW).access_class IS DISTINCT FROM (OLD).access_class
    OR (NEW).redaction_state IS DISTINCT FROM (OLD).redaction_state
    OR (NEW).scope_dimensions IS DISTINCT FROM (OLD).scope_dimensions
    OR (NEW).scope_digest IS DISTINCT FROM (OLD).scope_digest
    OR (NEW).parser_name IS DISTINCT FROM (OLD).parser_name
    OR (NEW).parser_version IS DISTINCT FROM (OLD).parser_version
    OR (NEW).parser_contract_version
      IS DISTINCT FROM (OLD).parser_contract_version
    OR (NEW).transformation_name IS DISTINCT FROM (OLD).transformation_name
    OR (NEW).transformation_version
      IS DISTINCT FROM (OLD).transformation_version
    OR (NEW).transformation_contract_version
      IS DISTINCT FROM (OLD).transformation_contract_version
    OR (NEW).origin_kind IS DISTINCT FROM (OLD).origin_kind
    OR (NEW).origin_system IS DISTINCT FROM (OLD).origin_system
    OR (NEW).origin_version IS DISTINCT FROM (OLD).origin_version
    OR (NEW).adapter_version IS DISTINCT FROM (OLD).adapter_version
    OR (NEW).external_ref IS DISTINCT FROM (OLD).external_ref
    OR (NEW).observed_at IS DISTINCT FROM (OLD).observed_at
    OR (NEW).artifact_kind IS DISTINCT FROM (OLD).artifact_kind
    OR (NEW).artifact_digest IS DISTINCT FROM (OLD).artifact_digest
    OR (NEW).artifact_byte_length IS DISTINCT FROM (OLD).artifact_byte_length
    OR (NEW).artifact_media_type IS DISTINCT FROM (OLD).artifact_media_type
    OR (NEW).artifact_created_at IS DISTINCT FROM (OLD).artifact_created_at
    OR (NEW).exact_source_bytes IS DISTINCT FROM (OLD).exact_source_bytes
    OR (NEW).model_generated IS DISTINCT FROM (OLD).model_generated
    OR (NEW).snapshot_id IS DISTINCT FROM (OLD).snapshot_id
    OR (NEW).knowledge_version_id IS DISTINCT FROM (OLD).knowledge_version_id
    OR (NEW).registry_digest IS DISTINCT FROM (OLD).registry_digest
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
  THEN
    RAISE EXCEPTION 'source registry identity is immutable'
      USING ERRCODE = '42501';
  END IF;
  IF (NEW).current_publication_sequence
      <> (OLD).current_publication_sequence + 1
    OR (NEW).current_publication_state
      IS NOT DISTINCT FROM (OLD).current_publication_state
    OR (NEW).current_publication_event_digest
      IS NOT DISTINCT FROM (OLD).current_publication_event_digest
    OR (NEW).updated_at < (OLD).updated_at
    OR NOT EXISTS (
      SELECT 1
        FROM memory_patch.source_publication_events AS event
       WHERE event.tenant_id = (OLD).tenant_id
         AND event.source_id = (OLD).source_id
         AND event.hat_scope_id = (OLD).hat_scope_id
         AND event.sequence_number = (NEW).current_publication_sequence
         AND event.from_state = (OLD).current_publication_state
         AND event.to_state = (NEW).current_publication_state
         AND event.previous_event_digest =
           (OLD).current_publication_event_digest
         AND event.event_digest = (NEW).current_publication_event_digest
         AND event.created_at = (NEW).updated_at
    )
  THEN
    RAISE EXCEPTION 'source publication update lacks its exact event'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER source_registry_entries_s9_publication_guard
BEFORE UPDATE ON memory_patch.source_registry_entries
FOR EACH ROW
EXECUTE FUNCTION memory_patch.guard_source_registry_publication_update();

ALTER TABLE memory_patch.source_registry_entries OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.source_provenance_edges OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.source_publication_events OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_source_registry_publication_update()
  OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE
  memory_patch.source_registry_entries,
  memory_patch.source_provenance_edges,
  memory_patch.source_publication_events
FROM PUBLIC, mp_request_context_setter;
REVOKE EXECUTE
  ON FUNCTION memory_patch.guard_source_registry_publication_update()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;

GRANT SELECT, INSERT, UPDATE
  ON TABLE memory_patch.source_registry_entries
  TO mp_app_runtime;
GRANT SELECT, INSERT
  ON TABLE
    memory_patch.source_provenance_edges,
    memory_patch.source_publication_events
  TO mp_app_runtime;

ALTER TABLE memory_patch.source_registry_entries
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_registry_entries
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_provenance_edges
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_provenance_edges
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_publication_events
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.source_publication_events
  FORCE ROW LEVEL SECURITY;

CREATE POLICY source_registry_entries_s9_select
  ON memory_patch.source_registry_entries
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_registry_entries_s9_insert
  ON memory_patch.source_registry_entries
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
    AND current_publication_state = 'REGISTERED'
    AND current_publication_sequence = 0
    AND current_publication_event_digest =
      '6d6e54df2447ab416012f2afbf0cdf857d2055a3ef05a3d9023b8561b20c9693'
  );

CREATE POLICY source_registry_entries_s9_update
  ON memory_patch.source_registry_entries
  FOR UPDATE
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  )
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_provenance_edges_s9_select
  ON memory_patch.source_provenance_edges
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_provenance_edges_s9_insert
  ON memory_patch.source_provenance_edges
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_publication_events_s9_select
  ON memory_patch.source_publication_events
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY source_publication_events_s9_insert
  ON memory_patch.source_publication_events
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
