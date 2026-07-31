-- Memory Patch Step 10 - durable idempotent ingestion saga 1A.
-- CockroachDB coordinates progress only. External storage evidence, parser
-- receipts, validators, models, and HATs gain no semantic authority here.

CREATE TABLE memory_patch.ingestion_sagas (
  tenant_id STRING NOT NULL,
  saga_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  owner_user_id STRING,
  knowledge_version_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  idempotency_key STRING NOT NULL,
  request_digest STRING NOT NULL,
  scope_digest STRING NOT NULL,
  source_registry_digest STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  content_length INT8 NOT NULL,
  media_type STRING NOT NULL,
  local_relative_path STRING NOT NULL,
  snapshot_id STRING NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL,
  retain_until TIMESTAMPTZ NOT NULL,
  current_milestone STRING NOT NULL,
  execution_disposition STRING NOT NULL,
  state_version INT8 NOT NULL,
  attempt_count INT8 NOT NULL,
  event_sequence INT8 NOT NULL,
  current_event_digest STRING NOT NULL,
  next_retry_at TIMESTAMPTZ,
  claim_token_digest STRING,
  claimed_at TIMESTAMPTZ,
  claim_expires_at TIMESTAMPTZ,
  quarantine_reason STRING,
  run_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  terminal_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, saga_id),
  UNIQUE (tenant_id, saga_id, hat_scope_id),
  UNIQUE (tenant_id, saga_id, run_digest),
  CONSTRAINT ingestion_sagas_registry_fk
    FOREIGN KEY (tenant_id, source_id, hat_scope_id)
    REFERENCES memory_patch.source_registry_entries (
      tenant_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT ingestion_sagas_version_fk
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
  CONSTRAINT ingestion_sagas_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT ingestion_sagas_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT ingestion_sagas_identity
    CHECK (
      saga_id ~ '^ingsaga-[0-9a-f]{64}$'
      AND btrim(source_id) <> ''
      AND btrim(hat_scope_id) <> ''
      AND btrim(knowledge_version_id) <> ''
      AND btrim(idempotency_key) <> ''
      AND char_length(idempotency_key) <= 512
      AND request_digest ~ '^[0-9a-f]{64}$'
      AND scope_digest ~ '^[0-9a-f]{64}$'
      AND source_registry_digest ~ '^[0-9a-f]{64}$'
      AND run_digest ~ '^[0-9a-f]{64}$'
      AND saga_id = 'ingsaga-' || run_digest
    ),
  CONSTRAINT ingestion_sagas_content
    CHECK (
      content_sha256 ~ '^[0-9a-f]{64}$'
      AND content_length >= 0
      AND content_length <= 67108864
      AND btrim(media_type) <> ''
      AND char_length(media_type) <= 255
      AND btrim(snapshot_id) <> ''
      AND char_length(snapshot_id) <= 255
      AND retain_until > captured_at
    ),
  CONSTRAINT ingestion_sagas_relative_path
    CHECK (
      btrim(local_relative_path) <> ''
      AND char_length(local_relative_path) <= 512
      AND left(local_relative_path, 1) <> '/'
      AND position(chr(92) IN local_relative_path) = 0
      AND local_relative_path NOT LIKE '%/../%'
      AND local_relative_path NOT LIKE '../%'
      AND local_relative_path NOT LIKE '%/..'
      AND local_relative_path <> '..'
    ),
  CONSTRAINT ingestion_sagas_milestone
    CHECK (
      current_milestone IN (
        'REGISTERED',
        'ACQUIRED_LOCAL',
        'HASH_VERIFIED',
        'SNAPSHOT_UPLOAD_PENDING',
        'SNAPSHOT_UPLOADED',
        'SNAPSHOT_LOCK_VERIFIED',
        'PARSED',
        'VALIDATED',
        'PUBLISHED'
      )
    ),
  CONSTRAINT ingestion_sagas_disposition
    CHECK (
      execution_disposition IN (
        'READY',
        'CLAIMED',
        'RETRY_WAIT',
        'OPERATOR_REVIEW',
        'QUARANTINED',
        'COMPLETED'
      )
    ),
  CONSTRAINT ingestion_sagas_counters
    CHECK (
      state_version >= 0
      AND attempt_count >= 0
      AND event_sequence >= 0
      AND current_event_digest ~ '^[0-9a-f]{64}$'
      AND (
        event_sequence > 0
        OR (
          current_milestone = 'REGISTERED'
          AND current_event_digest =
            'bcb38ae02e4305d0bec56081a371aeec436fec4a60e09bfbf4c7b3818f1e8b93'
        )
      )
    ),
  CONSTRAINT ingestion_sagas_claim
    CHECK (
      (
        execution_disposition = 'CLAIMED'
        AND claim_token_digest ~ '^[0-9a-f]{64}$'
        AND claimed_at IS NOT NULL
        AND claim_expires_at > claimed_at
      )
      OR
      (
        execution_disposition <> 'CLAIMED'
        AND claim_token_digest IS NULL
        AND claimed_at IS NULL
        AND claim_expires_at IS NULL
      )
    ),
  CONSTRAINT ingestion_sagas_retry
    CHECK (
      (execution_disposition = 'RETRY_WAIT' AND next_retry_at IS NOT NULL)
      OR
      (execution_disposition <> 'RETRY_WAIT' AND next_retry_at IS NULL)
    ),
  CONSTRAINT ingestion_sagas_quarantine
    CHECK (
      (
        execution_disposition = 'QUARANTINED'
        AND quarantine_reason ~ '^[A-Z0-9][A-Z0-9_:-]{0,127}$'
      )
      OR
      (
        execution_disposition <> 'QUARANTINED'
        AND quarantine_reason IS NULL
      )
    ),
  CONSTRAINT ingestion_sagas_terminal
    CHECK (
      (
        current_milestone = 'PUBLISHED'
        AND execution_disposition = 'COMPLETED'
        AND terminal_at IS NOT NULL
      )
      OR
      (
        current_milestone <> 'PUBLISHED'
        AND execution_disposition <> 'COMPLETED'
        AND terminal_at IS NULL
      )
    ),
  CONSTRAINT ingestion_sagas_time_order
    CHECK (
      updated_at >= created_at
      AND (
        terminal_at IS NULL
        OR (
          terminal_at >= created_at
          AND terminal_at <= updated_at
        )
      )
    )
);

CREATE UNIQUE INDEX ingestion_sagas_shared_idempotency_uq
  ON memory_patch.ingestion_sagas (
    tenant_id,
    idempotency_key
  )
  WHERE owner_user_id IS NULL;

CREATE UNIQUE INDEX ingestion_sagas_private_idempotency_uq
  ON memory_patch.ingestion_sagas (
    tenant_id,
    owner_user_id,
    idempotency_key
  )
  WHERE owner_user_id IS NOT NULL;

CREATE TABLE memory_patch.ingestion_saga_events (
  tenant_id STRING NOT NULL,
  saga_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  event_id STRING NOT NULL,
  sequence_number INT8 NOT NULL,
  from_milestone STRING NOT NULL,
  to_milestone STRING NOT NULL,
  reason_code STRING NOT NULL,
  actor_boundary STRING NOT NULL,
  idempotency_reference STRING NOT NULL,
  prerequisite_receipt_digests JSONB NOT NULL,
  previous_event_digest STRING NOT NULL,
  event_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, saga_id, event_id),
  UNIQUE (tenant_id, saga_id, sequence_number),
  UNIQUE (tenant_id, saga_id, event_digest),
  CONSTRAINT ingestion_saga_events_saga_fk
    FOREIGN KEY (tenant_id, saga_id, hat_scope_id)
    REFERENCES memory_patch.ingestion_sagas (
      tenant_id,
      saga_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT ingestion_saga_events_identity
    CHECK (
      event_id ~ '^ingevent-[0-9a-f]{64}$'
      AND sequence_number > 0
      AND reason_code ~ '^[A-Z0-9][A-Z0-9_:-]{0,127}$'
      AND btrim(actor_boundary) <> ''
      AND char_length(actor_boundary) <= 128
      AND btrim(idempotency_reference) <> ''
      AND char_length(idempotency_reference) <= 512
    ),
  CONSTRAINT ingestion_saga_events_edge
    CHECK (
      (
        from_milestone = 'REGISTERED'
        AND to_milestone = 'ACQUIRED_LOCAL'
      )
      OR (
        from_milestone = 'ACQUIRED_LOCAL'
        AND to_milestone = 'HASH_VERIFIED'
      )
      OR (
        from_milestone = 'HASH_VERIFIED'
        AND to_milestone = 'SNAPSHOT_UPLOAD_PENDING'
      )
      OR (
        from_milestone = 'SNAPSHOT_UPLOAD_PENDING'
        AND to_milestone = 'SNAPSHOT_UPLOADED'
      )
      OR (
        from_milestone = 'SNAPSHOT_UPLOADED'
        AND to_milestone = 'SNAPSHOT_LOCK_VERIFIED'
      )
      OR (
        from_milestone = 'SNAPSHOT_LOCK_VERIFIED'
        AND to_milestone = 'PARSED'
      )
      OR (
        from_milestone = 'PARSED'
        AND to_milestone = 'VALIDATED'
      )
      OR (
        from_milestone = 'VALIDATED'
        AND to_milestone = 'PUBLISHED'
      )
    ),
  CONSTRAINT ingestion_saga_events_prerequisites
    CHECK (
      jsonb_typeof(prerequisite_receipt_digests) = 'array'
      AND jsonb_array_length(prerequisite_receipt_digests) = 1
      AND octet_length(prerequisite_receipt_digests::STRING) <= 256
    ),
  CONSTRAINT ingestion_saga_events_chain
    CHECK (
      previous_event_digest ~ '^[0-9a-f]{64}$'
      AND event_digest ~ '^[0-9a-f]{64}$'
      AND previous_event_digest <> event_digest
    )
);

CREATE TABLE memory_patch.ingestion_external_effects (
  tenant_id STRING NOT NULL,
  saga_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  effect_id STRING NOT NULL,
  effect_kind STRING NOT NULL,
  deterministic_locator STRING NOT NULL,
  expected_snapshot_id STRING NOT NULL,
  expected_sha256 STRING NOT NULL,
  expected_length INT8 NOT NULL,
  intent_digest STRING NOT NULL,
  intent_created_at TIMESTAMPTZ NOT NULL,
  status STRING NOT NULL,
  evidence_digest STRING,
  evidence JSONB,
  receipt_digest STRING,
  completed_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, saga_id, effect_id),
  UNIQUE (tenant_id, saga_id, effect_kind),
  UNIQUE (tenant_id, saga_id, intent_digest),
  UNIQUE (tenant_id, saga_id, receipt_digest),
  CONSTRAINT ingestion_external_effects_saga_fk
    FOREIGN KEY (tenant_id, saga_id, hat_scope_id)
    REFERENCES memory_patch.ingestion_sagas (
      tenant_id,
      saga_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT ingestion_external_effects_identity
    CHECK (
      effect_id ~ '^ingeffect-[0-9a-f]{64}$'
      AND effect_kind IN (
        'ACQUISITION',
        'HASH_VERIFICATION',
        'S3_UPLOAD',
        'S3_LOCK_VERIFICATION',
        'PARSE',
        'VALIDATION',
        'PUBLICATION'
      )
      AND intent_digest ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT ingestion_external_effects_locator
    CHECK (
      btrim(deterministic_locator) <> ''
      AND char_length(deterministic_locator) <= 1024
      AND left(deterministic_locator, 1) <> '/'
      AND position(chr(92) IN deterministic_locator) = 0
      AND btrim(expected_snapshot_id) <> ''
      AND char_length(expected_snapshot_id) <= 255
      AND expected_sha256 ~ '^[0-9a-f]{64}$'
      AND expected_length >= 0
      AND expected_length <= 67108864
    ),
  CONSTRAINT ingestion_external_effects_status
    CHECK (status IN ('INTENT_RECORDED', 'RECEIPT_RECORDED')),
  CONSTRAINT ingestion_external_effects_receipt_shape
    CHECK (
      (
        status = 'INTENT_RECORDED'
        AND evidence_digest IS NULL
        AND evidence IS NULL
        AND receipt_digest IS NULL
        AND completed_at IS NULL
      )
      OR
      (
        status = 'RECEIPT_RECORDED'
        AND evidence_digest ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(evidence) = 'object'
        AND octet_length(evidence::STRING) <= 32768
        AND receipt_digest ~ '^[0-9a-f]{64}$'
        AND completed_at >= intent_created_at
      )
    )
);

CREATE TABLE memory_patch.ingestion_orphans (
  tenant_id STRING NOT NULL,
  orphan_id STRING NOT NULL,
  saga_id STRING,
  hat_scope_id STRING,
  backend STRING NOT NULL,
  deterministic_locator STRING NOT NULL,
  expected_snapshot_id STRING NOT NULL,
  observed_evidence_digest STRING NOT NULL,
  classification STRING NOT NULL,
  resolution STRING NOT NULL,
  reason_code STRING NOT NULL,
  retention_constraint STRING NOT NULL,
  cleanup_performed BOOL NOT NULL,
  record_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, orphan_id),
  UNIQUE (tenant_id, record_digest),
  CONSTRAINT ingestion_orphans_saga_fk
    FOREIGN KEY (tenant_id, saga_id, hat_scope_id)
    REFERENCES memory_patch.ingestion_sagas (
      tenant_id,
      saga_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT ingestion_orphans_link_shape
    CHECK (
      (saga_id IS NULL AND hat_scope_id IS NULL)
      OR
      (saga_id IS NOT NULL AND hat_scope_id IS NOT NULL)
    ),
  CONSTRAINT ingestion_orphans_identity
    CHECK (
      orphan_id ~ '^ingorphan-[0-9a-f]{64}$'
      AND record_digest ~ '^[0-9a-f]{64}$'
      AND backend IN ('EXTERNAL_VOLUME', 'S3')
      AND btrim(deterministic_locator) <> ''
      AND char_length(deterministic_locator) <= 1024
      AND left(deterministic_locator, 1) <> '/'
      AND position(chr(92) IN deterministic_locator) = 0
      AND btrim(expected_snapshot_id) <> ''
      AND char_length(expected_snapshot_id) <= 255
      AND observed_evidence_digest ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT ingestion_orphans_classification
    CHECK (
      classification IN (
        'EXACT_EVIDENCE',
        'EXACT_DUPLICATE',
        'EXPECTED_ABSENT',
        'DATABASE_RECEIPT_EXTERNAL_MISSING',
        'CONFLICTING_EXTERNAL_EVIDENCE',
        'AMBIGUOUS_EXTERNAL_EVIDENCE',
        'RETENTION_BLOCKED'
      )
    ),
  CONSTRAINT ingestion_orphans_resolution
    CHECK (
      resolution IN (
        'UNRESOLVED',
        'ATTACHED',
        'DUPLICATE_RECORDED',
        'QUARANTINED',
        'OPERATOR_REVIEW',
        'CLEANUP_ELIGIBLE_AFTER_POLICY'
      )
    ),
  CONSTRAINT ingestion_orphans_non_destructive
    CHECK (
      cleanup_performed = false
      AND reason_code ~ '^[A-Z0-9][A-Z0-9_:-]{0,127}$'
      AND retention_constraint ~ '^[A-Z0-9][A-Z0-9_:-]{0,127}$'
    )
);

CREATE OR REPLACE FUNCTION memory_patch.guard_ingestion_saga_update()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (OLD).execution_disposition = 'QUARANTINED' THEN
    RAISE EXCEPTION 'quarantined ingestion saga requires a future reviewed release'
      USING ERRCODE = '42501';
  END IF;
  IF (OLD).current_milestone = 'PUBLISHED' THEN
    RAISE EXCEPTION 'published ingestion saga is terminal'
      USING ERRCODE = '42501';
  END IF;
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).saga_id IS DISTINCT FROM (OLD).saga_id
    OR (NEW).source_id IS DISTINCT FROM (OLD).source_id
    OR (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
    OR (NEW).owner_user_id IS DISTINCT FROM (OLD).owner_user_id
    OR (NEW).knowledge_version_id IS DISTINCT FROM (OLD).knowledge_version_id
    OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
    OR (NEW).idempotency_key IS DISTINCT FROM (OLD).idempotency_key
    OR (NEW).request_digest IS DISTINCT FROM (OLD).request_digest
    OR (NEW).scope_digest IS DISTINCT FROM (OLD).scope_digest
    OR (NEW).source_registry_digest IS DISTINCT FROM (OLD).source_registry_digest
    OR (NEW).content_sha256 IS DISTINCT FROM (OLD).content_sha256
    OR (NEW).content_length IS DISTINCT FROM (OLD).content_length
    OR (NEW).media_type IS DISTINCT FROM (OLD).media_type
    OR (NEW).local_relative_path IS DISTINCT FROM (OLD).local_relative_path
    OR (NEW).snapshot_id IS DISTINCT FROM (OLD).snapshot_id
    OR (NEW).captured_at IS DISTINCT FROM (OLD).captured_at
    OR (NEW).retain_until IS DISTINCT FROM (OLD).retain_until
    OR (NEW).run_digest IS DISTINCT FROM (OLD).run_digest
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
  THEN
    RAISE EXCEPTION 'ingestion saga identity is immutable'
      USING ERRCODE = '42501';
  END IF;
  IF (NEW).state_version <> (OLD).state_version + 1
    OR (NEW).attempt_count < (OLD).attempt_count
    OR (NEW).attempt_count > (OLD).attempt_count + 1
    OR (NEW).updated_at < (OLD).updated_at
  THEN
    RAISE EXCEPTION 'ingestion saga mutable version is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF (NEW).attempt_count = (OLD).attempt_count + 1
    AND (NEW).execution_disposition <> 'CLAIMED'
  THEN
    RAISE EXCEPTION 'ingestion attempt increment requires a worker claim'
      USING ERRCODE = '23514';
  END IF;
  IF (NEW).current_milestone IS DISTINCT FROM (OLD).current_milestone THEN
    IF (NEW).event_sequence <> (OLD).event_sequence + 1
      OR (NEW).current_event_digest
        IS NOT DISTINCT FROM (OLD).current_event_digest
      OR NOT EXISTS (
        SELECT 1
          FROM memory_patch.ingestion_saga_events AS event
         WHERE event.tenant_id = (OLD).tenant_id
           AND event.saga_id = (OLD).saga_id
           AND event.hat_scope_id = (OLD).hat_scope_id
           AND event.sequence_number = (NEW).event_sequence
           AND event.from_milestone = (OLD).current_milestone
           AND event.to_milestone = (NEW).current_milestone
           AND event.previous_event_digest = (OLD).current_event_digest
           AND event.event_digest = (NEW).current_event_digest
           AND event.created_at = (NEW).updated_at
      )
    THEN
      RAISE EXCEPTION 'ingestion milestone update lacks its exact event'
        USING ERRCODE = '23514';
    END IF;
  ELSIF (NEW).event_sequence IS DISTINCT FROM (OLD).event_sequence
    OR (NEW).current_event_digest IS DISTINCT FROM (OLD).current_event_digest
    OR (NEW).terminal_at IS DISTINCT FROM (OLD).terminal_at
  THEN
    RAISE EXCEPTION 'ingestion event pointer changed without a milestone'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER ingestion_sagas_s10_state_guard
BEFORE UPDATE ON memory_patch.ingestion_sagas
FOR EACH ROW
EXECUTE FUNCTION memory_patch.guard_ingestion_saga_update();

CREATE OR REPLACE FUNCTION memory_patch.guard_ingestion_effect_receipt_update()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).saga_id IS DISTINCT FROM (OLD).saga_id
    OR (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
    OR (NEW).effect_id IS DISTINCT FROM (OLD).effect_id
    OR (NEW).effect_kind IS DISTINCT FROM (OLD).effect_kind
    OR (NEW).deterministic_locator
      IS DISTINCT FROM (OLD).deterministic_locator
    OR (NEW).expected_snapshot_id IS DISTINCT FROM (OLD).expected_snapshot_id
    OR (NEW).expected_sha256 IS DISTINCT FROM (OLD).expected_sha256
    OR (NEW).expected_length IS DISTINCT FROM (OLD).expected_length
    OR (NEW).intent_digest IS DISTINCT FROM (OLD).intent_digest
    OR (NEW).intent_created_at IS DISTINCT FROM (OLD).intent_created_at
  THEN
    RAISE EXCEPTION 'ingestion external intent is immutable'
      USING ERRCODE = '42501';
  END IF;
  IF (OLD).status <> 'INTENT_RECORDED'
    OR (NEW).status <> 'RECEIPT_RECORDED'
  THEN
    RAISE EXCEPTION 'ingestion external receipt is append-once'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER ingestion_external_effects_s10_receipt_guard
BEFORE UPDATE ON memory_patch.ingestion_external_effects
FOR EACH ROW
EXECUTE FUNCTION memory_patch.guard_ingestion_effect_receipt_update();

ALTER TABLE memory_patch.ingestion_sagas OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.ingestion_saga_events OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.ingestion_external_effects OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.ingestion_orphans OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_ingestion_saga_update()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_ingestion_effect_receipt_update()
  OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE
  memory_patch.ingestion_sagas,
  memory_patch.ingestion_saga_events,
  memory_patch.ingestion_external_effects,
  memory_patch.ingestion_orphans
FROM PUBLIC, mp_request_context_setter;
REVOKE EXECUTE
  ON FUNCTION memory_patch.guard_ingestion_saga_update()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE EXECUTE
  ON FUNCTION memory_patch.guard_ingestion_effect_receipt_update()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;

GRANT SELECT, INSERT, UPDATE
  ON TABLE memory_patch.ingestion_sagas
  TO mp_app_runtime;
GRANT SELECT, INSERT
  ON TABLE memory_patch.ingestion_saga_events
  TO mp_app_runtime;
GRANT SELECT, INSERT, UPDATE
  ON TABLE memory_patch.ingestion_external_effects
  TO mp_app_runtime;
GRANT SELECT, INSERT
  ON TABLE memory_patch.ingestion_orphans
  TO mp_app_runtime;

ALTER TABLE memory_patch.ingestion_sagas
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_sagas
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_saga_events
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_saga_events
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_external_effects
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_external_effects
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_orphans
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.ingestion_orphans
  FORCE ROW LEVEL SECURITY;

CREATE POLICY ingestion_sagas_s10_select
  ON memory_patch.ingestion_sagas
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));

CREATE POLICY ingestion_sagas_s10_insert
  ON memory_patch.ingestion_sagas
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
    AND current_milestone = 'REGISTERED'
    AND execution_disposition = 'READY'
    AND state_version = 0
    AND attempt_count = 0
    AND event_sequence = 0
  );

CREATE POLICY ingestion_sagas_s10_update
  ON memory_patch.ingestion_sagas
  FOR UPDATE
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id))
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY ingestion_saga_events_s10_select
  ON memory_patch.ingestion_saga_events
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));

CREATE POLICY ingestion_saga_events_s10_insert
  ON memory_patch.ingestion_saga_events
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY ingestion_external_effects_s10_select
  ON memory_patch.ingestion_external_effects
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));

CREATE POLICY ingestion_external_effects_s10_insert
  ON memory_patch.ingestion_external_effects
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY ingestion_external_effects_s10_update
  ON memory_patch.ingestion_external_effects
  FOR UPDATE
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id))
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY ingestion_orphans_s10_select
  ON memory_patch.ingestion_orphans
  FOR SELECT
  TO mp_app_runtime
  USING (
    CASE
      WHEN hat_scope_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
    END
  );

CREATE POLICY ingestion_orphans_s10_insert
  ON memory_patch.ingestion_orphans
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    CASE
      WHEN hat_scope_id IS NULL
      THEN memory_patch.tenant_context_matches(tenant_id)
      ELSE memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
    END
  );
