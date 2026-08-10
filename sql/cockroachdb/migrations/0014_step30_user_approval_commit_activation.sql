-- Memory Patch Step 30 - exact owner approval, least-privileged technical
-- commit, and receipt-gated activation.  This migration creates no retrieval,
-- cross-model reuse, canonical-evidence, provider, or external-action path.

-- STEP30_CLUSTER_ROLE_DDL_BEGIN
CREATE ROLE IF NOT EXISTS mp_personal_memory_commit_helper;
ALTER ROLE mp_personal_memory_commit_helper
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
REVOKE admin FROM mp_personal_memory_commit_helper;
REVOKE mp_schema_owner FROM mp_personal_memory_commit_helper;
REVOKE mp_security_owner FROM mp_personal_memory_commit_helper;
REVOKE mp_app_runtime FROM mp_personal_memory_commit_helper;
REVOKE mp_request_context_setter FROM mp_personal_memory_commit_helper;
REVOKE mp_personal_memory_commit_helper FROM mp_app_runtime;
REVOKE mp_personal_memory_commit_helper FROM mp_request_context_setter;
-- STEP30_CLUSTER_ROLE_DDL_END

ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step30_approval_id STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step30_approval_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step30_commit_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step30_activation_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD COLUMN step30_patch_id STRING;

ALTER TABLE memory_patch.memory_patch_approvals
  ADD COLUMN step30_request_hash STRING;
ALTER TABLE memory_patch.memory_patch_approvals
  ADD COLUMN step30_evidence_binding_hash STRING;
ALTER TABLE memory_patch.memory_patch_approvals
  ADD COLUMN step30_validation_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_approvals
  ADD COLUMN step30_approval_replay_identity STRING;
ALTER TABLE memory_patch.memory_patch_approvals
  ADD COLUMN step30_approval_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_approvals
  ADD COLUMN step30_approval_payload JSONB;

ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_request_hash STRING;
ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_validation_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_approval_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_commit_replay_identity STRING;
ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_patch_hash STRING;
ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_commit_receipt_hash STRING;
ALTER TABLE memory_patch.memory_patch_commits
  ADD COLUMN step30_commit_payload JSONB;

ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_proposal_id STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_proposal_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_approval_receipt_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_validation_receipt_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_commit_receipt_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_patch_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_state_version INT8;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_state_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_activation_replay_identity STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_activation_receipt_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step30_activation_payload JSONB;

ALTER TABLE memory_patch.patch_transition_records
  ADD COLUMN step30_receipt_hash STRING;

CREATE UNIQUE INDEX memory_patch_approvals_s30_replay_uq
  ON memory_patch.memory_patch_approvals (
    tenant_id, owner_user_id, step30_approval_replay_identity
  )
  WHERE step30_approval_replay_identity IS NOT NULL;
CREATE UNIQUE INDEX memory_patch_commits_s30_replay_uq
  ON memory_patch.memory_patch_commits (
    tenant_id, owner_user_id, step30_commit_replay_identity
  )
  WHERE step30_commit_replay_identity IS NOT NULL;
CREATE UNIQUE INDEX memory_items_s30_patch_uq
  ON memory_patch.memory_items (tenant_id, source_patch_id)
  WHERE step30_patch_hash IS NOT NULL;
CREATE UNIQUE INDEX memory_items_s30_activation_replay_uq
  ON memory_patch.memory_items (
    tenant_id, step30_activation_replay_identity
  )
  WHERE step30_activation_replay_identity IS NOT NULL;

ALTER TABLE memory_patch.memory_patch_approvals
  ADD CONSTRAINT memory_patch_approvals_s30_payload
    CHECK (
      step30_approval_payload IS NULL
      OR (
        target_scope = 'USER_PERSONAL_HAT'
        AND decision = 'APPROVE'
        AND approver_type = 'USER'
        AND approver_id = owner_user_id
        AND reason_code = 'APPROVAL_GRANTED'
        AND step30_request_hash ~ '^[0-9a-f]{64}$'
        AND step30_evidence_binding_hash ~ '^[0-9a-f]{64}$'
        AND step30_validation_receipt_hash ~ '^[0-9a-f]{64}$'
        AND step30_approval_replay_identity
          ~ '^personal-memory-approval-replay-[0-9a-f]{64}$'
        AND step30_approval_receipt_hash ~ '^[0-9a-f]{64}$'
        AND approval_proof = step30_approval_receipt_hash
        AND jsonb_typeof(step30_approval_payload) = 'object'
        AND step30_approval_payload ->> 'schema_version' = '1.0.0'
        AND step30_approval_payload ->> 'approval_id' = approval_id
        AND step30_approval_payload ->> 'request_hash' = step30_request_hash
        AND step30_approval_payload ->> 'proposal_id' = proposal_id
        AND step30_approval_payload ->> 'proposal_hash'
          = proposal_content_hash
        AND step30_approval_payload ->> 'evidence_binding_hash'
          = step30_evidence_binding_hash
        AND step30_approval_payload ->> 'validation_receipt_hash'
          = step30_validation_receipt_hash
        AND step30_approval_payload ->> 'approval_replay_identity'
          = step30_approval_replay_identity
        AND step30_approval_payload ->> 'actor_type' = 'HUMAN_USER'
        AND step30_approval_payload ->> 'actor_id' = owner_user_id
        AND step30_approval_payload ->> 'receipt_hash'
          = step30_approval_receipt_hash
      )
    );

ALTER TABLE memory_patch.memory_patch_commits
  ADD CONSTRAINT memory_patch_commits_s30_payload
    CHECK (
      step30_commit_payload IS NULL
      OR (
        target_scope = 'USER_PERSONAL_HAT'
        AND approval_decision = 'APPROVE'
        AND actor_type = 'COMMIT_SERVICE'
        AND actor_id = 'personal-memory-commit-helper-1a'
        AND step30_request_hash ~ '^[0-9a-f]{64}$'
        AND step30_validation_receipt_hash ~ '^[0-9a-f]{64}$'
        AND step30_approval_receipt_hash ~ '^[0-9a-f]{64}$'
        AND approval_proof = step30_approval_receipt_hash
        AND step30_commit_replay_identity
          ~ '^personal-memory-commit-replay-[0-9a-f]{64}$'
        AND step30_patch_hash ~ '^[0-9a-f]{64}$'
        AND step30_commit_receipt_hash ~ '^[0-9a-f]{64}$'
        AND commit_hash = step30_commit_receipt_hash
        AND jsonb_typeof(step30_commit_payload) = 'object'
        AND step30_commit_payload -> 'committed_patch' ->> 'patch_id'
          = committed_patch_id
        AND step30_commit_payload -> 'committed_patch' ->> 'patch_hash'
          = step30_patch_hash
        AND step30_commit_payload -> 'committed_patch' ->> 'proposal_hash'
          = proposal_content_hash
        AND step30_commit_payload -> 'committed_patch'
          ->> 'approval_receipt_hash' = step30_approval_receipt_hash
        AND step30_commit_payload -> 'commit_receipt' ->> 'commit_id'
          = commit_id
        AND step30_commit_payload -> 'commit_receipt' ->> 'request_hash'
          = step30_request_hash
        AND step30_commit_payload -> 'commit_receipt'
          ->> 'commit_replay_identity' = step30_commit_replay_identity
        AND step30_commit_payload -> 'commit_receipt' ->> 'receipt_hash'
          = step30_commit_receipt_hash
        AND step30_commit_payload -> 'commit_receipt' ->> 'authority_role'
          = 'mp_personal_memory_commit_helper'
      )
    );

ALTER TABLE memory_patch.memory_items
  ADD CONSTRAINT memory_items_s30_payload
    CHECK (
      step30_patch_hash IS NULL
      OR (
        target_scope = 'USER_PERSONAL_HAT'
        AND visibility = 'PERSONAL'
        AND trust_class = 'PERSONAL_VERIFIED_PATCH'
        AND content_kind = 'FACTUAL'
        AND revoked = false
        AND source_patch_id = memory_item_id
        AND step30_proposal_id
          ~ '^personal-memory-patch-proposal-[0-9a-f]{64}$'
        AND step30_proposal_hash ~ '^[0-9a-f]{64}$'
        AND step30_approval_receipt_hash ~ '^[0-9a-f]{64}$'
        AND step30_validation_receipt_hash ~ '^[0-9a-f]{64}$'
        AND step30_commit_receipt_hash ~ '^[0-9a-f]{64}$'
        AND step30_patch_hash ~ '^[0-9a-f]{64}$'
        AND step30_state_version IN (6, 7)
        AND step30_state_hash ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(content) = 'object'
        AND content ->> 'patch_id' = source_patch_id
        AND content ->> 'patch_hash' = step30_patch_hash
        AND content ->> 'proposal_id' = step30_proposal_id
        AND content ->> 'proposal_hash' = step30_proposal_hash
        AND content ->> 'approval_receipt_hash'
          = step30_approval_receipt_hash
        AND content ->> 'validation_receipt_hash'
          = step30_validation_receipt_hash
        AND (
          (
            step30_state_version = 6
            AND active = false
            AND step30_activation_replay_identity IS NULL
            AND step30_activation_receipt_hash IS NULL
            AND step30_activation_payload IS NULL
          )
          OR
          (
            step30_state_version = 7
            AND active = true
            AND step30_activation_replay_identity
              ~ '^personal-memory-activation-replay-[0-9a-f]{64}$'
            AND step30_activation_receipt_hash ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(step30_activation_payload) = 'object'
            AND step30_activation_payload ->> 'patch_id' = source_patch_id
            AND step30_activation_payload ->> 'patch_hash'
              = step30_patch_hash
            AND step30_activation_payload ->> 'proposal_hash'
              = step30_proposal_hash
            AND step30_activation_payload ->> 'commit_receipt_hash'
              = step30_commit_receipt_hash
            AND step30_activation_payload ->> 'approval_receipt_hash'
              = step30_approval_receipt_hash
            AND step30_activation_payload ->> 'activation_replay_identity'
              = step30_activation_replay_identity
            AND step30_activation_payload ->> 'receipt_hash'
              = step30_activation_receipt_hash
          )
        )
      )
    );

ALTER TABLE memory_patch.patch_transition_records
  ADD CONSTRAINT patch_transition_records_s30_receipt
    CHECK (
      step30_receipt_hash IS NULL
      OR (
        transition_id ~ '^step30-transition-[0-9a-f]{64}$'
        AND step30_receipt_hash ~ '^[0-9a-f]{64}$'
        AND (
          (state_before = 'AWAITING_APPROVAL' AND state_after = 'APPROVED')
          OR (state_before = 'APPROVED' AND state_after = 'COMMITTED')
          OR (state_before = 'COMMITTED' AND state_after = 'ACTIVE')
        )
      )
    );

ALTER TABLE memory_patch.memory_items
  DROP CONSTRAINT memory_items_verified_inert;
ALTER TABLE memory_patch.memory_items
  ADD CONSTRAINT memory_items_verified_inert
    CHECK (
      active = false
      OR trust_class NOT IN (
        'SHARED_HAT_VERIFIED_MEMORY',
        'PERSONAL_VERIFIED_PATCH'
      )
      OR (
        trust_class = 'PERSONAL_VERIFIED_PATCH'
        AND step30_state_version = 7
        AND step30_activation_receipt_hash ~ '^[0-9a-f]{64}$'
      )
    );

ALTER TABLE memory_patch.memory_patch_proposals
  DROP CONSTRAINT memory_patch_proposals_s29_state_tuple;
ALTER TABLE memory_patch.memory_patch_proposals
  ADD CONSTRAINT memory_patch_proposals_s30_state_tuple
    CHECK (
      (
        step29_dedup_key IS NULL
        AND step29_state_version IS NULL
        AND step29_state_hash IS NULL
        AND step30_approval_id IS NULL
        AND step30_approval_receipt_hash IS NULL
        AND step30_commit_receipt_hash IS NULL
        AND step30_activation_receipt_hash IS NULL
        AND step30_patch_id IS NULL
      )
      OR
      (
        step29_dedup_key ~ '^personal-patch-dedup-[0-9a-f]{64}$'
        AND step29_candidate_id ~ '^correction-candidate-[0-9a-f]{64}$'
        AND step29_candidate_hash ~ '^[0-9a-f]{64}$'
        AND step29_candidate_envelope_hash ~ '^[0-9a-f]{64}$'
        AND step29_target_binding_hash ~ '^[0-9a-f]{64}$'
        AND step29_state_version IN (1, 2, 3, 4, 5, 6, 7)
        AND step29_state_hash ~ '^[0-9a-f]{64}$'
        AND lifecycle_state = proposed_content ->> 'state'
        AND step29_state_version
          = (proposed_content ->> 'state_version')::INT8
        AND step29_state_hash = proposed_content ->> 'state_hash'
        AND (
          (
            step29_state_version IN (1, 2, 3, 4)
            AND proposed_content ->> 'contract_type'
              = 'PersonalMemoryPatchProposalState'
            AND step30_approval_id IS NULL
            AND step30_approval_receipt_hash IS NULL
            AND step30_commit_receipt_hash IS NULL
            AND step30_activation_receipt_hash IS NULL
            AND step30_patch_id IS NULL
            AND (
              (step29_state_version = 1 AND lifecycle_state = 'PROPOSED'
                AND step29_evidence_binding_hash IS NULL
                AND step29_validation_receipt_hash IS NULL)
              OR
              (step29_state_version = 2 AND lifecycle_state = 'EVIDENCE_BOUND'
                AND step29_evidence_binding_hash
                  = proposed_content -> 'evidence_binding' ->> 'binding_hash'
                AND step29_validation_receipt_hash IS NULL)
              OR
              (step29_state_version = 3 AND lifecycle_state = 'VALIDATED'
                AND step29_evidence_binding_hash
                  = proposed_content -> 'evidence_binding' ->> 'binding_hash'
                AND step29_validation_receipt_hash
                  = proposed_content -> 'validation_receipt' ->> 'receipt_hash')
              OR
              (step29_state_version = 4
                AND lifecycle_state = 'AWAITING_APPROVAL'
                AND step29_evidence_binding_hash
                  = proposed_content -> 'evidence_binding' ->> 'binding_hash'
                AND step29_validation_receipt_hash
                  = proposed_content -> 'validation_receipt' ->> 'receipt_hash')
            )
          )
          OR
          (
            step29_state_version IN (5, 6, 7)
            AND proposed_content ->> 'contract_type'
              = 'PersonalMemoryPatchLifecycleState'
            AND proposed_content -> 'step29_state' ->> 'state'
              = 'AWAITING_APPROVAL'
            AND (proposed_content -> 'step29_state' ->> 'state_version')::INT8 = 4
            AND content_hash = proposed_content -> 'step29_state'
              -> 'proposal' ->> 'proposal_hash'
            AND step29_dedup_key = proposed_content -> 'step29_state'
              -> 'proposal' ->> 'exact_dedup_key'
            AND step29_candidate_id = proposed_content -> 'step29_state'
              -> 'proposal' ->> 'candidate_id'
            AND step29_candidate_hash = proposed_content -> 'step29_state'
              -> 'proposal' ->> 'candidate_hash'
            AND step29_candidate_envelope_hash = proposed_content
              -> 'step29_state' -> 'proposal' ->> 'candidate_envelope_hash'
            AND step29_target_binding_hash = proposed_content
              -> 'step29_state' -> 'proposal' ->> 'target_binding_hash'
            AND step29_evidence_binding_hash = proposed_content
              -> 'step29_state' -> 'evidence_binding' ->> 'binding_hash'
            AND step29_validation_receipt_hash = proposed_content
              -> 'step29_state' -> 'validation_receipt' ->> 'receipt_hash'
            AND step30_approval_id
              = proposed_content -> 'approval_receipt' ->> 'approval_id'
            AND step30_approval_receipt_hash
              = proposed_content -> 'approval_receipt' ->> 'receipt_hash'
            AND (
              (step29_state_version = 5 AND lifecycle_state = 'APPROVED'
                AND step30_commit_receipt_hash IS NULL
                AND step30_activation_receipt_hash IS NULL
                AND step30_patch_id IS NULL)
              OR
              (step29_state_version = 6 AND lifecycle_state = 'COMMITTED'
                AND step30_commit_receipt_hash
                  = proposed_content -> 'commit_receipt' ->> 'receipt_hash'
                AND step30_patch_id
                  = proposed_content -> 'committed_patch' ->> 'patch_id'
                AND step30_activation_receipt_hash IS NULL)
              OR
              (step29_state_version = 7 AND lifecycle_state = 'ACTIVE'
                AND step30_commit_receipt_hash
                  = proposed_content -> 'commit_receipt' ->> 'receipt_hash'
                AND step30_patch_id
                  = proposed_content -> 'committed_patch' ->> 'patch_id'
                AND step30_activation_receipt_hash
                  = proposed_content -> 'activation_receipt' ->> 'receipt_hash')
            )
          )
        )
      )
    );

CREATE OR REPLACE FUNCTION memory_patch.step30_commit_helper_authorized()
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
    session_user,
    'mp_personal_memory_commit_helper',
    'MEMBER'
  )
$$;

-- CockroachDB v26.2 does not implement column-level UPDATE grants.  The
-- dedicated role therefore receives table UPDATE solely so this trigger can
-- provide the equivalent fail-closed capability: exactly one increment of
-- the existing non-semantic serialization epoch and no slot mutation.
CREATE OR REPLACE FUNCTION memory_patch.guard_step30_commit_slot_update()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF pg_catalog.pg_has_role(
    session_user,
    'mp_personal_memory_commit_helper',
    'MEMBER'
  ) THEN
    IF (NEW).candidate_quota_epoch <> (OLD).candidate_quota_epoch + 1
      OR (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
      OR (NEW).user_id IS DISTINCT FROM (OLD).user_id
      OR (NEW).personal_memory_space_id
        IS DISTINCT FROM (OLD).personal_memory_space_id
      OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
      OR (NEW).state IS DISTINCT FROM (OLD).state
      OR (NEW).display_name IS DISTINCT FROM (OLD).display_name
      OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
      OR (NEW).updated_at IS DISTINCT FROM (OLD).updated_at
      OR (NEW).export_requested_at IS DISTINCT FROM (OLD).export_requested_at
      OR (NEW).deletion_requested_at
        IS DISTINCT FROM (OLD).deletion_requested_at
      OR (NEW).deleted_at IS DISTINCT FROM (OLD).deleted_at
      OR (NEW).state_version IS DISTINCT FROM (OLD).state_version
      OR (NEW).configuration_version
        IS DISTINCT FROM (OLD).configuration_version
      OR (NEW).quota_policy_id IS DISTINCT FROM (OLD).quota_policy_id
      OR (NEW).quota_policy_digest IS DISTINCT FROM (OLD).quota_policy_digest
      OR (NEW).configuration_digest
        IS DISTINCT FROM (OLD).configuration_digest
      OR (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
      OR (NEW).slot_hash IS DISTINCT FROM (OLD).slot_hash
    THEN
      RAISE EXCEPTION 'Commit Helper may only increment the slot quota epoch'
        USING ERRCODE = '42501';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.step30_approval_target_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_evidence_binding_hash STRING,
  p_validation_receipt_hash STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_proposals AS proposal
       WHERE proposal.tenant_id = p_tenant_id
         AND proposal.owner_user_id = p_owner_user_id
         AND proposal.personal_memory_space_id = p_personal_memory_space_id
         AND proposal.proposal_id = p_proposal_id
         AND proposal.content_hash = p_proposal_hash
         AND proposal.lifecycle_state = 'AWAITING_APPROVAL'
         AND proposal.step29_state_version = 4
         AND proposal.step29_evidence_binding_hash = p_evidence_binding_hash
         AND proposal.step29_validation_receipt_hash = p_validation_receipt_hash
         AND proposal.proposed_content ->> 'contract_type'
           = 'PersonalMemoryPatchProposalState'
         AND (proposal.proposed_content -> 'validation_receipt'
           ->> 'validated')::BOOL
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step30_approval_transition_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_approval_id STRING,
  p_approval_receipt_hash STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_approvals AS approval_record
       WHERE approval_record.tenant_id = p_tenant_id
         AND approval_record.owner_user_id = p_owner_user_id
         AND approval_record.personal_memory_space_id
           = p_personal_memory_space_id
         AND approval_record.proposal_id = p_proposal_id
         AND approval_record.proposal_content_hash = p_proposal_hash
         AND approval_record.approval_id = p_approval_id
         AND approval_record.step30_approval_receipt_hash
           = p_approval_receipt_hash
         AND approval_record.decision = 'APPROVE'
         AND approval_record.approver_type = 'USER'
         AND approval_record.approver_id = p_owner_user_id
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step30_commit_target_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_approval_id STRING,
  p_approval_receipt_hash STRING,
  p_validation_receipt_hash STRING,
  p_patch_hash STRING,
  p_commit_receipt_hash STRING,
  p_commit_payload JSONB
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.step30_commit_helper_authorized()
    AND memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_proposals AS proposal
        JOIN memory_patch.memory_patch_approvals AS approval
          ON approval.tenant_id = proposal.tenant_id
         AND approval.proposal_id = proposal.proposal_id
         AND approval.owner_user_id = proposal.owner_user_id
         AND approval.personal_memory_space_id
           = proposal.personal_memory_space_id
       WHERE proposal.tenant_id = p_tenant_id
         AND proposal.owner_user_id = p_owner_user_id
         AND proposal.personal_memory_space_id = p_personal_memory_space_id
         AND proposal.proposal_id = p_proposal_id
         AND proposal.content_hash = p_proposal_hash
         AND proposal.lifecycle_state = 'APPROVED'
         AND proposal.step29_state_version = 5
         AND proposal.step30_approval_id = p_approval_id
         AND proposal.step30_approval_receipt_hash = p_approval_receipt_hash
         AND proposal.step29_validation_receipt_hash
           = p_validation_receipt_hash
         AND approval.approval_id = p_approval_id
         AND approval.step30_approval_receipt_hash
           = p_approval_receipt_hash
         AND approval.decision = 'APPROVE'
         AND approval.approver_type = 'USER'
         AND approval.approver_id = p_owner_user_id
         AND p_commit_payload -> 'committed_patch' ->> 'proposal_id'
           = p_proposal_id
         AND p_commit_payload -> 'committed_patch' ->> 'proposal_hash'
           = p_proposal_hash
         AND p_commit_payload -> 'committed_patch'
           ->> 'approval_receipt_hash' = p_approval_receipt_hash
         AND p_commit_payload -> 'committed_patch'
           ->> 'validation_receipt_hash' = p_validation_receipt_hash
         AND p_commit_payload -> 'committed_patch' ->> 'patch_hash'
           = p_patch_hash
         AND p_commit_payload -> 'committed_patch' ->> 'patch_statement'
           = proposal.proposed_content -> 'step29_state' -> 'proposal'
             ->> 'proposal_statement'
         AND p_commit_payload -> 'committed_patch'
           ->> 'patch_statement_sha256'
           = proposal.proposed_content -> 'step29_state' -> 'proposal'
             ->> 'proposal_statement_sha256'
         AND p_commit_payload -> 'committed_patch' -> 'patch_scope'
           = proposal.proposed_content -> 'step29_state' -> 'proposal'
             -> 'proposal_scope'
         AND p_commit_payload -> 'commit_receipt' ->> 'proposal_id'
           = p_proposal_id
         AND p_commit_payload -> 'commit_receipt' ->> 'proposal_hash'
           = p_proposal_hash
         AND p_commit_payload -> 'commit_receipt'
           ->> 'approval_receipt_hash' = p_approval_receipt_hash
         AND p_commit_payload -> 'commit_receipt'
           ->> 'validation_receipt_hash' = p_validation_receipt_hash
         AND p_commit_payload -> 'commit_receipt' ->> 'patch_hash'
           = p_patch_hash
         AND p_commit_payload -> 'commit_receipt' ->> 'receipt_hash'
           = p_commit_receipt_hash
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step30_memory_item_commit_matches(
  p_tenant_id STRING,
  p_hat_scope_id STRING,
  p_patch_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_approval_receipt_hash STRING,
  p_validation_receipt_hash STRING,
  p_commit_receipt_hash STRING,
  p_patch_hash STRING,
  p_content JSONB
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.step30_commit_helper_authorized()
    AND memory_patch.hat_scope_context_matches(
      p_tenant_id,
      p_hat_scope_id
    )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.memory_patch_commits AS commit_record
       WHERE commit_record.tenant_id = p_tenant_id
         AND commit_record.committed_patch_id = p_patch_id
         AND commit_record.proposal_id = p_proposal_id
         AND commit_record.proposal_content_hash = p_proposal_hash
         AND commit_record.step30_approval_receipt_hash
           = p_approval_receipt_hash
         AND commit_record.step30_validation_receipt_hash
           = p_validation_receipt_hash
         AND commit_record.step30_commit_receipt_hash
           = p_commit_receipt_hash
         AND commit_record.step30_patch_hash = p_patch_hash
         AND commit_record.step30_commit_payload -> 'committed_patch'
           = p_content
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step30_proposal_lifecycle_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_state_after STRING,
  p_approval_id STRING,
  p_approval_receipt_hash STRING,
  p_validation_receipt_hash STRING,
  p_commit_receipt_hash STRING,
  p_activation_receipt_hash STRING,
  p_patch_id STRING,
  p_lifecycle_payload JSONB
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.step30_commit_helper_authorized()
    AND memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND CASE p_state_after
      WHEN 'COMMITTED' THEN EXISTS (
        SELECT 1
          FROM memory_patch.memory_patch_commits AS commit_record
          JOIN memory_patch.memory_items AS patch_record
            ON patch_record.tenant_id = commit_record.tenant_id
           AND patch_record.memory_item_id
             = commit_record.committed_patch_id
         WHERE commit_record.tenant_id = p_tenant_id
           AND commit_record.owner_user_id = p_owner_user_id
           AND commit_record.personal_memory_space_id
             = p_personal_memory_space_id
           AND commit_record.proposal_id = p_proposal_id
           AND commit_record.proposal_content_hash = p_proposal_hash
           AND commit_record.approval_id = p_approval_id
           AND commit_record.step30_approval_receipt_hash
             = p_approval_receipt_hash
           AND commit_record.step30_validation_receipt_hash
             = p_validation_receipt_hash
           AND commit_record.step30_commit_receipt_hash
             = p_commit_receipt_hash
           AND commit_record.committed_patch_id = p_patch_id
           AND commit_record.step30_commit_payload -> 'committed_patch'
             = p_lifecycle_payload -> 'committed_patch'
           AND commit_record.step30_commit_payload -> 'commit_receipt'
             = p_lifecycle_payload -> 'commit_receipt'
           AND patch_record.step30_state_version = 6
           AND NOT patch_record.active
           AND patch_record.step30_proposal_id = p_proposal_id
           AND patch_record.step30_proposal_hash = p_proposal_hash
           AND patch_record.step30_approval_receipt_hash
             = p_approval_receipt_hash
           AND patch_record.step30_validation_receipt_hash
             = p_validation_receipt_hash
           AND patch_record.step30_commit_receipt_hash
             = p_commit_receipt_hash
           AND patch_record.content
             = p_lifecycle_payload -> 'committed_patch'
      )
      WHEN 'ACTIVE' THEN EXISTS (
        SELECT 1
          FROM memory_patch.memory_items AS patch_record
         WHERE patch_record.tenant_id = p_tenant_id
           AND patch_record.memory_item_id = p_patch_id
           AND patch_record.step30_state_version = 7
           AND patch_record.active
           AND NOT patch_record.revoked
           AND patch_record.step30_proposal_id = p_proposal_id
           AND patch_record.step30_proposal_hash = p_proposal_hash
           AND patch_record.step30_approval_receipt_hash
             = p_approval_receipt_hash
           AND patch_record.step30_validation_receipt_hash
             = p_validation_receipt_hash
           AND patch_record.step30_commit_receipt_hash
             = p_commit_receipt_hash
           AND patch_record.step30_activation_receipt_hash
             = p_activation_receipt_hash
           AND patch_record.content
             = p_lifecycle_payload -> 'committed_patch'
           AND patch_record.step30_activation_payload
             = p_lifecycle_payload -> 'activation_receipt'
      )
      ELSE false
    END
$$;

CREATE OR REPLACE FUNCTION memory_patch.step30_transition_target_matches(
  p_tenant_id STRING,
  p_proposal_id STRING,
  p_proposal_hash STRING,
  p_state_after STRING,
  p_receipt_hash STRING
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
         = 'PersonalMemoryPatchLifecycleState'
       AND memory_patch.user_context_matches(
         proposal.tenant_id,
         proposal.owner_user_id
       )
       AND CASE p_state_after
         WHEN 'APPROVED' THEN proposal.step30_approval_receipt_hash
         WHEN 'COMMITTED' THEN proposal.step30_commit_receipt_hash
         WHEN 'ACTIVE' THEN proposal.step30_activation_receipt_hash
         ELSE NULL
       END = p_receipt_hash
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step30_proposal_transition()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (OLD).proposed_content ->> 'contract_type' IN (
      'PersonalMemoryPatchProposalState', 'PersonalMemoryPatchLifecycleState'
    )
    OR (NEW).proposed_content ->> 'contract_type' IN (
      'PersonalMemoryPatchProposalState', 'PersonalMemoryPatchLifecycleState'
    )
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
      OR (OLD).step29_candidate_id IS DISTINCT FROM (NEW).step29_candidate_id
      OR (OLD).step29_candidate_hash
        IS DISTINCT FROM (NEW).step29_candidate_hash
      OR (OLD).step29_candidate_envelope_hash
        IS DISTINCT FROM (NEW).step29_candidate_envelope_hash
      OR (OLD).step29_target_binding_hash
        IS DISTINCT FROM (NEW).step29_target_binding_hash
      OR (
        (OLD).step29_state_version >= 2
        AND (OLD).step29_evidence_binding_hash
          IS DISTINCT FROM (NEW).step29_evidence_binding_hash
      )
      OR (
        (OLD).step29_state_version >= 3
        AND (OLD).step29_validation_receipt_hash
          IS DISTINCT FROM (NEW).step29_validation_receipt_hash
      )
    THEN
      RAISE EXCEPTION 'Step 30 proposal identity is immutable'
        USING ERRCODE = '23514';
    END IF;

    IF (
      (OLD).lifecycle_state = 'AWAITING_APPROVAL'
      AND (
        (NEW).step30_approval_id IS NULL
        OR (NEW).step30_approval_receipt_hash IS NULL
        OR (NEW).step30_commit_receipt_hash IS NOT NULL
        OR (NEW).step30_activation_receipt_hash IS NOT NULL
        OR (NEW).step30_patch_id IS NOT NULL
      )
    ) OR (
      (OLD).lifecycle_state = 'APPROVED'
      AND (
        (OLD).step30_approval_id
          IS DISTINCT FROM (NEW).step30_approval_id
        OR (OLD).step30_approval_receipt_hash
          IS DISTINCT FROM (NEW).step30_approval_receipt_hash
        OR (NEW).step30_commit_receipt_hash IS NULL
        OR (NEW).step30_activation_receipt_hash IS NOT NULL
        OR (NEW).step30_patch_id IS NULL
      )
    ) OR (
      (OLD).lifecycle_state = 'COMMITTED'
      AND (
        (OLD).step30_approval_id
          IS DISTINCT FROM (NEW).step30_approval_id
        OR (OLD).step30_approval_receipt_hash
          IS DISTINCT FROM (NEW).step30_approval_receipt_hash
        OR (OLD).step30_commit_receipt_hash
          IS DISTINCT FROM (NEW).step30_commit_receipt_hash
        OR (OLD).step30_patch_id IS DISTINCT FROM (NEW).step30_patch_id
        OR (NEW).step30_activation_receipt_hash IS NULL
      )
    ) THEN
      RAISE EXCEPTION 'Step 30 receipt lineage changed across a state edge'
        USING ERRCODE = '23514';
    END IF;

    IF NOT (
      ((OLD).lifecycle_state = 'PROPOSED'
        AND (OLD).step29_state_version = 1
        AND (NEW).lifecycle_state = 'EVIDENCE_BOUND'
        AND (NEW).step29_state_version = 2)
      OR ((OLD).lifecycle_state = 'EVIDENCE_BOUND'
        AND (OLD).step29_state_version = 2
        AND (NEW).lifecycle_state = 'VALIDATED'
        AND (NEW).step29_state_version = 3)
      OR ((OLD).lifecycle_state = 'VALIDATED'
        AND (OLD).step29_state_version = 3
        AND (NEW).lifecycle_state = 'AWAITING_APPROVAL'
        AND (NEW).step29_state_version = 4)
      OR ((OLD).lifecycle_state = 'AWAITING_APPROVAL'
        AND (OLD).step29_state_version = 4
        AND (NEW).lifecycle_state = 'APPROVED'
        AND (NEW).step29_state_version = 5)
      OR ((OLD).lifecycle_state = 'APPROVED'
        AND (OLD).step29_state_version = 5
        AND (NEW).lifecycle_state = 'COMMITTED'
        AND (NEW).step29_state_version = 6)
      OR ((OLD).lifecycle_state = 'COMMITTED'
        AND (OLD).step29_state_version = 6
        AND (NEW).lifecycle_state = 'ACTIVE'
        AND (NEW).step29_state_version = 7)
    ) THEN
      RAISE EXCEPTION 'Step 30 lifecycle transition skips a receipt gate'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step30_memory_item()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
DECLARE
  maximum_bytes INT8;
  maximum_active INT8;
  current_bytes INT8;
  current_active INT8;
BEGIN
  IF TG_OP = 'INSERT' AND (NEW).step30_patch_hash IS NOT NULL THEN
    IF NOT memory_patch.step30_commit_helper_authorized() THEN
      RAISE EXCEPTION 'dedicated commit helper authority required'
        USING ERRCODE = '42501';
    END IF;
    IF (NEW).active OR (NEW).step30_state_version <> 6 THEN
      RAISE EXCEPTION 'technical commit must persist an inactive patch'
        USING ERRCODE = '23514';
    END IF;
    UPDATE memory_patch.personal_memory_spaces AS space
       SET candidate_quota_epoch = space.candidate_quota_epoch + 1
     WHERE space.tenant_id = (NEW).tenant_id
       AND space.hat_scope_id = (NEW).hat_scope_id
       AND space.state IN ('CONFIGURED', 'ACTIVE');
    IF NOT EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS current_space
       WHERE current_space.tenant_id = (NEW).tenant_id
         AND current_space.hat_scope_id = (NEW).hat_scope_id
         AND current_space.state IN ('CONFIGURED', 'ACTIVE')
    ) THEN
      RAISE EXCEPTION 'Personal Memory target slot is not commit-eligible'
        USING ERRCODE = '23514';
    END IF;
    SELECT policy.maximum_bytes
      INTO maximum_bytes
      FROM memory_patch.personal_memory_spaces AS space
      JOIN memory_patch.personal_memory_quota_policies AS policy
        ON policy.tenant_id = space.tenant_id
       AND policy.owner_user_id = space.user_id
       AND policy.quota_policy_id = space.quota_policy_id
       AND policy.policy_digest = space.quota_policy_digest
     WHERE space.tenant_id = (NEW).tenant_id
       AND space.hat_scope_id = (NEW).hat_scope_id
       AND space.state IN ('CONFIGURED', 'ACTIVE');
    IF maximum_bytes IS NULL THEN
      RAISE EXCEPTION 'Personal Memory quota policy is unavailable'
        USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(sum(octet_length(item.content::STRING)), 0)
      INTO current_bytes
      FROM memory_patch.memory_items AS item
     WHERE item.tenant_id = (NEW).tenant_id
       AND item.hat_scope_id = (NEW).hat_scope_id;
    IF current_bytes > maximum_bytes - octet_length((NEW).content::STRING) THEN
      RAISE EXCEPTION 'Personal Memory byte quota exceeded'
        USING ERRCODE = '23514';
    END IF;
  ELSIF TG_OP = 'UPDATE' AND (OLD).step30_patch_hash IS NOT NULL THEN
    IF NOT memory_patch.step30_commit_helper_authorized() THEN
      RAISE EXCEPTION 'dedicated activation authority required'
        USING ERRCODE = '42501';
    END IF;
    IF (OLD).active OR NOT (NEW).active
      OR (OLD).step30_state_version <> 6
      OR (NEW).step30_state_version <> 7
      OR (OLD).tenant_id IS DISTINCT FROM (NEW).tenant_id
      OR (OLD).memory_item_id IS DISTINCT FROM (NEW).memory_item_id
      OR (OLD).schema_version IS DISTINCT FROM (NEW).schema_version
      OR (OLD).hat_scope_id IS DISTINCT FROM (NEW).hat_scope_id
      OR (OLD).target_scope IS DISTINCT FROM (NEW).target_scope
      OR (OLD).visibility IS DISTINCT FROM (NEW).visibility
      OR (OLD).trust_class IS DISTINCT FROM (NEW).trust_class
      OR (OLD).content_kind IS DISTINCT FROM (NEW).content_kind
      OR (OLD).content IS DISTINCT FROM (NEW).content
      OR (OLD).scope_dimensions IS DISTINCT FROM (NEW).scope_dimensions
      OR (OLD).evidence_references IS DISTINCT FROM (NEW).evidence_references
      OR (OLD).source_patch_id IS DISTINCT FROM (NEW).source_patch_id
      OR (OLD).valid_from IS DISTINCT FROM (NEW).valid_from
      OR (OLD).valid_until IS DISTINCT FROM (NEW).valid_until
      OR (OLD).expires_at IS DISTINCT FROM (NEW).expires_at
      OR (OLD).revoked IS DISTINCT FROM (NEW).revoked
      OR (OLD).created_at IS DISTINCT FROM (NEW).created_at
      OR (OLD).step30_proposal_id IS DISTINCT FROM (NEW).step30_proposal_id
      OR (OLD).step30_proposal_hash
        IS DISTINCT FROM (NEW).step30_proposal_hash
      OR (OLD).step30_approval_receipt_hash
        IS DISTINCT FROM (NEW).step30_approval_receipt_hash
      OR (OLD).step30_validation_receipt_hash
        IS DISTINCT FROM (NEW).step30_validation_receipt_hash
      OR (OLD).step30_patch_hash IS DISTINCT FROM (NEW).step30_patch_hash
      OR (OLD).step30_commit_receipt_hash
        IS DISTINCT FROM (NEW).step30_commit_receipt_hash
      OR (OLD).step30_state_hash IS NULL
      OR (NEW).step30_state_hash IS NULL
      OR (NEW).step30_activation_replay_identity IS NULL
      OR (NEW).step30_activation_receipt_hash IS NULL
      OR (NEW).step30_activation_payload IS NULL
    THEN
      RAISE EXCEPTION 'activation mutation is not the exact Step 30 edge'
        USING ERRCODE = '23514';
    END IF;
    UPDATE memory_patch.personal_memory_spaces AS space
       SET candidate_quota_epoch = space.candidate_quota_epoch + 1
     WHERE space.tenant_id = (NEW).tenant_id
       AND space.hat_scope_id = (NEW).hat_scope_id
       AND space.state IN ('CONFIGURED', 'ACTIVE');
    IF NOT EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS current_space
       WHERE current_space.tenant_id = (NEW).tenant_id
         AND current_space.hat_scope_id = (NEW).hat_scope_id
         AND current_space.state IN ('CONFIGURED', 'ACTIVE')
    ) THEN
      RAISE EXCEPTION 'Personal Memory target slot is not activation-eligible'
        USING ERRCODE = '23514';
    END IF;
    SELECT policy.maximum_active_memory_patches
      INTO maximum_active
      FROM memory_patch.personal_memory_spaces AS space
      JOIN memory_patch.personal_memory_quota_policies AS policy
        ON policy.tenant_id = space.tenant_id
       AND policy.owner_user_id = space.user_id
       AND policy.quota_policy_id = space.quota_policy_id
       AND policy.policy_digest = space.quota_policy_digest
     WHERE space.tenant_id = (NEW).tenant_id
       AND space.hat_scope_id = (NEW).hat_scope_id
       AND space.state IN ('CONFIGURED', 'ACTIVE');
    IF maximum_active IS NULL THEN
      RAISE EXCEPTION 'Personal Memory activation quota is unavailable'
        USING ERRCODE = '23514';
    END IF;
    IF maximum_active IS NOT NULL THEN
      SELECT count(*)
        INTO current_active
        FROM memory_patch.memory_items AS item
       WHERE item.tenant_id = (NEW).tenant_id
         AND item.hat_scope_id = (NEW).hat_scope_id
         AND item.trust_class = 'PERSONAL_VERIFIED_PATCH'
         AND item.active AND NOT item.revoked;
      IF current_active >= maximum_active THEN
        RAISE EXCEPTION 'Personal Memory active patch quota exceeded'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS memory_patch_proposals_s29_transition_guard
  ON memory_patch.memory_patch_proposals;
DROP TRIGGER IF EXISTS memory_patch_proposals_s30_transition_guard
  ON memory_patch.memory_patch_proposals;
CREATE TRIGGER memory_patch_proposals_s30_transition_guard
  BEFORE UPDATE ON memory_patch.memory_patch_proposals
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step30_proposal_transition();

DROP TRIGGER IF EXISTS memory_items_s30_guard
  ON memory_patch.memory_items;
CREATE TRIGGER memory_items_s30_guard
  BEFORE INSERT OR UPDATE ON memory_patch.memory_items
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step30_memory_item();

DROP TRIGGER IF EXISTS personal_memory_spaces_s30_commit_guard
  ON memory_patch.personal_memory_spaces;
CREATE TRIGGER personal_memory_spaces_s30_commit_guard
  BEFORE UPDATE ON memory_patch.personal_memory_spaces
  FOR EACH ROW
  EXECUTE FUNCTION memory_patch.guard_step30_commit_slot_update();

ALTER FUNCTION memory_patch.step30_commit_helper_authorized()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step30_commit_slot_update()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_approval_target_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_approval_transition_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_commit_target_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
  STRING, STRING, JSONB
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_memory_item_commit_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
  STRING, JSONB
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_proposal_lifecycle_matches(
  STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
  STRING, STRING, STRING, STRING, JSONB
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step30_transition_target_matches(
  STRING, STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step30_proposal_transition()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step30_memory_item()
  OWNER TO mp_schema_owner;

REVOKE ALL ON FUNCTION
  memory_patch.step30_commit_helper_authorized(),
  memory_patch.step30_approval_target_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step30_approval_transition_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step30_commit_target_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
    STRING, STRING, JSONB
  ),
  memory_patch.step30_memory_item_commit_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
    STRING, JSONB
  ),
  memory_patch.step30_proposal_lifecycle_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
    STRING, STRING, STRING, STRING, JSONB
  ),
  memory_patch.step30_transition_target_matches(
    STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.guard_step30_commit_slot_update(),
  memory_patch.guard_step30_proposal_transition(),
  memory_patch.guard_step30_memory_item()
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper;

GRANT EXECUTE ON FUNCTION
  memory_patch.step30_approval_target_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step30_approval_transition_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step30_transition_target_matches(
    STRING, STRING, STRING, STRING, STRING
  )
TO mp_app_runtime;

GRANT EXECUTE ON FUNCTION
  memory_patch.step30_commit_helper_authorized(),
  memory_patch.step30_commit_target_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
    STRING, STRING, JSONB
  ),
  memory_patch.step30_memory_item_commit_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
    STRING, JSONB
  ),
  memory_patch.step30_proposal_lifecycle_matches(
    STRING, STRING, STRING, STRING, STRING, STRING, STRING, STRING,
    STRING, STRING, STRING, STRING, JSONB
  ),
  memory_patch.step30_transition_target_matches(
    STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.tenant_context_matches(STRING),
  memory_patch.user_context_matches(STRING, STRING),
  memory_patch.scope_context_matches(STRING, STRING, STRING),
  memory_patch.hat_scope_context_matches(STRING, STRING),
  memory_patch.proposal_context_matches(STRING, STRING)
TO mp_personal_memory_commit_helper;

GRANT USAGE ON SCHEMA memory_patch
  TO mp_personal_memory_commit_helper;
GRANT SELECT ON TABLE
  memory_patch.tenants,
  memory_patch.users,
  memory_patch.kernel_runs,
  memory_patch.audit_events,
  memory_patch.personal_memory_spaces,
  memory_patch.personal_memory_quota_policies,
  memory_patch.personal_memory_model_bindings,
  memory_patch.hat_scopes,
  memory_patch.memory_patch_proposals,
  memory_patch.memory_patch_approvals,
  memory_patch.memory_patch_commits,
  memory_patch.patch_transition_records,
  memory_patch.memory_items,
  memory_patch.persistence_operations
TO mp_personal_memory_commit_helper;
GRANT INSERT ON TABLE
  memory_patch.memory_patch_commits,
  memory_patch.memory_items,
  memory_patch.patch_transition_records,
  memory_patch.persistence_operations
TO mp_personal_memory_commit_helper;
GRANT UPDATE ON TABLE
  memory_patch.memory_patch_proposals,
  memory_patch.memory_items,
  memory_patch.persistence_operations,
  memory_patch.personal_memory_spaces
TO mp_personal_memory_commit_helper;
REVOKE INSERT ON TABLE memory_patch.memory_patch_approvals
  FROM mp_personal_memory_commit_helper;
REVOKE UPDATE, DELETE ON TABLE
  memory_patch.memory_patch_approvals,
  memory_patch.memory_patch_commits,
  memory_patch.patch_transition_records
FROM mp_personal_memory_commit_helper;
REVOKE DELETE ON TABLE
  memory_patch.memory_patch_proposals,
  memory_patch.memory_items,
  memory_patch.persistence_operations
FROM mp_personal_memory_commit_helper;

GRANT INSERT ON TABLE memory_patch.memory_patch_approvals
  TO mp_app_runtime;

CREATE POLICY memory_patch_approvals_s30_insert
  ON memory_patch.memory_patch_approvals
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    target_scope = 'USER_PERSONAL_HAT'
    AND decision = 'APPROVE'
    AND approver_type = 'USER'
    AND approver_id = owner_user_id
    AND step30_approval_payload ->> 'actor_type' = 'HUMAN_USER'
    AND step30_approval_payload ->> 'actor_id' = owner_user_id
    AND step30_approval_payload ->> 'receipt_hash'
      = step30_approval_receipt_hash
    AND memory_patch.step30_approval_target_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      proposal_id,
      proposal_content_hash,
      step30_evidence_binding_hash,
      step30_validation_receipt_hash
    )
  );

CREATE POLICY memory_patch_proposals_s30_approve_update
  ON memory_patch.memory_patch_proposals
  FOR UPDATE
  TO mp_app_runtime
  USING (
    lifecycle_state = 'AWAITING_APPROVAL'
    AND step29_state_version = 4
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  )
  WITH CHECK (
    lifecycle_state = 'APPROVED'
    AND step29_state_version = 5
    AND proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchLifecycleState'
    AND memory_patch.step30_approval_transition_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      proposal_id,
      content_hash,
      step30_approval_id,
      step30_approval_receipt_hash
    )
  );

CREATE POLICY patch_transition_records_s30_approval_insert
  ON memory_patch.patch_transition_records
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    transition_id ~ '^step30-transition-[0-9a-f]{64}$'
    AND state_before = 'AWAITING_APPROVAL'
    AND state_after = 'APPROVED'
    AND actor_type = 'USER'
    AND memory_patch.user_context_matches(tenant_id, actor_id)
    AND memory_patch.step30_transition_target_matches(
      tenant_id,
      proposal_id,
      proposal_content_hash,
      state_after,
      step30_receipt_hash
    )
  );

CREATE POLICY personal_memory_spaces_s30_commit_select
  ON memory_patch.personal_memory_spaces
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY tenants_s30_commit_identity_select
  ON memory_patch.tenants
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.tenant_context_matches(tenant_id));
CREATE POLICY users_s30_commit_identity_select
  ON memory_patch.users
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY kernel_runs_s30_commit_lineage_select
  ON memory_patch.kernel_runs
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY audit_events_s30_commit_owner_select
  ON memory_patch.audit_events
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (
    user_id IS NOT NULL
    AND personal_memory_space_id IS NOT NULL
    AND memory_patch.user_context_matches(tenant_id, user_id)
  );
CREATE POLICY personal_memory_spaces_s30_commit_quota_lock_update
  ON memory_patch.personal_memory_spaces
  FOR UPDATE TO mp_personal_memory_commit_helper
  USING (
    state IN ('CONFIGURED', 'ACTIVE')
    AND memory_patch.user_context_matches(tenant_id, user_id)
  )
  WITH CHECK (
    state IN ('CONFIGURED', 'ACTIVE')
    AND memory_patch.user_context_matches(tenant_id, user_id)
  );
CREATE POLICY personal_memory_quota_policies_s30_commit_select
  ON memory_patch.personal_memory_quota_policies
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY personal_memory_model_bindings_s30_commit_select
  ON memory_patch.personal_memory_model_bindings
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY hat_scopes_s30_commit_select
  ON memory_patch.hat_scopes
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.scope_context_matches(tenant_id, target_scope, owner_user_id));
CREATE POLICY memory_patch_proposals_s30_commit_select
  ON memory_patch.memory_patch_proposals
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY memory_patch_approvals_s30_commit_select
  ON memory_patch.memory_patch_approvals
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY memory_patch_commits_s30_commit_select
  ON memory_patch.memory_patch_commits
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY patch_transition_records_s30_commit_select
  ON memory_patch.patch_transition_records
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.proposal_context_matches(tenant_id, proposal_id));
CREATE POLICY memory_items_s30_commit_select
  ON memory_patch.memory_items
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));
CREATE POLICY persistence_operations_s30_commit_select
  ON memory_patch.persistence_operations
  FOR SELECT TO mp_personal_memory_commit_helper
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY persistence_operations_s30_commit_insert
  ON memory_patch.persistence_operations
  FOR INSERT TO mp_personal_memory_commit_helper
  WITH CHECK (
    operation_kind IN (
      'PERSONAL_MEMORY_PATCH_TECHNICAL_COMMIT',
      'PERSONAL_MEMORY_PATCH_ACTIVATE'
    )
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  );
CREATE POLICY persistence_operations_s30_commit_update
  ON memory_patch.persistence_operations
  FOR UPDATE TO mp_personal_memory_commit_helper
  USING (
    operation_kind IN (
      'PERSONAL_MEMORY_PATCH_TECHNICAL_COMMIT',
      'PERSONAL_MEMORY_PATCH_ACTIVATE'
    )
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  )
  WITH CHECK (
    operation_kind IN (
      'PERSONAL_MEMORY_PATCH_TECHNICAL_COMMIT',
      'PERSONAL_MEMORY_PATCH_ACTIVATE'
    )
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  );

CREATE POLICY memory_patch_commits_s30_insert
  ON memory_patch.memory_patch_commits
  FOR INSERT TO mp_personal_memory_commit_helper
  WITH CHECK (
    actor_type = 'COMMIT_SERVICE'
    AND actor_id = 'personal-memory-commit-helper-1a'
    AND memory_patch.step30_commit_target_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      proposal_id,
      proposal_content_hash,
      approval_id,
      step30_approval_receipt_hash,
      step30_validation_receipt_hash,
      step30_patch_hash,
      step30_commit_receipt_hash,
      step30_commit_payload
    )
  );

CREATE POLICY memory_items_s30_commit_insert
  ON memory_patch.memory_items
  FOR INSERT TO mp_personal_memory_commit_helper
  WITH CHECK (
    step30_state_version = 6
    AND active = false
    AND trust_class = 'PERSONAL_VERIFIED_PATCH'
    AND memory_patch.step30_commit_helper_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
    AND memory_patch.step30_memory_item_commit_matches(
      tenant_id,
      hat_scope_id,
      source_patch_id,
      step30_proposal_id,
      step30_proposal_hash,
      step30_approval_receipt_hash,
      step30_validation_receipt_hash,
      step30_commit_receipt_hash,
      step30_patch_hash,
      content
    )
  );

CREATE POLICY memory_patch_proposals_s30_commit_update
  ON memory_patch.memory_patch_proposals
  FOR UPDATE TO mp_personal_memory_commit_helper
  USING (
    lifecycle_state IN ('APPROVED', 'COMMITTED')
    AND step29_state_version IN (5, 6)
    AND memory_patch.step30_commit_helper_authorized()
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
  )
  WITH CHECK (
    lifecycle_state IN ('COMMITTED', 'ACTIVE')
    AND step29_state_version IN (6, 7)
    AND proposed_content ->> 'contract_type'
      = 'PersonalMemoryPatchLifecycleState'
    AND memory_patch.step30_commit_helper_authorized()
    AND memory_patch.user_context_matches(tenant_id, owner_user_id)
    AND memory_patch.step30_proposal_lifecycle_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      proposal_id,
      content_hash,
      lifecycle_state,
      step30_approval_id,
      step30_approval_receipt_hash,
      step29_validation_receipt_hash,
      step30_commit_receipt_hash,
      step30_activation_receipt_hash,
      step30_patch_id,
      proposed_content
    )
  );

CREATE POLICY memory_items_s30_activate_update
  ON memory_patch.memory_items
  FOR UPDATE TO mp_personal_memory_commit_helper
  USING (
    step30_state_version = 6
    AND active = false
    AND memory_patch.step30_commit_helper_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  )
  WITH CHECK (
    step30_state_version = 7
    AND active = true
    AND step30_activation_receipt_hash ~ '^[0-9a-f]{64}$'
    AND memory_patch.step30_commit_helper_authorized()
    AND memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY patch_transition_records_s30_commit_insert
  ON memory_patch.patch_transition_records
  FOR INSERT TO mp_personal_memory_commit_helper
  WITH CHECK (
    transition_id ~ '^step30-transition-[0-9a-f]{64}$'
    AND (
      (state_before = 'APPROVED' AND state_after = 'COMMITTED'
        AND actor_type = 'COMMIT_SERVICE'
        AND actor_id = 'personal-memory-commit-helper-1a')
      OR
      (state_before = 'COMMITTED' AND state_after = 'ACTIVE'
        AND actor_type = 'SYSTEM'
        AND actor_id = 'personal-memory-activation-service-1a')
    )
    AND memory_patch.step30_commit_helper_authorized()
    AND memory_patch.step30_transition_target_matches(
      tenant_id,
      proposal_id,
      proposal_content_hash,
      state_after,
      step30_receipt_hash
    )
  );

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
ALTER TABLE memory_patch.persistence_operations
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.persistence_operations
  FORCE ROW LEVEL SECURITY;
