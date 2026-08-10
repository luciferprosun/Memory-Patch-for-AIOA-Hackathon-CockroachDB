-- Memory Patch Step 33 - append-only owner/tenant audit chains and bounded
-- proof exports.  The ledger mirrors immutable facts; it grants no approval,
-- commit, activation, revocation, deletion, publication, or execution power.

ALTER TABLE memory_patch.audit_events
  ADD COLUMN recorded_at TIMESTAMPTZ;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN chain_id STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN sequence_number INT8;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN idempotency_key STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN draft_hash STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN subject_type STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN subject_id STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN subject_hash STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN request_id STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN policy_id STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN policy_version STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN policy_digest STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN route_hash STRING;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN lineage_hashes JSONB;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN reason_codes JSONB;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN step33_envelope JSONB;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN step33_payload JSONB;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN step33_append_receipt JSONB;
ALTER TABLE memory_patch.audit_events
  ADD COLUMN step33_entry_hash STRING;

ALTER TABLE memory_patch.audit_events
  DROP CONSTRAINT audit_events_schema_version;
ALTER TABLE memory_patch.audit_events
  ADD CONSTRAINT audit_events_schema_version
    CHECK (schema_version IN ('1.0.0', 'audit-event-envelope-1.0.0'));

ALTER TABLE memory_patch.audit_events
  DROP CONSTRAINT audit_events_actor_type;
ALTER TABLE memory_patch.audit_events
  ADD CONSTRAINT audit_events_actor_type
    CHECK (
      actor_type IN (
        'USER', 'HUMAN_REVIEWER', 'SYSTEM', 'COMMIT_SERVICE',
        'KNOWLEDGE_KERNEL', 'KNOWLEDGE_HAT', 'KNOWLEDGE_HUB',
        'CRITIC_PROMPT_LOOP', 'MODEL', 'MODEL_VERIFIER',
        'MIGRATION_SERVICE', 'HUMAN_USER', 'KERNEL', 'MODEL_ADAPTER',
        'CRITIC_LOOP', 'COMMIT_HELPER', 'ACTIVATION_SERVICE',
        'SYSTEM_POLICY', 'REVIEW_SERVICE'
      )
    );

-- Step 4 paired owner+space rows remain valid.  Step 33 also permits an
-- owner-private, non-space event (for example a verified-answer audit fact).
ALTER TABLE memory_patch.audit_events
  DROP CONSTRAINT audit_events_scope_pair;
ALTER TABLE memory_patch.audit_events
  ADD CONSTRAINT audit_events_scope_pair
    CHECK (
      (user_id IS NULL AND personal_memory_space_id IS NULL)
      OR
      (
        user_id IS NOT NULL AND btrim(user_id) <> ''
        AND (
          personal_memory_space_id IS NULL
          OR btrim(personal_memory_space_id) <> ''
        )
      )
    );

ALTER TABLE memory_patch.audit_events
  ADD CONSTRAINT audit_events_s33_complete_shape
    CHECK (
      (
        chain_id IS NULL
        AND recorded_at IS NULL
        AND sequence_number IS NULL
        AND idempotency_key IS NULL
        AND draft_hash IS NULL
        AND subject_type IS NULL
        AND subject_id IS NULL
        AND subject_hash IS NULL
        AND lineage_hashes IS NULL
        AND reason_codes IS NULL
        AND step33_envelope IS NULL
        AND step33_payload IS NULL
        AND step33_append_receipt IS NULL
        AND step33_entry_hash IS NULL
      )
      OR
      (
        schema_version = 'audit-event-envelope-1.0.0'
        AND recorded_at IS NOT NULL
        AND chain_id IS NOT NULL
        AND sequence_number IS NOT NULL
        AND idempotency_key IS NOT NULL
        AND draft_hash IS NOT NULL
        AND subject_type IS NOT NULL
        AND subject_id IS NOT NULL
        AND subject_hash IS NOT NULL
        AND previous_event_hash IS NOT NULL
        AND lineage_hashes IS NOT NULL
        AND reason_codes IS NOT NULL
        AND step33_envelope IS NOT NULL
        AND step33_payload IS NOT NULL
        AND step33_append_receipt IS NOT NULL
        AND step33_entry_hash IS NOT NULL
        AND event_type IN (
          'KERNEL_REQUEST_RECEIVED', 'ROUTE_DECIDED',
          'KNOWLEDGE_POLICY_DECIDED', 'EVIDENCE_BUNDLE_CREATED',
          'TEMPORAL_RESOLUTION_COMPLETED', 'DRAFT_V1_GENERATED',
          'CLAIM_EVIDENCE_VALIDATED', 'CORRECTION_PACKET_CREATED',
          'DRAFT_V2_VERIFIED', 'VERIFIED_ANSWER_ASSEMBLED',
          'VERIFIED_ANSWER_BLOCKED', 'PERSONAL_MEMORY_SLOT_CREATED',
          'PERSONAL_MEMORY_SLOT_CONFIGURED',
          'PERSONAL_MEMORY_SLOT_STATE_CHANGED',
          'CORRECTION_CANDIDATE_DETECTED',
          'PERSONAL_MEMORY_PROPOSAL_CREATED',
          'PERSONAL_MEMORY_EVIDENCE_BOUND', 'PERSONAL_MEMORY_VALIDATED',
          'PERSONAL_MEMORY_AWAITING_APPROVAL', 'PERSONAL_MEMORY_APPROVED',
          'PERSONAL_MEMORY_COMMITTED', 'PERSONAL_MEMORY_ACTIVATED',
          'PERSONAL_MEMORY_SUPERSEDED', 'PERSONAL_MEMORY_REVOKED',
          'PERSONAL_MEMORY_EXPORTED', 'PERSONAL_MEMORY_DELETE_REQUESTED',
          'PERSONAL_MEMORY_DELETED', 'SHARED_PROMOTION_PROPOSED',
          'DEIDENTIFICATION_REVIEW_REQUIRED', 'IDEMPOTENCY_CONFLICT',
          'INTEGRITY_FAILURE', 'OWNER_SCOPE_DENIED',
          'TENANT_SCOPE_DENIED', 'POLICY_BLOCKED'
        )
        AND actor_type IN (
          'HUMAN_USER', 'KERNEL', 'MODEL_ADAPTER', 'CRITIC_LOOP',
          'COMMIT_HELPER', 'ACTIVATION_SERVICE', 'SYSTEM_POLICY',
          'REVIEW_SERVICE', 'MIGRATION_SERVICE'
        )
        AND event_id ~ '^audit-event-[0-9a-f]{64}$'
        AND chain_id ~ '^audit-chain-[0-9a-f]{64}$'
        AND sequence_number >= 1
        AND btrim(idempotency_key) <> ''
        AND draft_hash ~ '^[0-9a-f]{64}$'
        AND subject_type IN (
          'KERNEL_RUN', 'ROUTE_RESULT', 'POLICY_RESULT',
          'EVIDENCE_BUNDLE', 'TEMPORAL_RESULT', 'DRAFT',
          'CLAIM_ASSESSMENT', 'CORRECTION_PACKET', 'VERIFIED_ANSWER',
          'PERSONAL_MEMORY_SLOT', 'CORRECTION_CANDIDATE',
          'PERSONAL_MEMORY_PROPOSAL', 'PERSONAL_MEMORY_PATCH',
          'PERSONAL_MEMORY_EXPORT', 'PERSONAL_MEMORY_DELETION',
          'SHARED_PROMOTION_PROPOSAL', 'SECURITY_EVENT'
        )
        AND btrim(subject_id) <> ''
        AND subject_hash ~ '^[0-9a-f]{64}$'
        AND previous_event_hash ~ '^[0-9a-f]{64}$'
        AND payload_hash ~ '^[0-9a-f]{64}$'
        AND event_hash ~ '^[0-9a-f]{64}$'
        AND recorded_at >= occurred_at
        AND (policy_id IS NULL) = (policy_version IS NULL)
        AND (policy_id IS NULL) = (policy_digest IS NULL)
        AND (policy_digest IS NULL OR policy_digest ~ '^[0-9a-f]{64}$')
        AND (route_hash IS NULL OR route_hash ~ '^[0-9a-f]{64}$')
        AND jsonb_typeof(lineage_hashes) = 'object'
        AND jsonb_typeof(reason_codes) = 'array'
        AND jsonb_array_length(reason_codes) BETWEEN 1 AND 32
        AND jsonb_typeof(step33_envelope) = 'object'
        AND jsonb_typeof(step33_payload) = 'object'
        AND octet_length(step33_payload::STRING) <= 16384
        AND jsonb_typeof(step33_append_receipt) = 'object'
        AND step33_entry_hash ~ '^[0-9a-f]{64}$'
        AND metadata ->> 'step33_entry_hash' IS NOT NULL
        AND metadata ->> 'step33_entry_hash' = step33_entry_hash
        AND step33_envelope -> 'schema_version' IS NOT NULL
        AND step33_envelope -> 'event_type' IS NOT NULL
        AND step33_envelope -> 'tenant_id' IS NOT NULL
        AND step33_envelope -> 'owner_user_id' IS NOT NULL
        AND step33_envelope -> 'personal_memory_space_id' IS NOT NULL
        AND step33_envelope -> 'kernel_run_id' IS NOT NULL
        AND step33_envelope -> 'request_id' IS NOT NULL
        AND step33_envelope -> 'subject_type' IS NOT NULL
        AND step33_envelope -> 'subject_id' IS NOT NULL
        AND step33_envelope -> 'subject_hash' IS NOT NULL
        AND step33_envelope -> 'actor_type' IS NOT NULL
        AND step33_envelope -> 'actor_id' IS NOT NULL
        AND step33_envelope -> 'policy_id' IS NOT NULL
        AND step33_envelope -> 'policy_version' IS NOT NULL
        AND step33_envelope -> 'policy_digest' IS NOT NULL
        AND step33_envelope -> 'route_hash' IS NOT NULL
        AND step33_envelope -> 'lineage_hashes' IS NOT NULL
        AND step33_envelope -> 'reason_codes' IS NOT NULL
        AND step33_envelope -> 'sequence_number' IS NOT NULL
        AND step33_envelope -> 'chain_id' IS NOT NULL
        AND step33_envelope -> 'previous_event_hash' IS NOT NULL
        AND step33_envelope -> 'event_payload_digest' IS NOT NULL
        AND step33_envelope -> 'idempotency_key' IS NOT NULL
        AND step33_envelope -> 'draft_hash' IS NOT NULL
        AND step33_envelope -> 'occurred_at' IS NOT NULL
        AND step33_envelope -> 'recorded_at' IS NOT NULL
        AND step33_envelope -> 'hash_domain' IS NOT NULL
        AND step33_envelope -> 'event_id' IS NOT NULL
        AND step33_envelope -> 'event_hash' IS NOT NULL
        AND step33_envelope ->> 'schema_version'
          = 'audit-event-envelope-1.0.0'
        AND step33_envelope ->> 'hash_domain'
          = 'MEMORY_PATCH_AUDIT_EVENT_V1'
        AND step33_envelope ->> 'event_id' = event_id
        AND step33_envelope ->> 'event_hash' = event_hash
        AND step33_envelope ->> 'event_type' = event_type
        AND step33_envelope ->> 'actor_type' = actor_type
        AND step33_envelope ->> 'actor_id' = actor_id
        AND step33_envelope ->> 'tenant_id' = tenant_id
        AND (step33_envelope ->> 'owner_user_id')
          IS NOT DISTINCT FROM user_id
        AND (step33_envelope ->> 'personal_memory_space_id')
          IS NOT DISTINCT FROM personal_memory_space_id
        AND (step33_envelope ->> 'kernel_run_id')
          IS NOT DISTINCT FROM kernel_run_id
        AND (step33_envelope ->> 'request_id')
          IS NOT DISTINCT FROM request_id
        AND step33_envelope ->> 'chain_id' = chain_id
        AND (step33_envelope ->> 'sequence_number')::INT8
          = sequence_number
        AND step33_envelope ->> 'idempotency_key' = idempotency_key
        AND step33_envelope ->> 'draft_hash' = draft_hash
        AND step33_envelope ->> 'subject_type' = subject_type
        AND step33_envelope ->> 'subject_id' = subject_id
        AND step33_envelope ->> 'subject_hash' = subject_hash
        AND step33_envelope ->> 'previous_event_hash'
          = previous_event_hash
        AND step33_envelope ->> 'event_payload_digest' = payload_hash
        AND (step33_envelope ->> 'occurred_at')::TIMESTAMPTZ
          = occurred_at
        AND (step33_envelope ->> 'recorded_at')::TIMESTAMPTZ
          = recorded_at
        AND (step33_envelope ->> 'policy_id')
          IS NOT DISTINCT FROM policy_id
        AND (step33_envelope ->> 'policy_version')
          IS NOT DISTINCT FROM policy_version
        AND (step33_envelope ->> 'policy_digest')
          IS NOT DISTINCT FROM policy_digest
        AND (step33_envelope ->> 'route_hash')
          IS NOT DISTINCT FROM route_hash
        AND step33_envelope -> 'lineage_hashes' = lineage_hashes
        AND step33_envelope -> 'reason_codes' = reason_codes
        AND step33_append_receipt -> 'chain_id' IS NOT NULL
        AND step33_append_receipt -> 'sequence_number' IS NOT NULL
        AND step33_append_receipt -> 'event_id' IS NOT NULL
        AND step33_append_receipt -> 'event_hash' IS NOT NULL
        AND step33_append_receipt -> 'previous_event_hash' IS NOT NULL
        AND step33_append_receipt -> 'subject_type' IS NOT NULL
        AND step33_append_receipt -> 'subject_id' IS NOT NULL
        AND step33_append_receipt -> 'subject_hash' IS NOT NULL
        AND step33_append_receipt -> 'idempotency_key' IS NOT NULL
        AND step33_append_receipt -> 'receipt_hash' IS NOT NULL
        AND step33_append_receipt ->> 'event_id' = event_id
        AND step33_append_receipt ->> 'event_hash' = event_hash
        AND step33_append_receipt ->> 'chain_id' = chain_id
        AND (step33_append_receipt ->> 'sequence_number')::INT8
          = sequence_number
        AND step33_append_receipt ->> 'previous_event_hash'
          = previous_event_hash
        AND step33_append_receipt ->> 'subject_type' = subject_type
        AND step33_append_receipt ->> 'subject_id' = subject_id
        AND step33_append_receipt ->> 'subject_hash' = subject_hash
        AND step33_append_receipt ->> 'idempotency_key'
          = idempotency_key
        AND step33_append_receipt ->> 'receipt_hash'
          ~ '^[0-9a-f]{64}$'
      )
    );

CREATE UNIQUE INDEX audit_events_s33_chain_sequence_uq
  ON memory_patch.audit_events (tenant_id, chain_id, sequence_number)
  WHERE chain_id IS NOT NULL;
CREATE UNIQUE INDEX audit_events_s33_idempotency_uq
  ON memory_patch.audit_events (tenant_id, chain_id, idempotency_key)
  WHERE chain_id IS NOT NULL;
CREATE INDEX audit_events_s33_owner_chain_range_idx
  ON memory_patch.audit_events (
    tenant_id, user_id, chain_id, sequence_number
  )
  WHERE chain_id IS NOT NULL;

CREATE TABLE memory_patch.audit_chain_heads (
  tenant_id STRING NOT NULL,
  owner_user_id STRING,
  chain_id STRING NOT NULL,
  last_sequence INT8 NOT NULL,
  last_event_hash STRING NOT NULL,
  head_version INT8 NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, chain_id),
  CONSTRAINT audit_chain_heads_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id)
    ON DELETE RESTRICT,
  CONSTRAINT audit_chain_heads_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT audit_chain_heads_shape CHECK (
    tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
    AND (
      owner_user_id IS NULL
      OR owner_user_id ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
    )
    AND chain_id = concat(
      'audit-chain-',
      sha256(
        concat(
          '{"domain":"MEMORY_PATCH_AUDIT_CHAIN_V1",',
          '"owner_user_id":',
          CASE
            WHEN owner_user_id IS NULL THEN 'null'
            ELSE concat('"', owner_user_id, '"')
          END,
          ',"partition_policy":"tenant-owner-audit-chain-1a",',
          '"tenant_id":"', tenant_id, '"}'
        )::BYTES
      )
    )
    AND last_sequence >= 0
    AND head_version = last_sequence
    AND last_event_hash ~ '^[0-9a-f]{64}$'
    AND (
      last_sequence <> 0
      OR last_event_hash = sha256('MEMORY_PATCH_AUDIT_GENESIS_V1'::BYTES)
    )
  )
);

CREATE OR REPLACE FUNCTION memory_patch.guard_step33_audit_append_only()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  RAISE EXCEPTION 'Step 33 audit events are append-only'
    USING ERRCODE = '42501';
  RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step33_chain_head()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).owner_user_id IS DISTINCT FROM (OLD).owner_user_id
    OR (NEW).chain_id IS DISTINCT FROM (OLD).chain_id
    OR (NEW).last_sequence <> (OLD).last_sequence + 1
    OR (NEW).head_version <> (OLD).head_version + 1
    OR (NEW).updated_at < (OLD).updated_at
    OR NOT EXISTS (
      SELECT 1
        FROM memory_patch.audit_events AS event
       WHERE event.tenant_id = (NEW).tenant_id
         AND event.user_id IS NOT DISTINCT FROM (NEW).owner_user_id
         AND event.chain_id = (NEW).chain_id
         AND event.sequence_number = (NEW).last_sequence
         AND event.event_hash = (NEW).last_event_hash
         AND event.previous_event_hash = (OLD).last_event_hash
    )
  THEN
    RAISE EXCEPTION 'Step 33 chain-head transition is detached'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS audit_events_s33_append_only
  ON memory_patch.audit_events;
CREATE TRIGGER audit_events_s33_append_only
  BEFORE UPDATE OR DELETE ON memory_patch.audit_events
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step33_audit_append_only();

DROP TRIGGER IF EXISTS audit_chain_heads_s33_guard
  ON memory_patch.audit_chain_heads;
CREATE TRIGGER audit_chain_heads_s33_guard
  BEFORE UPDATE ON memory_patch.audit_chain_heads
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step33_chain_head();

ALTER TABLE memory_patch.audit_chain_heads OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step33_audit_append_only()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step33_chain_head()
  OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE memory_patch.audit_chain_heads
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper;
GRANT SELECT, INSERT, UPDATE ON TABLE memory_patch.audit_chain_heads
TO mp_app_runtime;
REVOKE DELETE ON TABLE memory_patch.audit_chain_heads
FROM mp_app_runtime;

-- Reassert the exact append-only privilege surface on the reused Step 4 table.
REVOKE UPDATE, DELETE ON TABLE memory_patch.audit_events
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper;
GRANT SELECT, INSERT ON TABLE memory_patch.audit_events
TO mp_app_runtime;

REVOKE ALL ON FUNCTION
  memory_patch.guard_step33_audit_append_only(),
  memory_patch.guard_step33_chain_head()
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper;
GRANT EXECUTE ON FUNCTION
  memory_patch.guard_step33_audit_append_only(),
  memory_patch.guard_step33_chain_head()
TO mp_app_runtime;

ALTER TABLE memory_patch.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_chain_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_chain_heads FORCE ROW LEVEL SECURITY;

CREATE POLICY audit_chain_heads_s33_select
  ON memory_patch.audit_chain_heads
  FOR SELECT TO mp_app_runtime
  USING (
    memory_patch.tenant_context_matches(tenant_id)
    AND (
      owner_user_id IS NULL
      OR memory_patch.user_context_matches(tenant_id, owner_user_id)
    )
  );
CREATE POLICY audit_chain_heads_s33_insert
  ON memory_patch.audit_chain_heads
  FOR INSERT TO mp_app_runtime
  WITH CHECK (
    memory_patch.tenant_context_matches(tenant_id)
    AND (
      owner_user_id IS NULL
      OR memory_patch.user_context_matches(tenant_id, owner_user_id)
    )
  );
CREATE POLICY audit_chain_heads_s33_update
  ON memory_patch.audit_chain_heads
  FOR UPDATE TO mp_app_runtime
  USING (
    memory_patch.tenant_context_matches(tenant_id)
    AND (
      owner_user_id IS NULL
      OR memory_patch.user_context_matches(tenant_id, owner_user_id)
    )
  )
  WITH CHECK (
    memory_patch.tenant_context_matches(tenant_id)
    AND (
      owner_user_id IS NULL
      OR memory_patch.user_context_matches(tenant_id, owner_user_id)
    )
  );

COMMENT ON TABLE memory_patch.audit_chain_heads IS
  'Step 33 operational O(1) chain heads; append-only audit_events remain history authority.';
