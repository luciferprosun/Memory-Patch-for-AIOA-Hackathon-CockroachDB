-- Memory Patch Step 34 - bounded human review workspace, typed decisions,
-- least-privileged reviewer access, and atomic Step 33 audit integration.
-- This migration creates no Personal Memory end-user UI, source publication,
-- model authority, or external-execution authority.

-- STEP34_CLUSTER_ROLE_DDL_BEGIN
CREATE ROLE IF NOT EXISTS mp_human_reviewer;
CREATE ROLE IF NOT EXISTS mp_review_service;

ALTER ROLE mp_human_reviewer
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE mp_review_service
  WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;

REVOKE admin FROM mp_human_reviewer;
REVOKE admin FROM mp_review_service;
REVOKE mp_schema_owner FROM mp_human_reviewer;
REVOKE mp_security_owner FROM mp_human_reviewer;
REVOKE mp_app_runtime FROM mp_human_reviewer;
REVOKE mp_personal_memory_commit_helper FROM mp_human_reviewer;
REVOKE mp_review_service FROM mp_human_reviewer;
REVOKE mp_schema_owner FROM mp_review_service;
REVOKE mp_security_owner FROM mp_review_service;
REVOKE mp_app_runtime FROM mp_review_service;
REVOKE mp_personal_memory_commit_helper FROM mp_review_service;
REVOKE mp_human_reviewer FROM mp_review_service;
REVOKE mp_human_reviewer FROM mp_app_runtime;
REVOKE mp_review_service FROM mp_app_runtime;
REVOKE mp_human_reviewer FROM mp_personal_memory_commit_helper;
REVOKE mp_review_service FROM mp_personal_memory_commit_helper;
-- STEP34_CLUSTER_ROLE_DDL_END

CREATE TABLE memory_patch.reviewer_authorizations (
  tenant_id STRING NOT NULL,
  authorization_id STRING NOT NULL,
  reviewer_id STRING NOT NULL,
  reviewer_role STRING NOT NULL,
  case_type STRING NOT NULL,
  owner_user_id STRING,
  access_policy_id STRING NOT NULL,
  access_policy_version STRING NOT NULL,
  access_policy_digest STRING NOT NULL,
  active BOOL NOT NULL,
  authorization_hash STRING NOT NULL,
  authorization_payload JSONB NOT NULL,
  granted_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, authorization_id),
  UNIQUE (
    tenant_id, reviewer_id, reviewer_role, case_type, owner_user_id
  ),
  CONSTRAINT reviewer_authorizations_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id) ON DELETE RESTRICT,
  CONSTRAINT reviewer_authorizations_reviewer_fk
    FOREIGN KEY (tenant_id, reviewer_id)
    REFERENCES memory_patch.users (tenant_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT reviewer_authorizations_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT reviewer_authorizations_shape CHECK (
    authorization_id ~ '^reviewer-authorization-[0-9a-f]{64}$'
    AND reviewer_role IN (
      'ANSWER_REVIEWER', 'SHARED_MEMORY_REVIEWER', 'SENIOR_REVIEWER'
    )
    AND case_type IN (
      'ANSWER_VERIFICATION_FAILURE', 'ANSWER_CONFLICTING_EVIDENCE',
      'ANSWER_INSUFFICIENT_EVIDENCE', 'ANSWER_STALE_EVIDENCE',
      'ANSWER_CONFIRMATION_REQUIRED', 'SHARED_MEMORY_PROMOTION',
      'SHARED_PROMOTION_PRIVACY_REVIEW',
      'SHARED_PROMOTION_CANONICAL_CONFLICT'
    )
    AND access_policy_id = 'human-review-access-policy-1a'
    AND access_policy_version = '1'
    AND access_policy_digest ~ '^[0-9a-f]{64}$'
    AND active = true
    AND authorization_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(authorization_payload) = 'object'
    AND authorization_payload ->> 'schema_version'
      = 'human-review-workspace-1.0.0'
    AND authorization_payload ->> 'authorization_id' = authorization_id
    AND authorization_payload ->> 'tenant_id' = tenant_id
    AND authorization_payload ->> 'reviewer_id' = reviewer_id
    AND authorization_payload ->> 'reviewer_role' = reviewer_role
    AND authorization_payload ->> 'case_type' = case_type
    AND (authorization_payload ->> 'owner_user_id')
      IS NOT DISTINCT FROM owner_user_id
    AND authorization_payload ->> 'access_policy_digest'
      = access_policy_digest
    AND authorization_payload ->> 'authorization_hash'
      = authorization_hash
    AND ((authorization_payload ->> 'active')::BOOL) IS TRUE
  )
);

CREATE TABLE memory_patch.human_review_cases (
  tenant_id STRING NOT NULL,
  review_case_id STRING NOT NULL,
  trigger_hash STRING NOT NULL,
  case_type STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  subject_type STRING NOT NULL,
  subject_id STRING NOT NULL,
  subject_hash STRING NOT NULL,
  source_audit_event_hash STRING NOT NULL,
  source_chain_id STRING NOT NULL,
  audit_verification_result_hash STRING NOT NULL,
  audit_context_verified BOOL NOT NULL,
  source_context_hash STRING NOT NULL,
  review_state STRING NOT NULL,
  review_state_version INT8 NOT NULL,
  priority STRING NOT NULL,
  claimed_reviewer_id STRING,
  claimed_reviewer_role STRING,
  case_hash STRING NOT NULL,
  case_payload JSONB NOT NULL,
  source_context_payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, review_case_id),
  UNIQUE (tenant_id, trigger_hash),
  CONSTRAINT human_review_cases_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id) ON DELETE RESTRICT,
  CONSTRAINT human_review_cases_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT human_review_cases_reviewer_fk
    FOREIGN KEY (tenant_id, claimed_reviewer_id)
    REFERENCES memory_patch.users (tenant_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT human_review_cases_shape CHECK (
    review_case_id ~ '^human-review-case-[0-9a-f]{64}$'
    AND trigger_hash ~ '^[0-9a-f]{64}$'
    AND case_type IN (
      'ANSWER_VERIFICATION_FAILURE', 'ANSWER_CONFLICTING_EVIDENCE',
      'ANSWER_INSUFFICIENT_EVIDENCE', 'ANSWER_STALE_EVIDENCE',
      'ANSWER_CONFIRMATION_REQUIRED', 'SHARED_MEMORY_PROMOTION',
      'SHARED_PROMOTION_PRIVACY_REVIEW',
      'SHARED_PROMOTION_CANONICAL_CONFLICT'
    )
    AND subject_type IN (
      'ANSWER_REVIEW_RESULT', 'SHARED_MEMORY_PROMOTION_PROPOSAL'
    )
    AND subject_hash ~ '^[0-9a-f]{64}$'
    AND source_audit_event_hash ~ '^[0-9a-f]{64}$'
    AND source_chain_id ~ '^audit-chain-[0-9a-f]{64}$'
    AND audit_verification_result_hash ~ '^[0-9a-f]{64}$'
    AND source_context_hash ~ '^[0-9a-f]{64}$'
    AND priority IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')
    AND (
      (review_state = 'OPEN' AND review_state_version = 1
        AND claimed_reviewer_id IS NULL
        AND claimed_reviewer_role IS NULL)
      OR
      (review_state = 'CLAIMED' AND review_state_version = 2
        AND claimed_reviewer_id IS NOT NULL
        AND claimed_reviewer_role IS NOT NULL)
      OR
      (review_state = 'IN_REVIEW' AND review_state_version = 3
        AND claimed_reviewer_id IS NOT NULL
        AND claimed_reviewer_role IS NOT NULL)
      OR
      (review_state IN ('RESOLVED', 'ESCALATED', 'STALE')
        AND review_state_version = 4
        AND claimed_reviewer_id IS NOT NULL
        AND claimed_reviewer_role IS NOT NULL)
    )
    AND (
      claimed_reviewer_role IS NULL
      OR claimed_reviewer_role IN (
        'ANSWER_REVIEWER', 'SHARED_MEMORY_REVIEWER', 'SENIOR_REVIEWER'
      )
    )
    AND case_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(case_payload) = 'object'
    AND jsonb_typeof(source_context_payload) = 'object'
    AND octet_length(source_context_payload::STRING) <= 65536
    AND case_payload ->> 'schema_version'
      = 'human-review-workspace-1.0.0'
    AND case_payload ->> 'review_case_id' = review_case_id
    AND case_payload ->> 'trigger_hash' = trigger_hash
    AND case_payload ->> 'case_type' = case_type
    AND case_payload ->> 'tenant_id' = tenant_id
    AND case_payload ->> 'owner_user_id' = owner_user_id
    AND case_payload ->> 'subject_type' = subject_type
    AND case_payload ->> 'subject_id' = subject_id
    AND case_payload ->> 'subject_hash' = subject_hash
    AND case_payload ->> 'source_audit_event_hash'
      = source_audit_event_hash
    AND case_payload ->> 'source_chain_id' = source_chain_id
    AND case_payload ->> 'audit_verification_result_hash'
      = audit_verification_result_hash
    AND ((case_payload ->> 'audit_context_verified')::BOOL)
      = audit_context_verified
    AND case_payload ->> 'source_context_hash' = source_context_hash
    AND case_payload ->> 'review_state' = review_state
    AND (case_payload ->> 'review_state_version')::INT8
      = review_state_version
    AND case_payload ->> 'priority' = priority
    AND (case_payload ->> 'claimed_reviewer_id')
      IS NOT DISTINCT FROM claimed_reviewer_id
    AND (case_payload ->> 'claimed_reviewer_role')
      IS NOT DISTINCT FROM claimed_reviewer_role
    AND case_payload ->> 'case_hash' = case_hash
    AND source_context_payload ->> 'context_hash'
      IS NOT DISTINCT FROM source_context_hash
    AND source_context_payload ->> 'subject_hash'
      IS NOT DISTINCT FROM subject_hash
    AND ((source_context_payload ->> 'canonical_evidence')::BOOL) IS FALSE
    AND ((source_context_payload ->> 'model_authority')::BOOL) IS FALSE
    AND (
      (
        case_type IN (
          'ANSWER_VERIFICATION_FAILURE', 'ANSWER_CONFLICTING_EVIDENCE',
          'ANSWER_INSUFFICIENT_EVIDENCE', 'ANSWER_STALE_EVIDENCE',
          'ANSWER_CONFIRMATION_REQUIRED'
        )
        AND subject_type = 'ANSWER_REVIEW_RESULT'
        AND (
          source_context_payload ->> 'source_contract' IN (
            'STEP26_HUMAN_REVIEW_REQUIRED',
            'STEP26_BOUNDED_ANSWER_FAILURE'
          )
        ) IS TRUE
        AND case_payload ->> 'request_id' IS NOT NULL
        AND case_payload ->> 'route_hash' ~ '^[0-9a-f]{64}$'
        AND ((source_context_payload -> 'context_payload')
          ->> 'answer_returned')::BOOL IS FALSE
        AND (
          (
            source_context_payload ->> 'source_contract'
              = 'STEP26_HUMAN_REVIEW_REQUIRED'
            AND ((case_payload -> 'required_context_refs')
              ->> 'human_review_result_hash')
              IS NOT DISTINCT FROM subject_hash
          )
          OR
          (
            source_context_payload ->> 'source_contract'
              = 'STEP26_BOUNDED_ANSWER_FAILURE'
            AND ((case_payload -> 'required_context_refs')
              ->> 'bounded_failure_hash')
              IS NOT DISTINCT FROM subject_hash
          )
        )
      )
      OR
      (
        case_type IN (
          'SHARED_MEMORY_PROMOTION',
          'SHARED_PROMOTION_PRIVACY_REVIEW',
          'SHARED_PROMOTION_CANONICAL_CONFLICT'
        )
        AND subject_type = 'SHARED_MEMORY_PROMOTION_PROPOSAL'
        AND source_context_payload ->> 'source_contract'
          IS NOT DISTINCT FROM 'STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL'
        AND case_payload ->> 'request_id' IS NULL
        AND case_payload ->> 'kernel_run_id' IS NULL
        AND case_payload ->> 'route_hash' IS NULL
        AND ((case_payload -> 'required_context_refs')
          ->> 'promotion_proposal_hash')
          IS NOT DISTINCT FROM subject_hash
        AND ((source_context_payload -> 'context_payload')
          ->> 'review_required')::BOOL IS TRUE
        AND ((source_context_payload -> 'context_payload')
          ->> 'source_registry_published')::BOOL IS FALSE
      )
    )
    AND updated_at >= created_at
  )
);

CREATE TABLE memory_patch.human_review_claims (
  tenant_id STRING NOT NULL,
  claim_id STRING NOT NULL,
  review_case_id STRING NOT NULL,
  reviewer_id STRING NOT NULL,
  reviewer_role STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  previous_case_hash STRING NOT NULL,
  claimed_case_hash STRING NOT NULL,
  audit_event_hash STRING NOT NULL,
  claim_receipt_hash STRING NOT NULL,
  claim_payload JSONB NOT NULL,
  claimed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, claim_id),
  UNIQUE (tenant_id, reviewer_id, replay_identity),
  UNIQUE (tenant_id, review_case_id),
  CONSTRAINT human_review_claims_case_fk
    FOREIGN KEY (tenant_id, review_case_id)
    REFERENCES memory_patch.human_review_cases (tenant_id, review_case_id)
    ON DELETE RESTRICT,
  CONSTRAINT human_review_claims_reviewer_fk
    FOREIGN KEY (tenant_id, reviewer_id)
    REFERENCES memory_patch.users (tenant_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT human_review_claims_shape CHECK (
    claim_id ~ '^human-review-claim-[0-9a-f]{64}$'
    AND reviewer_role IN (
      'ANSWER_REVIEWER', 'SHARED_MEMORY_REVIEWER', 'SENIOR_REVIEWER'
    )
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^human-review-replay-[0-9a-f]{64}$'
    AND previous_case_hash ~ '^[0-9a-f]{64}$'
    AND claimed_case_hash ~ '^[0-9a-f]{64}$'
    AND audit_event_hash ~ '^[0-9a-f]{64}$'
    AND claim_receipt_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(claim_payload) = 'object'
    AND claim_payload ->> 'claim_id' = claim_id
    AND claim_payload ->> 'review_case_id' = review_case_id
    AND claim_payload ->> 'reviewer_id' = reviewer_id
    AND claim_payload ->> 'reviewer_role' = reviewer_role
    AND claim_payload ->> 'request_hash' = request_hash
    AND claim_payload ->> 'replay_identity' = replay_identity
    AND claim_payload ->> 'previous_case_hash' = previous_case_hash
    AND claim_payload ->> 'claimed_case_hash' = claimed_case_hash
    AND claim_payload ->> 'audit_event_hash' = audit_event_hash
    AND claim_payload ->> 'receipt_hash' = claim_receipt_hash
  )
);

CREATE TABLE memory_patch.human_review_decisions (
  tenant_id STRING NOT NULL,
  decision_id STRING NOT NULL,
  review_case_id STRING NOT NULL,
  reviewer_id STRING NOT NULL,
  reviewer_role STRING NOT NULL,
  case_type STRING NOT NULL,
  decision_type STRING NOT NULL,
  command_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  previous_case_hash STRING NOT NULL,
  in_review_case_hash STRING NOT NULL,
  subject_hash STRING NOT NULL,
  decision_policy_digest STRING NOT NULL,
  decision_hash STRING NOT NULL,
  audit_event_hash STRING NOT NULL,
  decision_receipt_hash STRING NOT NULL,
  decision_payload JSONB NOT NULL,
  decision_receipt_payload JSONB NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, decision_id),
  UNIQUE (tenant_id, reviewer_id, replay_identity),
  UNIQUE (tenant_id, review_case_id),
  CONSTRAINT human_review_decisions_case_fk
    FOREIGN KEY (tenant_id, review_case_id)
    REFERENCES memory_patch.human_review_cases (tenant_id, review_case_id)
    ON DELETE RESTRICT,
  CONSTRAINT human_review_decisions_reviewer_fk
    FOREIGN KEY (tenant_id, reviewer_id)
    REFERENCES memory_patch.users (tenant_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT human_review_decisions_shape CHECK (
    decision_id ~ '^human-review-decision-[0-9a-f]{64}$'
    AND reviewer_role IN (
      'ANSWER_REVIEWER', 'SHARED_MEMORY_REVIEWER', 'SENIOR_REVIEWER'
    )
    AND case_type IN (
      'ANSWER_VERIFICATION_FAILURE', 'ANSWER_CONFLICTING_EVIDENCE',
      'ANSWER_INSUFFICIENT_EVIDENCE', 'ANSWER_STALE_EVIDENCE',
      'ANSWER_CONFIRMATION_REQUIRED', 'SHARED_MEMORY_PROMOTION',
      'SHARED_PROMOTION_PRIVACY_REVIEW',
      'SHARED_PROMOTION_CANONICAL_CONFLICT'
    )
    AND decision_type IN (
      'ALLOW_QUALIFIED_ANSWER', 'REJECT_ANSWER',
      'REQUEST_MORE_EVIDENCE', 'CONFIRMATION_REQUIRED',
      'APPROVE_SHARED_PROMOTION_CANDIDATE',
      'REJECT_SHARED_PROMOTION', 'REQUEST_REDACTION_CHANGES',
      'ESCALATE'
    )
    AND command_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^human-review-replay-[0-9a-f]{64}$'
    AND previous_case_hash ~ '^[0-9a-f]{64}$'
    AND in_review_case_hash ~ '^[0-9a-f]{64}$'
    AND subject_hash ~ '^[0-9a-f]{64}$'
    AND decision_policy_digest ~ '^[0-9a-f]{64}$'
    AND decision_hash ~ '^[0-9a-f]{64}$'
    AND audit_event_hash ~ '^[0-9a-f]{64}$'
    AND decision_receipt_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(decision_payload) = 'object'
    AND jsonb_typeof(decision_receipt_payload) = 'object'
    AND decision_payload ->> 'decision_id' = decision_id
    AND decision_payload ->> 'review_case_id' = review_case_id
    AND decision_payload ->> 'reviewer_id' = reviewer_id
    AND decision_payload ->> 'reviewer_role' = reviewer_role
    AND decision_payload ->> 'case_type' = case_type
    AND decision_payload ->> 'decision_type' = decision_type
    AND decision_payload ->> 'command_hash' = command_hash
    AND decision_payload ->> 'replay_identity' = replay_identity
    AND decision_payload ->> 'review_case_hash' = previous_case_hash
    AND decision_payload ->> 'subject_hash' = subject_hash
    AND decision_payload ->> 'decision_policy_digest'
      = decision_policy_digest
    AND decision_payload ->> 'decision_hash' = decision_hash
    AND ((decision_payload ->> 'reviewer_note_is_canonical_evidence')::BOOL)
      IS FALSE
    AND ((decision_payload ->> 'external_execution_authority')::BOOL)
      IS FALSE
    AND ((decision_payload ->> 'source_publication_authority')::BOOL)
      IS FALSE
    AND decision_receipt_payload ->> 'decision_hash' = decision_hash
    AND decision_receipt_payload ->> 'in_review_case_hash'
      = in_review_case_hash
    AND decision_receipt_payload ->> 'audit_event_hash' = audit_event_hash
    AND decision_receipt_payload ->> 'receipt_hash'
      = decision_receipt_hash
    AND ((decision_receipt_payload ->> 'handoff_completed')::BOOL)
      IS FALSE
  )
);

CREATE TABLE memory_patch.human_review_handoffs (
  tenant_id STRING NOT NULL,
  handoff_id STRING NOT NULL,
  review_case_id STRING NOT NULL,
  decision_id STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  decision_hash STRING NOT NULL,
  decision_receipt_hash STRING NOT NULL,
  handoff_result_hash STRING NOT NULL,
  terminal_case_hash STRING NOT NULL,
  terminal_state STRING NOT NULL,
  audit_event_hash STRING NOT NULL,
  handoff_receipt_hash STRING NOT NULL,
  handoff_result_payload JSONB NOT NULL,
  handoff_receipt_payload JSONB NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, handoff_id),
  UNIQUE (tenant_id, replay_identity),
  UNIQUE (tenant_id, review_case_id),
  UNIQUE (tenant_id, decision_id),
  CONSTRAINT human_review_handoffs_case_fk
    FOREIGN KEY (tenant_id, review_case_id)
    REFERENCES memory_patch.human_review_cases (tenant_id, review_case_id)
    ON DELETE RESTRICT,
  CONSTRAINT human_review_handoffs_decision_fk
    FOREIGN KEY (tenant_id, decision_id)
    REFERENCES memory_patch.human_review_decisions (tenant_id, decision_id)
    ON DELETE RESTRICT,
  CONSTRAINT human_review_handoffs_shape CHECK (
    handoff_id ~ '^review-decision-handoff-[0-9a-f]{64}$'
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^human-review-replay-[0-9a-f]{64}$'
    AND decision_hash ~ '^[0-9a-f]{64}$'
    AND decision_receipt_hash ~ '^[0-9a-f]{64}$'
    AND handoff_result_hash ~ '^[0-9a-f]{64}$'
    AND terminal_case_hash ~ '^[0-9a-f]{64}$'
    AND terminal_state IN ('RESOLVED', 'ESCALATED')
    AND audit_event_hash ~ '^[0-9a-f]{64}$'
    AND handoff_receipt_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(handoff_result_payload) = 'object'
    AND jsonb_typeof(handoff_receipt_payload) = 'object'
    AND handoff_result_payload ->> 'review_case_id' = review_case_id
    AND handoff_result_payload ->> 'decision_id' = decision_id
    AND handoff_result_payload ->> 'decision_hash' = decision_hash
    AND handoff_result_payload ->> 'result_hash' = handoff_result_hash
    AND ((handoff_result_payload ->> 'answer_returned')::BOOL) IS FALSE
    AND ((handoff_result_payload ->> 'source_registry_published')::BOOL)
      IS FALSE
    AND ((handoff_result_payload ->> 'private_source_mutated')::BOOL)
      IS FALSE
    AND ((handoff_result_payload ->> 'external_execution_authority')::BOOL)
      IS FALSE
    AND handoff_receipt_payload ->> 'handoff_id' = handoff_id
    AND handoff_receipt_payload ->> 'request_hash' = request_hash
    AND handoff_receipt_payload ->> 'replay_identity' = replay_identity
    AND handoff_receipt_payload ->> 'decision_hash' = decision_hash
    AND handoff_receipt_payload ->> 'decision_receipt_hash'
      = decision_receipt_hash
    AND handoff_receipt_payload ->> 'handoff_result_hash'
      = handoff_result_hash
    AND handoff_receipt_payload ->> 'terminal_case_hash'
      = terminal_case_hash
    AND handoff_receipt_payload ->> 'terminal_state' = terminal_state
    AND handoff_receipt_payload ->> 'audit_event_hash' = audit_event_hash
    AND handoff_receipt_payload ->> 'receipt_hash'
      = handoff_receipt_hash
    AND ((handoff_receipt_payload ->> 'succeeded')::BOOL) IS TRUE
  )
);

CREATE INDEX human_review_cases_s34_queue_idx
  ON memory_patch.human_review_cases (
    tenant_id, review_state, case_type, priority, created_at, review_case_id
  );
CREATE INDEX human_review_cases_s34_reviewer_idx
  ON memory_patch.human_review_cases (
    tenant_id, claimed_reviewer_id, review_state, review_case_id
  );
CREATE INDEX reviewer_authorizations_s34_lookup_idx
  ON memory_patch.reviewer_authorizations (
    tenant_id, reviewer_id, reviewer_role, case_type, owner_user_id, active
  );

CREATE OR REPLACE FUNCTION memory_patch.step34_reviewer_authorized(
  p_tenant_id STRING,
  p_case_type STRING,
  p_owner_user_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_human_reviewer', 'MEMBER'
         )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.reviewer_authorizations AS review_auth
       WHERE review_auth.tenant_id = p_tenant_id
         AND review_auth.case_type = p_case_type
         AND review_auth.active
         AND (
           review_auth.owner_user_id IS NULL
           OR review_auth.owner_user_id = p_owner_user_id
         )
         AND memory_patch.user_context_matches(
           review_auth.tenant_id, review_auth.reviewer_id
         )
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step34_reviewer_owner_authorized(
  p_tenant_id STRING,
  p_owner_user_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_human_reviewer', 'MEMBER'
         )
    AND EXISTS (
      SELECT 1
        FROM memory_patch.reviewer_authorizations AS review_auth
       WHERE review_auth.tenant_id = p_tenant_id
         AND review_auth.active
         AND (
           review_auth.owner_user_id IS NULL
           OR review_auth.owner_user_id = p_owner_user_id
         )
         AND memory_patch.user_context_matches(
           review_auth.tenant_id, review_auth.reviewer_id
         )
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step34_review_service_authorized(
  p_tenant_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT pg_catalog.pg_has_role(
           session_user, 'mp_review_service', 'MEMBER'
         )
    AND memory_patch.tenant_context_matches(p_tenant_id)
$$;

CREATE OR REPLACE FUNCTION memory_patch.step34_reviewer_case_authorized(
  p_tenant_id STRING,
  p_review_case_id STRING,
  p_required_state STRING,
  p_required_reviewer_id STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM memory_patch.human_review_cases AS scoped_case
     WHERE scoped_case.tenant_id = p_tenant_id
       AND scoped_case.review_case_id = p_review_case_id
       AND (
         p_required_state IS NULL
         OR scoped_case.review_state = p_required_state
       )
       AND (
         p_required_reviewer_id IS NULL
         OR scoped_case.claimed_reviewer_id = p_required_reviewer_id
       )
       AND memory_patch.step34_reviewer_authorized(
         scoped_case.tenant_id,
         scoped_case.case_type,
         scoped_case.owner_user_id
       )
  )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step34_review_service_case_state_matches(
  p_tenant_id STRING,
  p_review_case_id STRING,
  p_required_state STRING
)
RETURNS BOOL
LANGUAGE SQL
VOLATILE
SECURITY INVOKER
AS $$
  SELECT memory_patch.step34_review_service_authorized(p_tenant_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.human_review_cases AS scoped_case
       WHERE scoped_case.tenant_id = p_tenant_id
         AND scoped_case.review_case_id = p_review_case_id
         AND scoped_case.review_state = p_required_state
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step34_review_append_only()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  RAISE EXCEPTION 'Step 34 review records are append-only'
    USING ERRCODE = '42501';
  RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step34_review_case_transition()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF (NEW).review_state <> 'OPEN'
      OR (NEW).review_state_version <> 1
      OR (NEW).claimed_reviewer_id IS NOT NULL
      OR (NEW).claimed_reviewer_role IS NOT NULL
    THEN
      RAISE EXCEPTION 'Step 34 review case must start OPEN at version 1'
        USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;

  IF (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).review_case_id IS DISTINCT FROM (OLD).review_case_id
    OR (NEW).trigger_hash IS DISTINCT FROM (OLD).trigger_hash
    OR (NEW).case_type IS DISTINCT FROM (OLD).case_type
    OR (NEW).owner_user_id IS DISTINCT FROM (OLD).owner_user_id
    OR (NEW).subject_type IS DISTINCT FROM (OLD).subject_type
    OR (NEW).subject_id IS DISTINCT FROM (OLD).subject_id
    OR (NEW).subject_hash IS DISTINCT FROM (OLD).subject_hash
    OR (NEW).source_audit_event_hash
      IS DISTINCT FROM (OLD).source_audit_event_hash
    OR (NEW).source_chain_id IS DISTINCT FROM (OLD).source_chain_id
    OR (NEW).audit_verification_result_hash
      IS DISTINCT FROM (OLD).audit_verification_result_hash
    OR (NEW).audit_context_verified
      IS DISTINCT FROM (OLD).audit_context_verified
    OR (NEW).source_context_hash IS DISTINCT FROM (OLD).source_context_hash
    OR (NEW).priority IS DISTINCT FROM (OLD).priority
    OR (NEW).source_context_payload IS DISTINCT FROM (OLD).source_context_payload
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
    OR (NEW).updated_at < (OLD).updated_at
    OR (NEW).review_state_version <> (OLD).review_state_version + 1
  THEN
    RAISE EXCEPTION 'Step 34 review case immutable fields changed'
      USING ERRCODE = '23514';
  END IF;

  IF (OLD).review_state = 'OPEN' AND (NEW).review_state = 'CLAIMED' THEN
    IF (NEW).review_state_version <> 2
      OR (NEW).claimed_reviewer_id IS NULL
      OR (NEW).claimed_reviewer_role IS NULL
      OR NOT EXISTS (
        SELECT 1 FROM memory_patch.human_review_claims AS claim
         WHERE claim.tenant_id = (NEW).tenant_id
           AND claim.review_case_id = (NEW).review_case_id
           AND claim.reviewer_id = (NEW).claimed_reviewer_id
           AND claim.reviewer_role = (NEW).claimed_reviewer_role
           AND claim.previous_case_hash = (OLD).case_hash
           AND claim.claimed_case_hash = (NEW).case_hash
           AND EXISTS (
             SELECT 1 FROM memory_patch.audit_events AS event
              WHERE event.tenant_id = claim.tenant_id
                AND event.event_hash = claim.audit_event_hash
                AND event.event_type = 'REVIEW_CASE_CLAIMED'
                AND event.subject_id = claim.review_case_id
           )
      )
    THEN
      RAISE EXCEPTION 'Step 34 claim transition is detached'
        USING ERRCODE = '23514';
    END IF;
  ELSIF (OLD).review_state = 'CLAIMED'
    AND (NEW).review_state = 'IN_REVIEW' THEN
    IF (NEW).review_state_version <> 3
      OR (NEW).claimed_reviewer_id IS DISTINCT FROM (OLD).claimed_reviewer_id
      OR (NEW).claimed_reviewer_role
        IS DISTINCT FROM (OLD).claimed_reviewer_role
      OR NOT EXISTS (
        SELECT 1 FROM memory_patch.human_review_decisions AS decision
         WHERE decision.tenant_id = (NEW).tenant_id
           AND decision.review_case_id = (NEW).review_case_id
           AND decision.reviewer_id = (NEW).claimed_reviewer_id
           AND decision.reviewer_role = (NEW).claimed_reviewer_role
           AND decision.previous_case_hash = (OLD).case_hash
           AND decision.in_review_case_hash = (NEW).case_hash
           AND EXISTS (
             SELECT 1 FROM memory_patch.audit_events AS event
              WHERE event.tenant_id = decision.tenant_id
                AND event.event_hash = decision.audit_event_hash
                AND event.event_type = 'REVIEW_DECISION_RECORDED'
                AND event.subject_id = decision.review_case_id
           )
      )
    THEN
      RAISE EXCEPTION 'Step 34 decision transition is detached'
        USING ERRCODE = '23514';
    END IF;
  ELSIF (OLD).review_state = 'IN_REVIEW'
    AND (NEW).review_state IN ('RESOLVED', 'ESCALATED') THEN
    IF (NEW).review_state_version <> 4
      OR (NEW).claimed_reviewer_id IS DISTINCT FROM (OLD).claimed_reviewer_id
      OR (NEW).claimed_reviewer_role
        IS DISTINCT FROM (OLD).claimed_reviewer_role
      OR NOT EXISTS (
        SELECT 1 FROM memory_patch.human_review_handoffs AS handoff
         WHERE handoff.tenant_id = (NEW).tenant_id
           AND handoff.review_case_id = (NEW).review_case_id
           AND handoff.terminal_case_hash = (NEW).case_hash
           AND handoff.terminal_state = (NEW).review_state
           AND EXISTS (
             SELECT 1 FROM memory_patch.audit_events AS event
              WHERE event.tenant_id = handoff.tenant_id
                AND event.event_hash = handoff.audit_event_hash
                AND event.event_type = 'REVIEW_HANDOFF_SUCCEEDED'
                AND event.subject_id = handoff.review_case_id
           )
      )
    THEN
      RAISE EXCEPTION 'Step 34 handoff transition is detached'
        USING ERRCODE = '23514';
    END IF;
  ELSE
    RAISE EXCEPTION 'Step 34 review state transition is forbidden'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER reviewer_authorizations_s34_append_only
  BEFORE UPDATE OR DELETE ON memory_patch.reviewer_authorizations
  FOR EACH ROW EXECUTE FUNCTION memory_patch.guard_step34_review_append_only();
CREATE TRIGGER human_review_claims_s34_append_only
  BEFORE UPDATE OR DELETE ON memory_patch.human_review_claims
  FOR EACH ROW EXECUTE FUNCTION memory_patch.guard_step34_review_append_only();
CREATE TRIGGER human_review_decisions_s34_append_only
  BEFORE UPDATE OR DELETE ON memory_patch.human_review_decisions
  FOR EACH ROW EXECUTE FUNCTION memory_patch.guard_step34_review_append_only();
CREATE TRIGGER human_review_handoffs_s34_append_only
  BEFORE UPDATE OR DELETE ON memory_patch.human_review_handoffs
  FOR EACH ROW EXECUTE FUNCTION memory_patch.guard_step34_review_append_only();
CREATE TRIGGER human_review_cases_s34_transition_guard
  BEFORE INSERT OR UPDATE ON memory_patch.human_review_cases
  FOR EACH ROW EXECUTE FUNCTION memory_patch.guard_step34_review_case_transition();

-- Extend only the Step 33 closed vocabularies.  Hash domains, sequence,
-- canonical serialization, chain partition, and append-only rules are unchanged.
ALTER TABLE memory_patch.audit_events
  DROP CONSTRAINT audit_events_actor_type;
ALTER TABLE memory_patch.audit_events
  ADD CONSTRAINT audit_events_actor_type CHECK (
    actor_type IN (
      'USER', 'HUMAN_REVIEWER', 'SYSTEM', 'COMMIT_SERVICE',
      'KNOWLEDGE_KERNEL', 'KNOWLEDGE_HAT', 'KNOWLEDGE_HUB',
      'CRITIC_PROMPT_LOOP', 'MODEL', 'MODEL_VERIFIER',
      'MIGRATION_SERVICE', 'HUMAN_USER', 'KERNEL', 'MODEL_ADAPTER',
      'CRITIC_LOOP', 'COMMIT_HELPER', 'ACTIVATION_SERVICE',
      'SYSTEM_POLICY', 'REVIEW_SERVICE'
    )
  );

ALTER TABLE memory_patch.audit_events
  DROP CONSTRAINT audit_events_s33_complete_shape;
ALTER TABLE memory_patch.audit_events
  ADD CONSTRAINT audit_events_s33_complete_shape CHECK (
    (
      chain_id IS NULL AND recorded_at IS NULL
      AND sequence_number IS NULL AND idempotency_key IS NULL
      AND draft_hash IS NULL AND subject_type IS NULL
      AND subject_id IS NULL AND subject_hash IS NULL
      AND lineage_hashes IS NULL AND reason_codes IS NULL
      AND step33_envelope IS NULL AND step33_payload IS NULL
      AND step33_append_receipt IS NULL AND step33_entry_hash IS NULL
    )
    OR
    (
      schema_version = 'audit-event-envelope-1.0.0'
      AND recorded_at IS NOT NULL AND chain_id IS NOT NULL
      AND sequence_number >= 1 AND idempotency_key IS NOT NULL
      AND draft_hash ~ '^[0-9a-f]{64}$'
      AND subject_type IN (
        'KERNEL_RUN', 'ROUTE_RESULT', 'POLICY_RESULT',
        'EVIDENCE_BUNDLE', 'TEMPORAL_RESULT', 'DRAFT',
        'CLAIM_ASSESSMENT', 'CORRECTION_PACKET', 'VERIFIED_ANSWER',
        'PERSONAL_MEMORY_SLOT', 'CORRECTION_CANDIDATE',
        'PERSONAL_MEMORY_PROPOSAL', 'PERSONAL_MEMORY_PATCH',
        'PERSONAL_MEMORY_EXPORT', 'PERSONAL_MEMORY_DELETION',
        'SHARED_PROMOTION_PROPOSAL', 'SECURITY_EVENT',
        'REVIEW_CASE', 'REVIEW_DECISION', 'REVIEW_HANDOFF'
      )
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
        'TENANT_SCOPE_DENIED', 'POLICY_BLOCKED',
        'REVIEW_CASE_CREATED', 'REVIEW_CASE_CLAIMED',
        'REVIEW_DECISION_RECORDED', 'REVIEW_HANDOFF_SUCCEEDED',
        'REVIEW_HANDOFF_FAILED', 'REVIEW_CASE_RESOLVED',
        'REVIEW_CASE_ESCALATED'
      )
      AND actor_type IN (
        'HUMAN_USER', 'HUMAN_REVIEWER', 'KERNEL', 'MODEL_ADAPTER',
        'CRITIC_LOOP', 'COMMIT_HELPER', 'ACTIVATION_SERVICE',
        'SYSTEM_POLICY', 'REVIEW_SERVICE', 'MIGRATION_SERVICE'
      )
      AND event_id ~ '^audit-event-[0-9a-f]{64}$'
      AND chain_id ~ '^audit-chain-[0-9a-f]{64}$'
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
      AND metadata ->> 'step33_entry_hash' = step33_entry_hash
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
      AND (step33_envelope ->> 'sequence_number')::INT8 = sequence_number
      AND step33_envelope ->> 'idempotency_key' = idempotency_key
      AND step33_envelope ->> 'draft_hash' = draft_hash
      AND step33_envelope ->> 'subject_type' = subject_type
      AND step33_envelope ->> 'subject_id' = subject_id
      AND step33_envelope ->> 'subject_hash' = subject_hash
      AND step33_envelope ->> 'previous_event_hash' = previous_event_hash
      AND step33_envelope ->> 'event_payload_digest' = payload_hash
      AND (step33_envelope ->> 'occurred_at')::TIMESTAMPTZ = occurred_at
      AND (step33_envelope ->> 'recorded_at')::TIMESTAMPTZ = recorded_at
      AND (step33_envelope ->> 'policy_id') IS NOT DISTINCT FROM policy_id
      AND (step33_envelope ->> 'policy_version')
        IS NOT DISTINCT FROM policy_version
      AND (step33_envelope ->> 'policy_digest')
        IS NOT DISTINCT FROM policy_digest
      AND (step33_envelope ->> 'route_hash') IS NOT DISTINCT FROM route_hash
      AND step33_envelope -> 'lineage_hashes' = lineage_hashes
      AND step33_envelope -> 'reason_codes' = reason_codes
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
      AND step33_append_receipt ->> 'idempotency_key' = idempotency_key
      AND step33_append_receipt ->> 'receipt_hash' ~ '^[0-9a-f]{64}$'
    )
  );

ALTER TABLE memory_patch.reviewer_authorizations OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.human_review_cases OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.human_review_claims OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.human_review_decisions OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.human_review_handoffs OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_reviewer_authorized(
  STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_reviewer_owner_authorized(
  STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_review_service_authorized(STRING)
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_reviewer_case_authorized(
  STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step34_review_service_case_state_matches(
  STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step34_review_append_only()
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step34_review_case_transition()
  OWNER TO mp_schema_owner;

GRANT USAGE ON SCHEMA memory_patch TO mp_human_reviewer, mp_review_service;

REVOKE ALL ON TABLE
  memory_patch.reviewer_authorizations,
  memory_patch.human_review_cases,
  memory_patch.human_review_claims,
  memory_patch.human_review_decisions,
  memory_patch.human_review_handoffs
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper, mp_human_reviewer, mp_review_service;

GRANT SELECT ON TABLE memory_patch.reviewer_authorizations
  TO mp_human_reviewer;
GRANT SELECT, UPDATE ON TABLE memory_patch.human_review_cases
  TO mp_human_reviewer;
GRANT SELECT, INSERT ON TABLE
  memory_patch.human_review_claims,
  memory_patch.human_review_decisions
TO mp_human_reviewer;
GRANT SELECT ON TABLE memory_patch.human_review_handoffs
  TO mp_human_reviewer;

GRANT SELECT, INSERT, UPDATE ON TABLE memory_patch.human_review_cases
  TO mp_review_service;
GRANT SELECT ON TABLE
  memory_patch.human_review_claims,
  memory_patch.human_review_decisions
TO mp_review_service;
GRANT SELECT, INSERT ON TABLE memory_patch.human_review_handoffs
  TO mp_review_service;

GRANT SELECT ON TABLE memory_patch.tenants, memory_patch.users
  TO mp_human_reviewer, mp_review_service;
GRANT SELECT ON TABLE
  memory_patch.kernel_runs,
  memory_patch.personal_memory_spaces
TO mp_human_reviewer, mp_review_service;
GRANT SELECT, INSERT ON TABLE memory_patch.audit_events
  TO mp_human_reviewer, mp_review_service;
GRANT SELECT, INSERT, UPDATE ON TABLE memory_patch.audit_chain_heads
  TO mp_human_reviewer, mp_review_service;

REVOKE ALL ON FUNCTION
  memory_patch.step34_reviewer_authorized(STRING, STRING, STRING),
  memory_patch.step34_reviewer_owner_authorized(STRING, STRING),
  memory_patch.step34_review_service_authorized(STRING),
  memory_patch.step34_reviewer_case_authorized(
    STRING, STRING, STRING, STRING
  ),
  memory_patch.step34_review_service_case_state_matches(
    STRING, STRING, STRING
  ),
  memory_patch.guard_step34_review_append_only(),
  memory_patch.guard_step34_review_case_transition()
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper, mp_human_reviewer, mp_review_service;
GRANT EXECUTE ON FUNCTION
  memory_patch.step34_reviewer_authorized(STRING, STRING, STRING),
  memory_patch.step34_reviewer_owner_authorized(STRING, STRING),
  memory_patch.step34_reviewer_case_authorized(
    STRING, STRING, STRING, STRING
  ),
  memory_patch.guard_step34_review_append_only(),
  memory_patch.guard_step34_review_case_transition()
TO mp_human_reviewer;
GRANT EXECUTE ON FUNCTION
  memory_patch.step34_review_service_authorized(STRING),
  memory_patch.step34_review_service_case_state_matches(
    STRING, STRING, STRING
  ),
  memory_patch.guard_step34_review_append_only(),
  memory_patch.guard_step34_review_case_transition()
TO mp_review_service;

GRANT EXECUTE ON FUNCTION
  memory_patch.guard_step33_audit_append_only(),
  memory_patch.guard_step33_chain_head()
TO mp_human_reviewer, mp_review_service;

GRANT EXECUTE ON FUNCTION
  memory_patch.tenant_context_matches(STRING),
  memory_patch.user_context_matches(STRING, STRING)
TO mp_human_reviewer;
GRANT EXECUTE ON FUNCTION
  memory_patch.tenant_context_matches(STRING)
TO mp_review_service;

ALTER TABLE memory_patch.reviewer_authorizations
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.reviewer_authorizations
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_cases FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_claims FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.human_review_handoffs FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_chain_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.audit_chain_heads FORCE ROW LEVEL SECURITY;

CREATE POLICY reviewer_authorizations_s34_select
  ON memory_patch.reviewer_authorizations
  FOR SELECT TO mp_human_reviewer
  USING (
    active
    AND memory_patch.user_context_matches(tenant_id, reviewer_id)
  );

CREATE POLICY human_review_cases_s34_service_select
  ON memory_patch.human_review_cases
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY human_review_cases_s34_service_insert
  ON memory_patch.human_review_cases
  FOR INSERT TO mp_review_service
  WITH CHECK (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY human_review_cases_s34_service_update
  ON memory_patch.human_review_cases
  FOR UPDATE TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id))
  WITH CHECK (memory_patch.step34_review_service_authorized(tenant_id));

CREATE POLICY human_review_cases_s34_reviewer_select
  ON memory_patch.human_review_cases
  FOR SELECT TO mp_human_reviewer
  USING (
    memory_patch.step34_reviewer_authorized(
      tenant_id, case_type, owner_user_id
    )
  );
CREATE POLICY human_review_cases_s34_reviewer_update
  ON memory_patch.human_review_cases
  FOR UPDATE TO mp_human_reviewer
  USING (
    memory_patch.step34_reviewer_authorized(
      tenant_id, case_type, owner_user_id
    )
  )
  WITH CHECK (
    memory_patch.step34_reviewer_authorized(
      tenant_id, case_type, owner_user_id
    )
  );

CREATE POLICY human_review_claims_s34_reviewer_select
  ON memory_patch.human_review_claims
  FOR SELECT TO mp_human_reviewer
  USING (
    memory_patch.user_context_matches(tenant_id, reviewer_id)
    AND memory_patch.step34_reviewer_case_authorized(
      tenant_id, review_case_id, NULL::STRING, NULL::STRING
    )
  );
CREATE POLICY human_review_claims_s34_reviewer_insert
  ON memory_patch.human_review_claims
  FOR INSERT TO mp_human_reviewer
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, reviewer_id)
    AND memory_patch.step34_reviewer_case_authorized(
      tenant_id, review_case_id, 'OPEN', NULL::STRING
    )
  );
CREATE POLICY human_review_claims_s34_service_select
  ON memory_patch.human_review_claims
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));

CREATE POLICY human_review_decisions_s34_reviewer_select
  ON memory_patch.human_review_decisions
  FOR SELECT TO mp_human_reviewer
  USING (
    memory_patch.user_context_matches(tenant_id, reviewer_id)
    AND memory_patch.step34_reviewer_case_authorized(
      tenant_id, review_case_id, NULL::STRING, NULL::STRING
    )
  );
CREATE POLICY human_review_decisions_s34_reviewer_insert
  ON memory_patch.human_review_decisions
  FOR INSERT TO mp_human_reviewer
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, reviewer_id)
    AND memory_patch.step34_reviewer_case_authorized(
      tenant_id, review_case_id, 'CLAIMED', reviewer_id
    )
  );
CREATE POLICY human_review_decisions_s34_service_select
  ON memory_patch.human_review_decisions
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));

CREATE POLICY human_review_handoffs_s34_reviewer_select
  ON memory_patch.human_review_handoffs
  FOR SELECT TO mp_human_reviewer
  USING (
    memory_patch.step34_reviewer_case_authorized(
      tenant_id, review_case_id, NULL::STRING, NULL::STRING
    )
  );
CREATE POLICY human_review_handoffs_s34_service_select
  ON memory_patch.human_review_handoffs
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY human_review_handoffs_s34_service_insert
  ON memory_patch.human_review_handoffs
  FOR INSERT TO mp_review_service
  WITH CHECK (
    memory_patch.step34_review_service_authorized(tenant_id)
    AND memory_patch.step34_review_service_case_state_matches(
      tenant_id, review_case_id, 'IN_REVIEW'
    )
  );

CREATE POLICY tenants_s34_reviewer_select
  ON memory_patch.tenants
  FOR SELECT TO mp_human_reviewer
  USING (memory_patch.tenant_context_matches(tenant_id));
CREATE POLICY tenants_s34_review_service_select
  ON memory_patch.tenants
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY users_s34_reviewer_identity_select
  ON memory_patch.users
  FOR SELECT TO mp_human_reviewer
  USING (memory_patch.user_context_matches(tenant_id, user_id));
CREATE POLICY users_s34_review_service_select
  ON memory_patch.users
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));

CREATE POLICY kernel_runs_s34_reviewer_lineage_select
  ON memory_patch.kernel_runs
  FOR SELECT TO mp_human_reviewer
  USING (
    memory_patch.step34_reviewer_owner_authorized(tenant_id, user_id)
  );
CREATE POLICY kernel_runs_s34_review_service_lineage_select
  ON memory_patch.kernel_runs
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY personal_memory_spaces_s34_reviewer_lineage_select
  ON memory_patch.personal_memory_spaces
  FOR SELECT TO mp_human_reviewer
  USING (
    memory_patch.step34_reviewer_owner_authorized(tenant_id, user_id)
  );
CREATE POLICY personal_memory_spaces_s34_review_service_lineage_select
  ON memory_patch.personal_memory_spaces
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));

CREATE POLICY audit_events_s34_reviewer_select
  ON memory_patch.audit_events
  FOR SELECT TO mp_human_reviewer
  USING (
    event_type IN (
      'REVIEW_CASE_CREATED', 'REVIEW_CASE_CLAIMED',
      'REVIEW_DECISION_RECORDED', 'REVIEW_HANDOFF_SUCCEEDED',
      'REVIEW_HANDOFF_FAILED', 'REVIEW_CASE_RESOLVED',
      'REVIEW_CASE_ESCALATED'
    )
    AND user_id IS NOT NULL
    AND memory_patch.step34_reviewer_authorized(
      tenant_id, step33_payload ->> 'case_type', user_id
    )
  );
CREATE POLICY audit_events_s34_reviewer_insert
  ON memory_patch.audit_events
  FOR INSERT TO mp_human_reviewer
  WITH CHECK (
    event_type IN ('REVIEW_CASE_CLAIMED', 'REVIEW_DECISION_RECORDED')
    AND actor_type = 'HUMAN_REVIEWER'
    AND user_id IS NOT NULL
    AND memory_patch.step34_reviewer_authorized(
      tenant_id, step33_payload ->> 'case_type', user_id
    )
  );
CREATE POLICY audit_events_s34_review_service_select
  ON memory_patch.audit_events
  FOR SELECT TO mp_review_service
  USING (
    event_type IN (
      'REVIEW_CASE_CREATED', 'REVIEW_CASE_CLAIMED',
      'REVIEW_DECISION_RECORDED', 'REVIEW_HANDOFF_SUCCEEDED',
      'REVIEW_HANDOFF_FAILED', 'REVIEW_CASE_RESOLVED',
      'REVIEW_CASE_ESCALATED'
    )
    AND memory_patch.step34_review_service_authorized(tenant_id)
  );
CREATE POLICY audit_events_s34_review_service_insert
  ON memory_patch.audit_events
  FOR INSERT TO mp_review_service
  WITH CHECK (
    event_type IN (
      'REVIEW_CASE_CREATED', 'REVIEW_HANDOFF_SUCCEEDED',
      'REVIEW_HANDOFF_FAILED', 'REVIEW_CASE_RESOLVED',
      'REVIEW_CASE_ESCALATED'
    )
    AND memory_patch.step34_review_service_authorized(tenant_id)
  );

CREATE POLICY audit_chain_heads_s34_reviewer_select
  ON memory_patch.audit_chain_heads
  FOR SELECT TO mp_human_reviewer
  USING (
    owner_user_id IS NOT NULL
    AND memory_patch.step34_reviewer_owner_authorized(
      tenant_id, owner_user_id
    )
  );
CREATE POLICY audit_chain_heads_s34_reviewer_insert
  ON memory_patch.audit_chain_heads
  FOR INSERT TO mp_human_reviewer
  WITH CHECK (
    owner_user_id IS NOT NULL
    AND memory_patch.step34_reviewer_owner_authorized(
      tenant_id, owner_user_id
    )
  );
CREATE POLICY audit_chain_heads_s34_reviewer_update
  ON memory_patch.audit_chain_heads
  FOR UPDATE TO mp_human_reviewer
  USING (
    owner_user_id IS NOT NULL
    AND memory_patch.step34_reviewer_owner_authorized(
      tenant_id, owner_user_id
    )
  )
  WITH CHECK (
    owner_user_id IS NOT NULL
    AND memory_patch.step34_reviewer_owner_authorized(
      tenant_id, owner_user_id
    )
  );
CREATE POLICY audit_chain_heads_s34_review_service_select
  ON memory_patch.audit_chain_heads
  FOR SELECT TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY audit_chain_heads_s34_review_service_insert
  ON memory_patch.audit_chain_heads
  FOR INSERT TO mp_review_service
  WITH CHECK (memory_patch.step34_review_service_authorized(tenant_id));
CREATE POLICY audit_chain_heads_s34_review_service_update
  ON memory_patch.audit_chain_heads
  FOR UPDATE TO mp_review_service
  USING (memory_patch.step34_review_service_authorized(tenant_id))
  WITH CHECK (memory_patch.step34_review_service_authorized(tenant_id));

COMMENT ON TABLE memory_patch.human_review_cases IS
  'Step 34 bounded review cases; review is neither evidence nor execution authority.';
COMMENT ON TABLE memory_patch.reviewer_authorizations IS
  'Step 34 schema-owner provisioned least-privilege reviewer grants.';
