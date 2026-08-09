-- Memory Patch Step 29 - owner-private proposal and evidence validation.
-- The only writable lifecycle is DETECTED -> PROPOSED -> EVIDENCE_BOUND ->
-- VALIDATED -> AWAITING_APPROVAL.  This migration grants no approval,
-- commit, activation, memory-item, retrieval, provider, or execution authority.

ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_dedup_key STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_candidate_id STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_candidate_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_candidate_envelope_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_target_binding_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_state_version INT8;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_state_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_evidence_binding_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step29_validation_receipt_hash STRING;

-- Materialize the two immutable Step 28 values needed by the Step 29
-- self-referential foreign key.  This is a deterministic metadata backfill;
-- candidate content and lifecycle state remain untouched.
UPDATE memory_patch.memory_patch_proposals
   SET step29_candidate_hash = proposed_content -> 'submission' -> 'candidate'
         ->> 'content_hash',
       step29_target_binding_hash = proposed_content -> 'submission'
         -> 'target_slot_binding' ->> 'target_binding_hash'
 WHERE proposed_content ->> 'contract_type' = 'CorrectionCandidateEnvelope';

ALTER TABLE memory_patch.memory_patch_proposals
  ADD CONSTRAINT memory_patch_proposals_s29_candidate_projection
    CHECK (
      proposed_content ->> 'contract_type' <> 'CorrectionCandidateEnvelope'
      OR (
        step29_candidate_hash ~ '^[0-9a-f]{64}$'
        AND step29_candidate_hash = proposed_content -> 'submission'
          -> 'candidate' ->> 'content_hash'
        AND step29_target_binding_hash ~ '^[0-9a-f]{64}$'
        AND step29_target_binding_hash = proposed_content -> 'submission'
          -> 'target_slot_binding' ->> 'target_binding_hash'
      )
    );

ALTER TABLE memory_patch.memory_patch_proposals
  ADD CONSTRAINT memory_patch_proposals_s29_candidate_lineage_unique
    UNIQUE (
      tenant_id,
      proposal_id,
      content_hash,
      step29_candidate_hash,
      step29_target_binding_hash,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    );

ALTER TABLE memory_patch.memory_patch_proposals
  ADD CONSTRAINT memory_patch_proposals_s29_state_tuple
    CHECK (
      (
        step29_dedup_key IS NULL
        AND step29_state_version IS NULL
        AND step29_state_hash IS NULL
        AND step29_evidence_binding_hash IS NULL
        AND step29_validation_receipt_hash IS NULL
      )
      OR
      (
        step29_dedup_key
          ~ '^personal-patch-dedup-[0-9a-f]{64}$'
        AND step29_candidate_id
          ~ '^correction-candidate-[0-9a-f]{64}$'
        AND step29_candidate_hash ~ '^[0-9a-f]{64}$'
        AND step29_candidate_envelope_hash ~ '^[0-9a-f]{64}$'
        AND step29_target_binding_hash ~ '^[0-9a-f]{64}$'
        AND step29_candidate_id
          = proposed_content -> 'proposal' ->> 'candidate_id'
        AND step29_candidate_hash
          = proposed_content -> 'proposal' ->> 'candidate_hash'
        AND step29_candidate_envelope_hash
          = proposed_content -> 'proposal' ->> 'candidate_envelope_hash'
        AND step29_target_binding_hash
          = proposed_content -> 'proposal' ->> 'target_binding_hash'
        AND step29_target_binding_hash
          = proposed_content -> 'proposal' -> 'target_slot_binding'
            ->> 'target_binding_hash'
        AND step29_state_version IN (1, 2, 3, 4)
        AND step29_state_hash ~ '^[0-9a-f]{64}$'
        AND proposed_content ->> 'contract_type'
          = 'PersonalMemoryPatchProposalState'
        AND proposed_content ->> 'schema_version' = '1.0.0'
        AND lifecycle_state = proposed_content ->> 'state'
        AND step29_state_version
          = (proposed_content ->> 'state_version')::INT8
        AND step29_state_hash = proposed_content ->> 'state_hash'
        AND (
          (step29_state_version = 1
            AND lifecycle_state = 'PROPOSED'
            AND step29_evidence_binding_hash IS NULL
            AND step29_validation_receipt_hash IS NULL)
          OR
          (step29_state_version = 2
            AND lifecycle_state = 'EVIDENCE_BOUND'
            AND step29_evidence_binding_hash ~ '^[0-9a-f]{64}$'
            AND step29_evidence_binding_hash
              = proposed_content -> 'evidence_binding' ->> 'binding_hash'
            AND step29_validation_receipt_hash IS NULL)
          OR
          (step29_state_version = 3
            AND lifecycle_state = 'VALIDATED'
            AND step29_evidence_binding_hash ~ '^[0-9a-f]{64}$'
            AND step29_evidence_binding_hash
              = proposed_content -> 'evidence_binding' ->> 'binding_hash'
            AND step29_validation_receipt_hash ~ '^[0-9a-f]{64}$'
            AND step29_validation_receipt_hash
              = proposed_content -> 'validation_receipt' ->> 'receipt_hash'
            AND (proposed_content -> 'validation_receipt'
              ->> 'validated')::BOOL)
          OR
          (step29_state_version = 4
            AND lifecycle_state = 'AWAITING_APPROVAL'
            AND step29_evidence_binding_hash ~ '^[0-9a-f]{64}$'
            AND step29_evidence_binding_hash
              = proposed_content -> 'evidence_binding' ->> 'binding_hash'
            AND step29_validation_receipt_hash ~ '^[0-9a-f]{64}$'
            AND step29_validation_receipt_hash
              = proposed_content -> 'validation_receipt' ->> 'receipt_hash'
            AND (proposed_content -> 'validation_receipt'
              ->> 'validated')::BOOL)
        )
      )
    );

ALTER TABLE memory_patch.memory_patch_proposals
  ADD CONSTRAINT memory_patch_proposals_s29_candidate_lineage_fk
    FOREIGN KEY (
      tenant_id,
      step29_candidate_id,
      step29_candidate_envelope_hash,
      step29_candidate_hash,
      step29_target_binding_hash,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    )
    REFERENCES memory_patch.memory_patch_proposals (
      tenant_id,
      proposal_id,
      content_hash,
      step29_candidate_hash,
      step29_target_binding_hash,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT;

CREATE UNIQUE INDEX memory_patch_proposals_s29_exact_dedup
  ON memory_patch.memory_patch_proposals (
    tenant_id,
    owner_user_id,
    personal_memory_space_id,
    step29_dedup_key
  )
  WHERE step29_dedup_key IS NOT NULL;

CREATE OR REPLACE FUNCTION memory_patch.step29_proposal_target_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_hat_scope_id STRING,
  p_candidate_id STRING,
  p_candidate_hash STRING,
  p_candidate_envelope_hash STRING,
  p_target_binding_hash STRING,
  p_slot_state STRING,
  p_slot_state_version INT8,
  p_slot_configuration_version INT8,
  p_slot_hash STRING,
  p_configuration_digest STRING,
  p_quota_policy_id STRING,
  p_quota_policy_digest STRING,
  p_model_binding_id STRING,
  p_model_binding_hash STRING,
  p_model_binding_version INT8,
  p_binding_mode STRING,
  p_provider_id STRING,
  p_model_id STRING,
  p_model_revision STRING,
  p_model_binding_enabled BOOL,
  p_route_hash STRING,
  p_effective_scope JSONB
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT
    memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND p_candidate_id ~ '^correction-candidate-[0-9a-f]{64}$'
    AND p_candidate_hash ~ '^[0-9a-f]{64}$'
    AND p_candidate_envelope_hash ~ '^[0-9a-f]{64}$'
    AND p_target_binding_hash ~ '^[0-9a-f]{64}$'
    AND p_model_binding_hash ~ '^[0-9a-f]{64}$'
    AND p_route_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(p_effective_scope) = 'array'
    AND memory_patch.step28_candidate_target_matches(
      p_tenant_id,
      p_owner_user_id,
      p_personal_memory_space_id,
      p_hat_scope_id,
      p_slot_state,
      p_slot_state_version,
      p_slot_configuration_version,
      p_slot_hash,
      p_configuration_digest,
      p_quota_policy_id,
      p_quota_policy_digest,
      p_model_binding_id,
      p_model_binding_hash,
      p_model_binding_version,
      p_binding_mode,
      p_provider_id,
      p_model_id,
      p_model_revision,
      p_model_binding_enabled
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.enforce_step29_proposal_quota()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).target_scope = 'USER_PERSONAL_HAT'
    AND (NEW).proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchProposalState'
  THEN
    IF octet_length((NEW).proposed_content::STRING) > 8388608 THEN
      RAISE EXCEPTION 'proposal slot quota exceeded'
        USING ERRCODE = '23514';
    END IF;

    UPDATE memory_patch.personal_memory_spaces AS space
       SET candidate_quota_epoch = space.candidate_quota_epoch + 1
     WHERE space.tenant_id = (NEW).tenant_id
       AND space.user_id = (NEW).owner_user_id
       AND space.personal_memory_space_id = (NEW).personal_memory_space_id
       AND space.hat_scope_id = (NEW).hat_scope_id
       AND space.state IN ('CONFIGURED', 'ACTIVE');
    -- CockroachDB's PL/pgSQL trigger subset does not expose PostgreSQL's
    -- implicit FOUND variable.  Re-check the exact authoritative row instead;
    -- the update above remains the per-slot serialization point.
    IF NOT EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS current_space
       WHERE current_space.tenant_id = (NEW).tenant_id
         AND current_space.user_id = (NEW).owner_user_id
         AND current_space.personal_memory_space_id
           = (NEW).personal_memory_space_id
         AND current_space.hat_scope_id = (NEW).hat_scope_id
         AND current_space.state IN ('CONFIGURED', 'ACTIVE')
    ) THEN
      RAISE EXCEPTION 'proposal target is not an eligible current slot'
        USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_proposals AS proposal
       WHERE proposal.tenant_id = (NEW).tenant_id
         AND proposal.owner_user_id = (NEW).owner_user_id
         AND proposal.personal_memory_space_id
           = (NEW).personal_memory_space_id
         AND proposal.proposal_id <> (NEW).proposal_id
         AND proposal.target_scope = 'USER_PERSONAL_HAT'
         AND proposal.proposed_content ->> 'contract_type'
           = 'PersonalMemoryPatchProposalState'
       HAVING count(*) >= 128
          OR coalesce(
            sum(octet_length(proposal.proposed_content::STRING)),
            0
          ) > 8388608 - octet_length((NEW).proposed_content::STRING)
    ) THEN
      RAISE EXCEPTION 'proposal slot quota exceeded'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step29_proposal_transition()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (OLD).proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchProposalState'
    OR (NEW).proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchProposalState'
  THEN
    IF (OLD).tenant_id IS DISTINCT FROM (NEW).tenant_id
      OR (OLD).proposal_id IS DISTINCT FROM (NEW).proposal_id
      OR (OLD).schema_version IS DISTINCT FROM (NEW).schema_version
      OR (OLD).hat_scope_id IS DISTINCT FROM (NEW).hat_scope_id
      OR (OLD).target_scope IS DISTINCT FROM (NEW).target_scope
      OR (OLD).target_hat_id IS DISTINCT FROM (NEW).target_hat_id
      OR (OLD).owner_user_id IS DISTINCT FROM (NEW).owner_user_id
      OR (OLD).personal_memory_space_id
        IS DISTINCT FROM (NEW).personal_memory_space_id
      OR (OLD).origin IS DISTINCT FROM (NEW).origin
      OR (OLD).scope_dimensions IS DISTINCT FROM (NEW).scope_dimensions
      OR (OLD).requested_trust_class
        IS DISTINCT FROM (NEW).requested_trust_class
      OR (OLD).approval_requirement
        IS DISTINCT FROM (NEW).approval_requirement
      OR (OLD).content_kind IS DISTINCT FROM (NEW).content_kind
      OR (OLD).created_at IS DISTINCT FROM (NEW).created_at
      OR (OLD).content_hash IS DISTINCT FROM (NEW).content_hash
      OR (OLD).step29_dedup_key IS DISTINCT FROM (NEW).step29_dedup_key
      OR (OLD).step29_candidate_id
        IS DISTINCT FROM (NEW).step29_candidate_id
      OR (OLD).step29_candidate_hash
        IS DISTINCT FROM (NEW).step29_candidate_hash
      OR (OLD).step29_candidate_envelope_hash
        IS DISTINCT FROM (NEW).step29_candidate_envelope_hash
      OR (OLD).step29_target_binding_hash
        IS DISTINCT FROM (NEW).step29_target_binding_hash
      OR (OLD).proposed_content -> 'proposal'
        IS DISTINCT FROM (NEW).proposed_content -> 'proposal'
    THEN
      RAISE EXCEPTION 'Step 29 proposal identity is immutable'
        USING ERRCODE = '23514';
    END IF;

    IF NOT (
      ((OLD).lifecycle_state = 'PROPOSED'
        AND (OLD).step29_state_version = 1
        AND (NEW).lifecycle_state = 'EVIDENCE_BOUND'
        AND (NEW).step29_state_version = 2)
      OR
      ((OLD).lifecycle_state = 'EVIDENCE_BOUND'
        AND (OLD).step29_state_version = 2
        AND (NEW).lifecycle_state = 'VALIDATED'
        AND (NEW).step29_state_version = 3)
      OR
      ((OLD).lifecycle_state = 'VALIDATED'
        AND (OLD).step29_state_version = 3
        AND (NEW).lifecycle_state = 'AWAITING_APPROVAL'
        AND (NEW).step29_state_version = 4)
    ) THEN
      RAISE EXCEPTION 'Step 29 transition skips or exceeds its lifecycle'
        USING ERRCODE = '23514';
    END IF;

    IF (NEW).proposed_content ->> 'state' <> (NEW).lifecycle_state
      OR ((NEW).proposed_content ->> 'state_version')::INT8
        <> (NEW).step29_state_version
      OR (NEW).proposed_content ->> 'state_hash'
        <> (NEW).step29_state_hash
      OR ((NEW).proposed_content -> 'proposal' ->> 'proposal_hash')
        <> (NEW).content_hash
      OR ((NEW).proposed_content -> 'proposal' ->> 'exact_dedup_key')
        <> (NEW).step29_dedup_key
      OR ((NEW).proposed_content -> 'proposal' ->> 'proposal_id')
        <> (NEW).proposal_id
      OR ((NEW).proposed_content -> 'proposal' ->> 'candidate_id')
        <> (NEW).step29_candidate_id
      OR ((NEW).proposed_content -> 'proposal' ->> 'candidate_hash')
        <> (NEW).step29_candidate_hash
      OR ((NEW).proposed_content -> 'proposal'
        ->> 'candidate_envelope_hash')
        <> (NEW).step29_candidate_envelope_hash
      OR ((NEW).proposed_content -> 'proposal' ->> 'target_binding_hash')
        <> (NEW).step29_target_binding_hash
      OR (
        (NEW).step29_state_version >= 2
        AND (NEW).step29_evidence_binding_hash
          <> (NEW).proposed_content -> 'evidence_binding' ->> 'binding_hash'
      )
      OR (
        (NEW).step29_state_version >= 3
        AND (
          (NEW).step29_validation_receipt_hash
            <> (NEW).proposed_content -> 'validation_receipt' ->> 'receipt_hash'
          OR NOT ((NEW).proposed_content -> 'validation_receipt'
            ->> 'validated')::BOOL
        )
      )
    THEN
      RAISE EXCEPTION 'Step 29 transition columns are detached from JSON'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.step29_transition_target_matches(
  p_tenant_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_state_after STRING
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
       AND proposal.content_hash = p_proposal_hash
       AND proposal.lifecycle_state = p_state_after
       AND proposal.proposed_content ->> 'contract_type'
         = 'PersonalMemoryPatchProposalState'
       AND memory_patch.user_context_matches(
         proposal.tenant_id,
         proposal.owner_user_id
       )
  )
$$;

ALTER FUNCTION memory_patch.step29_proposal_target_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
  INT8, INT8, STRING, STRING, STRING, STRING, STRING, STRING, INT8,
  STRING, STRING, STRING, STRING, BOOL, STRING, JSONB
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.enforce_step29_proposal_quota()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step29_proposal_transition()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step29_transition_target_matches(
  STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;

REVOKE ALL ON FUNCTION memory_patch.step29_proposal_target_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
  INT8, INT8, STRING, STRING, STRING, STRING, STRING, STRING, INT8,
  STRING, STRING, STRING, STRING, BOOL, STRING, JSONB
) FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.enforce_step29_proposal_quota()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.guard_step29_proposal_transition()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.step29_transition_target_matches(
  STRING, STRING, STRING, STRING
) FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
GRANT EXECUTE ON FUNCTION memory_patch.step29_proposal_target_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
  INT8, INT8, STRING, STRING, STRING, STRING, STRING, STRING, INT8,
  STRING, STRING, STRING, STRING, BOOL, STRING, JSONB
) TO mp_app_runtime;
GRANT EXECUTE ON FUNCTION memory_patch.step29_transition_target_matches(
  STRING, STRING, STRING, STRING
) TO mp_app_runtime;

DROP TRIGGER IF EXISTS memory_patch_proposals_s29_quota
  ON memory_patch.memory_patch_proposals;
CREATE TRIGGER memory_patch_proposals_s29_quota
  BEFORE INSERT OR UPDATE ON memory_patch.memory_patch_proposals
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.enforce_step29_proposal_quota();

DROP TRIGGER IF EXISTS memory_patch_proposals_s29_transition_guard
  ON memory_patch.memory_patch_proposals;
CREATE TRIGGER memory_patch_proposals_s29_transition_guard
  BEFORE UPDATE ON memory_patch.memory_patch_proposals
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step29_proposal_transition();

REVOKE ALL ON TABLE
  memory_patch.memory_patch_proposals,
  memory_patch.patch_transition_records
FROM PUBLIC, mp_request_context_setter;
GRANT UPDATE ON TABLE memory_patch.memory_patch_proposals
  TO mp_app_runtime;
GRANT INSERT ON TABLE memory_patch.patch_transition_records
  TO mp_app_runtime;
REVOKE DELETE ON TABLE
  memory_patch.memory_patch_proposals,
  memory_patch.patch_transition_records
FROM mp_app_runtime;

ALTER TABLE memory_patch.memory_patch_proposals
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_proposals
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.patch_transition_records
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.patch_transition_records
  FORCE ROW LEVEL SECURITY;

CREATE POLICY memory_patch_proposals_s29_insert
  ON memory_patch.memory_patch_proposals
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    target_scope = 'USER_PERSONAL_HAT'
    AND target_hat_id IS NULL
    AND valid_from IS NULL
    AND valid_until IS NULL
    AND origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
    AND lifecycle_state = 'PROPOSED'
    AND approval_requirement = 'OWNER'
    AND requested_trust_class = 'PERSONAL_VERIFIED_PATCH'
    AND content_kind = 'FACTUAL'
    AND jsonb_typeof(proposed_content) = 'object'
    AND proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchProposalState'
    AND proposed_content ->> 'schema_version' = '1.0.0'
    AND schema_version = proposed_content ->> 'schema_version'
    AND proposal_id ~ '^personal-memory-patch-proposal-[0-9a-f]{64}$'
    AND proposal_id = proposed_content -> 'proposal' ->> 'proposal_id'
    AND content_hash = proposed_content -> 'proposal' ->> 'proposal_hash'
    AND lifecycle_state = proposed_content ->> 'state'
    AND step29_state_version = 1
    AND step29_state_version
      = (proposed_content ->> 'state_version')::INT8
    AND step29_state_hash = proposed_content ->> 'state_hash'
    AND step29_dedup_key
      = proposed_content -> 'proposal' ->> 'exact_dedup_key'
    AND step29_candidate_id
      = proposed_content -> 'proposal' ->> 'candidate_id'
    AND step29_candidate_hash
      = proposed_content -> 'proposal' ->> 'candidate_hash'
    AND step29_candidate_envelope_hash
      = proposed_content -> 'proposal' ->> 'candidate_envelope_hash'
    AND step29_target_binding_hash
      = proposed_content -> 'proposal' ->> 'target_binding_hash'
    AND step29_evidence_binding_hash IS NULL
    AND step29_validation_receipt_hash IS NULL
    AND evidence_references = '[]'::JSONB
    AND scope_dimensions = proposed_content -> 'proposal' -> 'proposal_scope'
    AND tenant_id = proposed_content -> 'proposal' ->> 'tenant_id'
    AND owner_user_id = proposed_content -> 'proposal' ->> 'owner_user_id'
    AND personal_memory_space_id
      = proposed_content -> 'proposal' ->> 'personal_memory_space_id'
    AND hat_scope_id = proposed_content -> 'proposal' ->> 'hat_scope_id'
    AND origin = proposed_content -> 'proposal' ->> 'origin'
    AND memory_patch.step29_proposal_target_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      hat_scope_id,
      proposed_content -> 'proposal' ->> 'candidate_id',
      proposed_content -> 'proposal' ->> 'candidate_hash',
      proposed_content -> 'proposal' ->> 'candidate_envelope_hash',
      proposed_content -> 'proposal' ->> 'target_binding_hash',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_state',
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_state_version')::INT8,
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_configuration_version')::INT8,
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_hash',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'configuration_digest',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'quota_policy_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'quota_policy_digest',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_hash',
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_version')::INT8,
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'binding_mode',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'provider_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_revision_or_declared_version',
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_enabled')::BOOL,
      proposed_content -> 'proposal' ->> 'route_hash',
      proposed_content -> 'proposal' -> 'proposal_scope'
    )
  );

CREATE POLICY memory_patch_proposals_s29_update
  ON memory_patch.memory_patch_proposals
  FOR UPDATE
  TO mp_app_runtime
  USING (
    target_scope = 'USER_PERSONAL_HAT'
    AND proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchProposalState'
    AND lifecycle_state IN ('PROPOSED', 'EVIDENCE_BOUND', 'VALIDATED')
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  )
  WITH CHECK (
    target_scope = 'USER_PERSONAL_HAT'
    AND proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchProposalState'
    AND lifecycle_state IN (
      'EVIDENCE_BOUND',
      'VALIDATED',
      'AWAITING_APPROVAL'
    )
    AND memory_patch.step29_proposal_target_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      hat_scope_id,
      proposed_content -> 'proposal' ->> 'candidate_id',
      proposed_content -> 'proposal' ->> 'candidate_hash',
      proposed_content -> 'proposal' ->> 'candidate_envelope_hash',
      proposed_content -> 'proposal' ->> 'target_binding_hash',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_state',
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_state_version')::INT8,
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_configuration_version')::INT8,
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'slot_hash',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'configuration_digest',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'quota_policy_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'quota_policy_digest',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_hash',
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_version')::INT8,
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'binding_mode',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'provider_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_id',
      proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_revision_or_declared_version',
      (proposed_content -> 'proposal' -> 'target_slot_binding'
        ->> 'model_binding_enabled')::BOOL,
      proposed_content -> 'proposal' ->> 'route_hash',
      proposed_content -> 'proposal' -> 'proposal_scope'
    )
  );

CREATE POLICY patch_transition_records_s29_insert
  ON memory_patch.patch_transition_records
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    transition_id ~ '^step29-transition-[0-9a-f]{64}$'
    AND actor_type = 'SYSTEM'
    AND actor_id = 'personal-memory-patch-validation-service'
    AND (
      (state_before = 'DETECTED' AND state_after = 'PROPOSED')
      OR
      (state_before = 'PROPOSED' AND state_after = 'EVIDENCE_BOUND')
      OR
      (state_before = 'EVIDENCE_BOUND' AND state_after = 'VALIDATED')
      OR
      (state_before = 'VALIDATED' AND state_after = 'AWAITING_APPROVAL')
    )
    AND memory_patch.step29_transition_target_matches(
      tenant_id,
      proposal_id,
      proposal_content_hash,
      state_after
    )
  );
