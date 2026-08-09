-- Memory Patch Step 28 - owner-private Correction Candidate intake.
-- This migration reuses DETECTED proposal rows and grants no approval, commit,
-- activation, update, delete, retrieval, provider, or execution authority.

ALTER TABLE memory_patch.memory_patch_proposals
  DROP CONSTRAINT memory_patch_proposals_origin;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD CONSTRAINT memory_patch_proposals_origin
    CHECK (
      origin IN (
        'KNOWLEDGE_KERNEL',
        'KNOWLEDGE_HUB',
        'CRITIC_PROMPT_LOOP',
        'MODEL_VERIFIER',
        'USER_ENTRY',
        'USER_DOCUMENT',
        'HUMAN_REVIEW',
        'SYSTEM_MIGRATION'
      )
    );

-- Step 27 canonical values are materialized here so the database policy can
-- compare a candidate with one exact, current slot snapshot.  Pre-0012 rows
-- remain NULL and therefore fail closed for Step 28 intake until an ordinary
-- Step 27 repository update reconstructs and seals them.
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN hat_scope_id STRING;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN slot_hash STRING;
ALTER TABLE memory_patch.personal_memory_spaces
  ADD COLUMN candidate_quota_epoch INT8 NOT NULL DEFAULT 0;

-- The Step 27 Python contract hashes one four-key canonical JSON object.  Its
-- logical identifiers are ASCII and require no JSON escaping, so this
-- immutable SQL function reproduces those exact UTF-8 bytes at the database
-- boundary.  A non-canonical identifier returns NULL and cannot seal a slot.
CREATE OR REPLACE FUNCTION memory_patch.step28_personal_hat_scope_id(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING
)
RETURNS STRING
LANGUAGE SQL
IMMUTABLE
SECURITY INVOKER
AS $$
  SELECT CASE
    WHEN p_tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
      AND p_owner_user_id ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
      AND p_personal_memory_space_id
        ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
    THEN concat(
      'personal-hat-scope-',
      sha256(
        concat(
          '{"owner_user_id":"',
          p_owner_user_id,
          '","personal_memory_space_id":"',
          p_personal_memory_space_id,
          '","target_scope":"USER_PERSONAL_HAT","tenant_id":"',
          p_tenant_id,
          '"}'
        )::BYTES
      )
    )
    ELSE NULL
  END
$$;

ALTER TABLE memory_patch.personal_memory_spaces
  ADD CONSTRAINT personal_memory_spaces_s28_authority_tuple
    CHECK (
      (
        hat_scope_id IS NULL
        AND slot_hash IS NULL
      )
      OR
      (
        hat_scope_id IS NOT NULL
        AND slot_hash IS NOT NULL
        AND tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
        AND user_id ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
        AND personal_memory_space_id
          ~ '^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$'
        AND hat_scope_id = concat(
          'personal-hat-scope-',
          sha256(
            concat(
              '{"owner_user_id":"',
              user_id,
              '","personal_memory_space_id":"',
              personal_memory_space_id,
              '","target_scope":"USER_PERSONAL_HAT","tenant_id":"',
              tenant_id,
              '"}'
            )::BYTES
          )
        )
        AND slot_hash ~ '^[0-9a-f]{64}$'
      )
    );
ALTER TABLE memory_patch.personal_memory_spaces
  ADD CONSTRAINT personal_memory_spaces_s28_quota_epoch
    CHECK (candidate_quota_epoch >= 0);
ALTER TABLE memory_patch.personal_memory_spaces
  ADD CONSTRAINT personal_memory_spaces_s28_hat_scope_unique
    UNIQUE (tenant_id, hat_scope_id);

CREATE OR REPLACE FUNCTION memory_patch.step28_candidate_slot_is_eligible(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_hat_scope_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT
    memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS space
       WHERE space.tenant_id = p_tenant_id
         AND space.user_id = p_owner_user_id
         AND space.personal_memory_space_id = p_personal_memory_space_id
         AND space.hat_scope_id = p_hat_scope_id
         AND space.hat_scope_id = memory_patch.step28_personal_hat_scope_id(
           space.tenant_id,
           space.user_id,
           space.personal_memory_space_id
         )
         AND space.slot_hash ~ '^[0-9a-f]{64}$'
         AND space.state IN ('CONFIGURED', 'ACTIVE')
         AND space.quota_policy_id IS NOT NULL
         AND space.quota_policy_digest IS NOT NULL
         AND space.configuration_digest IS NOT NULL
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step28_candidate_target_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_hat_scope_id STRING,
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
  p_model_binding_enabled BOOL
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT
    p_hat_scope_id ~ '^personal-hat-scope-[0-9a-f]{64}$'
    AND p_slot_hash ~ '^[0-9a-f]{64}$'
    AND p_configuration_digest ~ '^[0-9a-f]{64}$'
    AND p_quota_policy_digest ~ '^[0-9a-f]{64}$'
    AND p_model_binding_hash ~ '^[0-9a-f]{64}$'
    AND p_binding_mode = 'EXACT_MODEL'
    AND p_model_binding_enabled IS TRUE
    AND memory_patch.step28_candidate_slot_is_eligible(
      p_tenant_id,
      p_owner_user_id,
      p_personal_memory_space_id,
      p_hat_scope_id
    )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS space
       WHERE space.tenant_id = p_tenant_id
         AND space.user_id = p_owner_user_id
         AND space.personal_memory_space_id = p_personal_memory_space_id
         AND space.hat_scope_id = p_hat_scope_id
         AND space.slot_hash = p_slot_hash
         AND space.state = p_slot_state
         AND space.state_version = p_slot_state_version
         AND space.configuration_version = p_slot_configuration_version
         AND space.configuration_digest = p_configuration_digest
         AND space.quota_policy_id = p_quota_policy_id
         AND space.quota_policy_digest = p_quota_policy_digest
    )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.hat_scopes AS scope
       WHERE scope.tenant_id = p_tenant_id
         AND scope.hat_scope_id = p_hat_scope_id
         AND scope.target_scope = 'USER_PERSONAL_HAT'
         AND scope.owner_user_id = p_owner_user_id
         AND scope.personal_memory_space_id = p_personal_memory_space_id
    )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_model_bindings AS binding
       WHERE binding.tenant_id = p_tenant_id
         AND binding.user_id = p_owner_user_id
         AND binding.personal_memory_space_id = p_personal_memory_space_id
         AND binding.model_binding_id = p_model_binding_id
         AND binding.binding_digest = p_model_binding_hash
         AND binding.binding_version = p_model_binding_version
         AND binding.binding_mode = p_binding_mode
         AND binding.provider_id = p_provider_id
         AND binding.model_id = p_model_id
         AND binding.model_revision = p_model_revision
         AND binding.enabled = p_model_binding_enabled
    )
$$;

-- The proposal rows remain the authoritative usage ledger.  This epoch is
-- only a same-slot serialization point: concurrent inserts contend on one
-- existing row, then the trigger measures the actual ledger plus NEW before
-- insertion.  An exact proposal replay bypasses accounting and reaches its
-- ON CONFLICT path.  Raising rolls back the epoch update and candidate write.
CREATE OR REPLACE FUNCTION memory_patch.enforce_step28_candidate_quota()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (NEW).target_scope = 'USER_PERSONAL_HAT'
    AND (NEW).target_hat_id IS NULL
    AND (NEW).origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
    AND (NEW).lifecycle_state = 'DETECTED'
    AND (NEW).approval_requirement = 'OWNER'
    AND (NEW).requested_trust_class = 'MODEL_EXPERIENCE_HINT'
    AND (NEW).content_kind = 'MODEL_EXPERIENCE'
    AND (NEW).proposed_content ->> 'contract_type'
      = 'CorrectionCandidateEnvelope'
  THEN
    IF octet_length((NEW).proposed_content::STRING) > 8388608 THEN
      RAISE EXCEPTION 'candidate slot quota exceeded'
        USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_proposals AS existing
       WHERE existing.tenant_id = (NEW).tenant_id
         AND existing.proposal_id = (NEW).proposal_id
         AND existing.owner_user_id = (NEW).owner_user_id
         AND existing.personal_memory_space_id
           = (NEW).personal_memory_space_id
         AND existing.target_scope = 'USER_PERSONAL_HAT'
         AND existing.proposed_content ->> 'contract_type'
           = 'CorrectionCandidateEnvelope'
    ) THEN
      RETURN NEW;
    END IF;

    UPDATE memory_patch.personal_memory_spaces AS space
       SET candidate_quota_epoch = space.candidate_quota_epoch + 1
     WHERE space.tenant_id = (NEW).tenant_id
       AND space.user_id = (NEW).owner_user_id
       AND space.personal_memory_space_id = (NEW).personal_memory_space_id
       AND space.hat_scope_id = (NEW).hat_scope_id
       AND space.slot_hash = (
         (NEW).proposed_content -> 'submission' -> 'target_slot_binding'
           ->> 'slot_hash'
       )
       AND space.state IN ('CONFIGURED', 'ACTIVE');
    IF NOT EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS current_space
       WHERE current_space.tenant_id = (NEW).tenant_id
         AND current_space.user_id = (NEW).owner_user_id
         AND current_space.personal_memory_space_id
           = (NEW).personal_memory_space_id
         AND current_space.hat_scope_id = (NEW).hat_scope_id
         AND current_space.slot_hash = (
           (NEW).proposed_content -> 'submission' -> 'target_slot_binding'
             ->> 'slot_hash'
         )
         AND current_space.state IN ('CONFIGURED', 'ACTIVE')
    ) THEN
      RAISE EXCEPTION 'candidate target is not the current authoritative slot'
        USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_proposals AS candidate
       WHERE candidate.tenant_id = (NEW).tenant_id
         AND candidate.owner_user_id = (NEW).owner_user_id
         AND candidate.personal_memory_space_id = (NEW).personal_memory_space_id
         AND candidate.target_scope = 'USER_PERSONAL_HAT'
         AND candidate.target_hat_id IS NULL
         AND candidate.origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
         AND candidate.approval_requirement = 'OWNER'
         AND candidate.requested_trust_class = 'MODEL_EXPERIENCE_HINT'
         AND candidate.content_kind = 'MODEL_EXPERIENCE'
         AND candidate.proposed_content ->> 'contract_type'
           = 'CorrectionCandidateEnvelope'
       HAVING count(*) >= 128
          OR coalesce(
            sum(octet_length(candidate.proposed_content::STRING)),
            0
          ) > 8388608 - octet_length((NEW).proposed_content::STRING)
    ) THEN
      RAISE EXCEPTION 'candidate slot quota exceeded'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step28_slot_authority_update()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (OLD).hat_scope_id IS NOT NULL
    AND (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
  THEN
    RAISE EXCEPTION 'personal memory HAT scope identity is immutable'
      USING ERRCODE = '23514';
  END IF;

  IF (OLD).slot_hash IS DISTINCT FROM (NEW).slot_hash
    AND (OLD).slot_hash IS NOT NULL
    AND NOT (
      (OLD).state IS DISTINCT FROM (NEW).state
      OR (OLD).display_name IS DISTINCT FROM (NEW).display_name
      OR (OLD).updated_at IS DISTINCT FROM (NEW).updated_at
      OR (OLD).export_requested_at IS DISTINCT FROM (NEW).export_requested_at
      OR (OLD).deletion_requested_at
        IS DISTINCT FROM (NEW).deletion_requested_at
      OR (OLD).deleted_at IS DISTINCT FROM (NEW).deleted_at
      OR (OLD).state_version IS DISTINCT FROM (NEW).state_version
      OR (OLD).configuration_version
        IS DISTINCT FROM (NEW).configuration_version
      OR (OLD).quota_policy_id IS DISTINCT FROM (NEW).quota_policy_id
      OR (OLD).quota_policy_digest IS DISTINCT FROM (NEW).quota_policy_digest
      OR (OLD).configuration_digest IS DISTINCT FROM (NEW).configuration_digest
    )
  THEN
    RAISE EXCEPTION 'slot hash changed without slot material'
      USING ERRCODE = '23514';
  END IF;

  IF (OLD).slot_hash IS NOT NULL
    AND (OLD).slot_hash IS NOT DISTINCT FROM (NEW).slot_hash
    AND (
      (OLD).state IS DISTINCT FROM (NEW).state
      OR (OLD).display_name IS DISTINCT FROM (NEW).display_name
      OR (OLD).updated_at IS DISTINCT FROM (NEW).updated_at
      OR (OLD).export_requested_at IS DISTINCT FROM (NEW).export_requested_at
      OR (OLD).deletion_requested_at
        IS DISTINCT FROM (NEW).deletion_requested_at
      OR (OLD).deleted_at IS DISTINCT FROM (NEW).deleted_at
      OR (OLD).state_version IS DISTINCT FROM (NEW).state_version
      OR (OLD).configuration_version
        IS DISTINCT FROM (NEW).configuration_version
      OR (OLD).quota_policy_id IS DISTINCT FROM (NEW).quota_policy_id
      OR (OLD).quota_policy_digest IS DISTINCT FROM (NEW).quota_policy_digest
      OR (OLD).configuration_digest IS DISTINCT FROM (NEW).configuration_digest
    )
  THEN
    RAISE EXCEPTION 'slot material changed without a new slot hash'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

ALTER FUNCTION memory_patch.step28_candidate_slot_is_eligible(
  STRING,
  STRING,
  STRING,
  STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step28_personal_hat_scope_id(
  STRING,
  STRING,
  STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step28_candidate_target_matches(
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  INT8,
  INT8,
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  INT8,
  STRING,
  STRING,
  STRING,
  STRING,
  BOOL
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.enforce_step28_candidate_quota()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step28_slot_authority_update()
  OWNER TO mp_schema_owner;

REVOKE ALL ON FUNCTION memory_patch.step28_candidate_slot_is_eligible(
  STRING,
  STRING,
  STRING,
  STRING
) FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.step28_personal_hat_scope_id(
  STRING,
  STRING,
  STRING
) FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.step28_candidate_target_matches(
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  INT8,
  INT8,
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  INT8,
  STRING,
  STRING,
  STRING,
  STRING,
  BOOL
) FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.enforce_step28_candidate_quota()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
REVOKE ALL ON FUNCTION memory_patch.guard_step28_slot_authority_update()
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
GRANT EXECUTE ON FUNCTION memory_patch.step28_candidate_slot_is_eligible(
  STRING,
  STRING,
  STRING,
  STRING
) TO mp_app_runtime;
GRANT EXECUTE ON FUNCTION memory_patch.step28_personal_hat_scope_id(
  STRING,
  STRING,
  STRING
) TO mp_app_runtime;
GRANT EXECUTE ON FUNCTION memory_patch.step28_candidate_target_matches(
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  INT8,
  INT8,
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  STRING,
  INT8,
  STRING,
  STRING,
  STRING,
  STRING,
  BOOL
) TO mp_app_runtime;

DROP TRIGGER IF EXISTS memory_patch_proposals_s28_candidate_quota
  ON memory_patch.memory_patch_proposals;
CREATE TRIGGER memory_patch_proposals_s28_candidate_quota
  BEFORE INSERT ON memory_patch.memory_patch_proposals
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.enforce_step28_candidate_quota();

DROP TRIGGER IF EXISTS personal_memory_spaces_s28_authority_guard
  ON memory_patch.personal_memory_spaces;
CREATE TRIGGER personal_memory_spaces_s28_authority_guard
  BEFORE UPDATE ON memory_patch.personal_memory_spaces
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step28_slot_authority_update();

REVOKE ALL ON TABLE
  memory_patch.hat_scopes,
  memory_patch.memory_patch_proposals
FROM PUBLIC, mp_request_context_setter;
REVOKE UPDATE, DELETE ON TABLE
  memory_patch.hat_scopes,
  memory_patch.memory_patch_proposals
FROM mp_app_runtime;
GRANT INSERT ON TABLE
  memory_patch.hat_scopes,
  memory_patch.memory_patch_proposals
TO mp_app_runtime;

ALTER TABLE memory_patch.hat_scopes
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.hat_scopes
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_proposals
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_patch_proposals
  FORCE ROW LEVEL SECURITY;

CREATE POLICY hat_scopes_s28_personal_insert
  ON memory_patch.hat_scopes
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    target_scope = 'USER_PERSONAL_HAT'
    AND hat_scope_id ~ '^personal-hat-scope-[0-9a-f]{64}$'
    AND memory_patch.step28_candidate_slot_is_eligible(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      hat_scope_id
    )
  );

CREATE POLICY memory_patch_proposals_s28_insert
  ON memory_patch.memory_patch_proposals
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    target_scope = 'USER_PERSONAL_HAT'
    AND target_hat_id IS NULL
    AND valid_from IS NULL
    AND valid_until IS NULL
    AND origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
    AND lifecycle_state = 'DETECTED'
    AND approval_requirement = 'OWNER'
    AND requested_trust_class = 'MODEL_EXPERIENCE_HINT'
    AND content_kind = 'MODEL_EXPERIENCE'
    AND jsonb_typeof(proposed_content) = 'object'
    AND proposed_content ->> 'contract_type' = 'CorrectionCandidateEnvelope'
    AND proposed_content ->> 'schema_version' = '1.0.0'
    AND schema_version = proposed_content ->> 'schema_version'
    AND proposal_id ~ '^correction-candidate-[0-9a-f]{64}$'
    AND proposal_id = proposed_content ->> 'candidate_id'
    AND proposed_content ->> 'envelope_id'
      ~ '^correction-candidate-envelope-[0-9a-f]{64}$'
    AND content_hash = proposed_content ->> 'envelope_hash'
    AND jsonb_typeof(proposed_content -> 'policy') = 'object'
    AND jsonb_typeof(proposed_content -> 'submission') = 'object'
    AND jsonb_typeof(
      proposed_content -> 'submission' -> 'target_slot_binding'
    ) = 'object'
    AND jsonb_typeof(
      proposed_content -> 'submission' -> 'candidate'
    ) = 'object'
    AND jsonb_typeof(
      proposed_content -> 'submission' -> 'lineage'
    ) = 'object'
    AND jsonb_typeof(
      proposed_content -> 'submission' -> 'candidate'
        -> 'available_evidence_references'
    ) = 'array'
    AND jsonb_typeof(
      proposed_content -> 'submission' -> 'lineage' -> 'effective_scope'
    ) = 'array'
    AND evidence_references = (
      proposed_content -> 'submission' -> 'candidate'
        -> 'available_evidence_references'
    )
    AND scope_dimensions = (
      proposed_content -> 'submission' -> 'lineage' -> 'effective_scope'
    )
    AND tenant_id = (
      proposed_content -> 'submission' -> 'candidate' ->> 'tenant_id'
    )
    AND owner_user_id = (
      proposed_content -> 'submission' -> 'candidate' ->> 'user_id'
    )
    AND personal_memory_space_id = (
      proposed_content -> 'submission' -> 'candidate'
        ->> 'personal_memory_space_id'
    )
    AND origin = (
      proposed_content -> 'submission' -> 'candidate' ->> 'source_component'
    )
    AND lifecycle_state = (
      proposed_content -> 'submission' -> 'candidate' ->> 'state'
    )
    AND (
      proposed_content -> 'submission' -> 'candidate' ->> 'model_binding_id'
    ) = (
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'model_binding_id'
    )
    AND tenant_id = (
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'tenant_id'
    )
    AND owner_user_id = (
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'owner_user_id'
    )
    AND personal_memory_space_id = (
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'personal_memory_space_id'
    )
    AND hat_scope_id = (
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'hat_scope_id'
    )
    AND memory_patch.step28_candidate_target_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      hat_scope_id,
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'slot_state',
      (
        proposed_content -> 'submission' -> 'target_slot_binding'
          ->> 'slot_state_version'
      )::INT8,
      (
        proposed_content -> 'submission' -> 'target_slot_binding'
          ->> 'slot_configuration_version'
      )::INT8,
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'slot_hash',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'configuration_digest',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'quota_policy_id',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'quota_policy_digest',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'model_binding_id',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'model_binding_hash',
      (
        proposed_content -> 'submission' -> 'target_slot_binding'
          ->> 'model_binding_version'
      )::INT8,
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'binding_mode',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'provider_id',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'model_id',
      proposed_content -> 'submission' -> 'target_slot_binding'
        ->> 'model_revision_or_declared_version',
      (
        proposed_content -> 'submission' -> 'target_slot_binding'
          ->> 'model_binding_enabled'
      )::BOOL
    )
  );
