-- Memory Patch Step 4 — durable Kernel, memory, approval-claim, and audit facts.
-- Stored decisions and authority claims are evidence only. This migration
-- creates no roles, policies, triggers, executors, or automatic transitions.

CREATE TABLE memory_patch.kernel_runs (
  tenant_id STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  user_id STRING NOT NULL,
  personal_memory_space_id STRING,
  model_binding_id STRING NOT NULL,
  request_sha256 STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, kernel_run_id),
  CONSTRAINT kernel_runs_user_fk
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT kernel_runs_personal_space_fk
    FOREIGN KEY (tenant_id, user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id,
      user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT kernel_runs_id_not_blank
    CHECK (btrim(kernel_run_id) <> ''),
  CONSTRAINT kernel_runs_model_binding_not_blank
    CHECK (btrim(model_binding_id) <> ''),
  CONSTRAINT kernel_runs_request_sha256
    CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT kernel_runs_time_order
    CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE memory_patch.routing_decisions (
  tenant_id STRING NOT NULL,
  routing_decision_id STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  knowledge_route STRING NOT NULL,
  selected_hat_scope_id STRING,
  selected_hat_id STRING,
  reason_codes JSONB NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, routing_decision_id),
  UNIQUE (tenant_id, kernel_run_id),
  CONSTRAINT routing_decisions_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT routing_decisions_selected_hat_fk
    FOREIGN KEY (tenant_id, selected_hat_scope_id, selected_hat_id)
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      knowledge_hat_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT routing_decisions_id_not_blank
    CHECK (btrim(routing_decision_id) <> ''),
  CONSTRAINT routing_decisions_route
    CHECK (
      knowledge_route IN (
        'PASS_THROUGH',
        'HAT_ASSIST',
        'HAT_ENFORCE',
        'AMBIGUOUS'
      )
    ),
  CONSTRAINT routing_decisions_selected_hat
    CHECK (
      (
        knowledge_route IN ('HAT_ASSIST', 'HAT_ENFORCE')
        AND selected_hat_scope_id IS NOT NULL
        AND btrim(selected_hat_scope_id) <> ''
        AND selected_hat_id IS NOT NULL
        AND btrim(selected_hat_id) <> ''
      )
      OR
      (
        knowledge_route IN ('PASS_THROUGH', 'AMBIGUOUS')
        AND selected_hat_scope_id IS NULL
        AND selected_hat_id IS NULL
      )
    ),
  CONSTRAINT routing_decisions_reason_codes
    CHECK (
      jsonb_typeof(reason_codes) = 'array'
      AND jsonb_array_length(reason_codes) > 0
    )
);

CREATE TABLE memory_patch.action_policy_decisions (
  tenant_id STRING NOT NULL,
  action_policy_decision_id STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  action_policy STRING NOT NULL,
  reason_codes JSONB NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, action_policy_decision_id),
  UNIQUE (tenant_id, kernel_run_id),
  CONSTRAINT action_policy_decisions_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT action_policy_decisions_id_not_blank
    CHECK (btrim(action_policy_decision_id) <> ''),
  CONSTRAINT action_policy_decisions_policy
    CHECK (
      action_policy IN (
        'ALLOW',
        'DENY_ACTION',
        'REQUIRE_CONFIRMATION'
      )
    ),
  CONSTRAINT action_policy_decisions_reason_codes
    CHECK (
      jsonb_typeof(reason_codes) = 'array'
      AND jsonb_array_length(reason_codes) > 0
    )
);

CREATE TABLE memory_patch.drafts (
  tenant_id STRING NOT NULL,
  draft_id STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  draft_stage INT8 NOT NULL,
  content_sha256 STRING NOT NULL,
  immutable_content_reference STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, draft_id),
  UNIQUE (tenant_id, kernel_run_id, draft_stage),
  CONSTRAINT drafts_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT drafts_id_not_blank CHECK (btrim(draft_id) <> ''),
  CONSTRAINT drafts_stage CHECK (draft_stage IN (1, 2)),
  CONSTRAINT drafts_content_sha256
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT drafts_reference_not_blank
    CHECK (btrim(immutable_content_reference) <> '')
);

CREATE TABLE memory_patch.claims (
  tenant_id STRING NOT NULL,
  claim_id STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  draft_id STRING NOT NULL,
  statement STRING NOT NULL,
  claim_category STRING NOT NULL,
  scope_dimensions JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, claim_id),
  CONSTRAINT claims_draft_fk
    FOREIGN KEY (tenant_id, draft_id)
    REFERENCES memory_patch.drafts (tenant_id, draft_id)
    ON DELETE RESTRICT,
  CONSTRAINT claims_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT claims_id_not_blank CHECK (btrim(claim_id) <> ''),
  CONSTRAINT claims_statement_not_blank CHECK (btrim(statement) <> ''),
  CONSTRAINT claims_category_not_blank CHECK (btrim(claim_category) <> ''),
  CONSTRAINT claims_scope_dimensions_array
    CHECK (jsonb_typeof(scope_dimensions) = 'array')
);

CREATE TABLE memory_patch.evidence_items (
  tenant_id STRING NOT NULL,
  evidence_id STRING NOT NULL,
  source_id STRING NOT NULL,
  knowledge_version_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  citation_reference STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  trust_class STRING NOT NULL,
  authority_rank INT8 NOT NULL,
  scope_dimensions JSONB NOT NULL,
  metadata JSONB NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  valid_from TIMESTAMPTZ,
  valid_until TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, evidence_id),
  CONSTRAINT evidence_items_version_fk
    FOREIGN KEY (
      tenant_id,
      knowledge_version_id,
      source_id,
      hat_scope_id
    )
    REFERENCES memory_patch.knowledge_versions (
      tenant_id,
      knowledge_version_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT evidence_items_id_not_blank
    CHECK (btrim(evidence_id) <> ''),
  CONSTRAINT evidence_items_citation_not_blank
    CHECK (btrim(citation_reference) <> ''),
  CONSTRAINT evidence_items_content_sha256
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT evidence_items_trust_class
    CHECK (
      trust_class IN (
        'CANONICAL_SOURCE_EVIDENCE',
        'SHARED_HAT_VERIFIED_MEMORY'
      )
    ),
  CONSTRAINT evidence_items_authority_rank
    CHECK (authority_rank >= 0),
  CONSTRAINT evidence_items_scope_dimensions_array
    CHECK (jsonb_typeof(scope_dimensions) = 'array'),
  CONSTRAINT evidence_items_metadata_object
    CHECK (jsonb_typeof(metadata) = 'object'),
  CONSTRAINT evidence_items_validity
    CHECK (
      valid_from IS NULL
      OR valid_until IS NULL
      OR valid_from <= valid_until
    )
);

CREATE TABLE memory_patch.evidence_bundles (
  tenant_id STRING NOT NULL,
  evidence_bundle_id STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  hat_id STRING NOT NULL,
  evidence_status STRING NOT NULL,
  retrieval_policy_version STRING NOT NULL,
  bundle_hash STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, evidence_bundle_id),
  UNIQUE (tenant_id, evidence_bundle_id, kernel_run_id),
  CONSTRAINT evidence_bundles_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT evidence_bundles_scope_fk
    FOREIGN KEY (tenant_id, hat_scope_id, hat_id)
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      knowledge_hat_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT evidence_bundles_id_not_blank
    CHECK (btrim(evidence_bundle_id) <> ''),
  CONSTRAINT evidence_bundles_status
    CHECK (
      evidence_status IN (
        'NOT_REQUIRED',
        'SUFFICIENT',
        'INSUFFICIENT',
        'CONFLICTING',
        'UNAVAILABLE',
        'STALE',
        'INVALID'
      )
    ),
  CONSTRAINT evidence_bundles_policy_not_blank
    CHECK (btrim(retrieval_policy_version) <> ''),
  CONSTRAINT evidence_bundles_hash
    CHECK (bundle_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE memory_patch.evidence_bundle_items (
  tenant_id STRING NOT NULL,
  evidence_bundle_id STRING NOT NULL,
  evidence_id STRING NOT NULL,
  item_ordinal INT8 NOT NULL,
  PRIMARY KEY (tenant_id, evidence_bundle_id, evidence_id),
  UNIQUE (tenant_id, evidence_bundle_id, item_ordinal),
  CONSTRAINT evidence_bundle_items_bundle_fk
    FOREIGN KEY (tenant_id, evidence_bundle_id)
    REFERENCES memory_patch.evidence_bundles (
      tenant_id,
      evidence_bundle_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT evidence_bundle_items_evidence_fk
    FOREIGN KEY (tenant_id, evidence_id)
    REFERENCES memory_patch.evidence_items (tenant_id, evidence_id)
    ON DELETE RESTRICT,
  CONSTRAINT evidence_bundle_items_ordinal
    CHECK (item_ordinal >= 0)
);

CREATE TABLE memory_patch.claim_verdicts (
  tenant_id STRING NOT NULL,
  claim_id STRING NOT NULL,
  verdict STRING NOT NULL,
  evidence_references JSONB NOT NULL,
  verifier_id STRING NOT NULL,
  explanation_code STRING NOT NULL,
  verified_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, claim_id),
  CONSTRAINT claim_verdicts_claim_fk
    FOREIGN KEY (tenant_id, claim_id)
    REFERENCES memory_patch.claims (tenant_id, claim_id)
    ON DELETE RESTRICT,
  CONSTRAINT claim_verdicts_verdict
    CHECK (
      verdict IN (
        'SUPPORTED',
        'CONTRADICTED',
        'INSUFFICIENT',
        'OUT_OF_SCOPE',
        'STALE'
      )
    ),
  CONSTRAINT claim_verdicts_evidence_array
    CHECK (jsonb_typeof(evidence_references) = 'array'),
  CONSTRAINT claim_verdicts_supported_evidence
    CHECK (
      verdict <> 'SUPPORTED'
      OR jsonb_array_length(evidence_references) > 0
    ),
  CONSTRAINT claim_verdicts_verifier_not_blank
    CHECK (btrim(verifier_id) <> ''),
  CONSTRAINT claim_verdicts_explanation_not_blank
    CHECK (btrim(explanation_code) <> '')
);

CREATE TABLE memory_patch.correction_packets (
  tenant_id STRING NOT NULL,
  packet_hash STRING NOT NULL,
  schema_version STRING NOT NULL,
  kernel_run_id STRING NOT NULL,
  draft_v1_id STRING NOT NULL,
  selected_hat_scope_id STRING NOT NULL,
  selected_hat_id STRING NOT NULL,
  knowledge_route STRING NOT NULL,
  action_policy STRING NOT NULL,
  evidence_status STRING NOT NULL,
  packet_payload JSONB NOT NULL,
  uncertainty DECIMAL NOT NULL,
  retrieval_policy_version STRING NOT NULL,
  embedding_model_version STRING,
  persisted_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, packet_hash),
  CONSTRAINT correction_packets_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT correction_packets_draft_fk
    FOREIGN KEY (tenant_id, draft_v1_id)
    REFERENCES memory_patch.drafts (tenant_id, draft_id)
    ON DELETE RESTRICT,
  CONSTRAINT correction_packets_scope_fk
    FOREIGN KEY (tenant_id, selected_hat_scope_id, selected_hat_id)
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      knowledge_hat_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT correction_packets_hash
    CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT correction_packets_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT correction_packets_route
    CHECK (knowledge_route IN ('HAT_ASSIST', 'HAT_ENFORCE')),
  CONSTRAINT correction_packets_action_policy
    CHECK (
      action_policy IN (
        'ALLOW',
        'DENY_ACTION',
        'REQUIRE_CONFIRMATION'
      )
    ),
  CONSTRAINT correction_packets_evidence_status
    CHECK (
      evidence_status IN (
        'NOT_REQUIRED',
        'SUFFICIENT',
        'INSUFFICIENT',
        'CONFLICTING',
        'UNAVAILABLE',
        'STALE',
        'INVALID'
      )
    ),
  CONSTRAINT correction_packets_payload_object
    CHECK (jsonb_typeof(packet_payload) = 'object'),
  CONSTRAINT correction_packets_uncertainty
    CHECK (uncertainty >= 0 AND uncertainty <= 1),
  CONSTRAINT correction_packets_policy_not_blank
    CHECK (btrim(retrieval_policy_version) <> ''),
  CONSTRAINT correction_packets_embedding_model_not_blank
    CHECK (
      embedding_model_version IS NULL
      OR btrim(embedding_model_version) <> ''
    )
);

CREATE TABLE memory_patch.correction_requirements (
  tenant_id STRING NOT NULL,
  packet_hash STRING NOT NULL,
  requirement_id STRING NOT NULL,
  claim_id STRING NOT NULL,
  instruction STRING NOT NULL,
  evidence_references JSONB NOT NULL,
  mandatory BOOL NOT NULL,
  PRIMARY KEY (tenant_id, packet_hash, requirement_id),
  CONSTRAINT correction_requirements_packet_fk
    FOREIGN KEY (tenant_id, packet_hash)
    REFERENCES memory_patch.correction_packets (tenant_id, packet_hash)
    ON DELETE RESTRICT,
  CONSTRAINT correction_requirements_claim_fk
    FOREIGN KEY (tenant_id, claim_id)
    REFERENCES memory_patch.claims (tenant_id, claim_id)
    ON DELETE RESTRICT,
  CONSTRAINT correction_requirements_id_not_blank
    CHECK (btrim(requirement_id) <> ''),
  CONSTRAINT correction_requirements_instruction_not_blank
    CHECK (btrim(instruction) <> ''),
  CONSTRAINT correction_requirements_evidence_array
    CHECK (jsonb_typeof(evidence_references) = 'array'),
  CONSTRAINT correction_requirements_mandatory_evidence
    CHECK (
      mandatory = false
      OR jsonb_array_length(evidence_references) > 0
    )
);

CREATE TABLE memory_patch.memory_patch_proposals (
  tenant_id STRING NOT NULL,
  proposal_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  target_scope STRING NOT NULL,
  target_hat_id STRING,
  owner_user_id STRING,
  personal_memory_space_id STRING,
  origin STRING NOT NULL,
  proposed_content JSONB NOT NULL,
  evidence_references JSONB NOT NULL,
  scope_dimensions JSONB NOT NULL,
  valid_from TIMESTAMPTZ,
  valid_until TIMESTAMPTZ,
  requested_trust_class STRING NOT NULL,
  approval_requirement STRING NOT NULL,
  lifecycle_state STRING NOT NULL,
  content_kind STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  content_hash STRING NOT NULL,
  PRIMARY KEY (tenant_id, proposal_id),
  UNIQUE (tenant_id, proposal_id, content_hash),
  UNIQUE (tenant_id, proposal_id, content_hash, target_scope),
  UNIQUE (
    tenant_id,
    proposal_id,
    content_hash,
    target_scope,
    owner_user_id,
    personal_memory_space_id
  ),
  CONSTRAINT memory_patch_proposals_scope_fk
    FOREIGN KEY (tenant_id, hat_scope_id, target_scope)
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      target_scope
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_proposals_personal_scope_fk
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
  CONSTRAINT memory_patch_proposals_shared_scope_fk
    FOREIGN KEY (
      tenant_id,
      hat_scope_id,
      target_scope,
      target_hat_id
    )
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      target_scope,
      knowledge_hat_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_proposals_id_not_blank
    CHECK (btrim(proposal_id) <> ''),
  CONSTRAINT memory_patch_proposals_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT memory_patch_proposals_exact_scope
    CHECK (
      (
        target_scope = 'SHARED_KNOWLEDGE_HAT'
        AND target_hat_id IS NOT NULL
        AND btrim(target_hat_id) <> ''
        AND owner_user_id IS NULL
        AND personal_memory_space_id IS NULL
        AND requested_trust_class = 'SHARED_HAT_VERIFIED_MEMORY'
        AND approval_requirement = 'DOMAIN_REVIEWER'
      )
      OR
      (
        target_scope = 'USER_PERSONAL_HAT'
        AND target_hat_id IS NULL
        AND owner_user_id IS NOT NULL
        AND btrim(owner_user_id) <> ''
        AND personal_memory_space_id IS NOT NULL
        AND btrim(personal_memory_space_id) <> ''
        AND requested_trust_class <> 'SHARED_HAT_VERIFIED_MEMORY'
        AND approval_requirement = 'OWNER'
      )
    ),
  CONSTRAINT memory_patch_proposals_origin
    CHECK (
      origin IN (
        'KNOWLEDGE_HUB',
        'CRITIC_PROMPT_LOOP',
        'MODEL_VERIFIER',
        'USER_ENTRY',
        'USER_DOCUMENT',
        'HUMAN_REVIEW',
        'SYSTEM_MIGRATION'
      )
    ),
  CONSTRAINT memory_patch_proposals_content_object
    CHECK (
      jsonb_typeof(proposed_content) IN (
        'object',
        'array',
        'string',
        'number',
        'boolean',
        'null'
      )
    ),
  CONSTRAINT memory_patch_proposals_evidence_array
    CHECK (jsonb_typeof(evidence_references) = 'array'),
  CONSTRAINT memory_patch_proposals_scope_dimensions_array
    CHECK (jsonb_typeof(scope_dimensions) = 'array'),
  CONSTRAINT memory_patch_proposals_validity
    CHECK (
      valid_from IS NULL
      OR valid_until IS NULL
      OR valid_from <= valid_until
    ),
  CONSTRAINT memory_patch_proposals_trust_class
    CHECK (
      requested_trust_class IN (
        'SHARED_HAT_VERIFIED_MEMORY',
        'PERSONAL_VERIFIED_PATCH',
        'USER_ASSERTED_MEMORY',
        'MODEL_EXPERIENCE_HINT'
      )
    ),
  CONSTRAINT memory_patch_proposals_lifecycle_state
    CHECK (
      lifecycle_state IN (
        'DETECTED',
        'PROPOSED',
        'EVIDENCE_BOUND',
        'VALIDATED',
        'AWAITING_APPROVAL',
        'APPROVED',
        'COMMITTED',
        'ACTIVE',
        'SUPERSEDED',
        'REJECTED',
        'REVOKED'
      )
    ),
  CONSTRAINT memory_patch_proposals_content_kind
    CHECK (
      content_kind IN (
        'FACTUAL',
        'PREFERENCE',
        'WORKFLOW',
        'MODEL_EXPERIENCE'
      )
    ),
  CONSTRAINT memory_patch_proposals_model_experience_trust
    CHECK (
      content_kind <> 'MODEL_EXPERIENCE'
      OR requested_trust_class = 'MODEL_EXPERIENCE_HINT'
    ),
  CONSTRAINT memory_patch_proposals_content_hash
    CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE memory_patch.memory_patch_approvals (
  tenant_id STRING NOT NULL,
  approval_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  proposal_id STRING NOT NULL,
  proposal_content_hash STRING NOT NULL,
  target_scope STRING NOT NULL,
  owner_user_id STRING,
  personal_memory_space_id STRING,
  decision STRING NOT NULL,
  approver_type STRING NOT NULL,
  approver_id STRING NOT NULL,
  reason_code STRING NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL,
  approval_proof STRING NOT NULL,
  PRIMARY KEY (tenant_id, approval_id),
  UNIQUE (
    tenant_id,
    approval_id,
    proposal_id,
    proposal_content_hash,
    target_scope,
    approval_proof,
    decision
  ),
  UNIQUE (
    tenant_id,
    approval_id,
    proposal_id,
    proposal_content_hash,
    target_scope,
    approval_proof,
    decision,
    owner_user_id,
    personal_memory_space_id
  ),
  CONSTRAINT memory_patch_approvals_proposal_fk
    FOREIGN KEY (
      tenant_id,
      proposal_id,
      proposal_content_hash,
      target_scope
    )
    REFERENCES memory_patch.memory_patch_proposals (
      tenant_id,
      proposal_id,
      content_hash,
      target_scope
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_approvals_personal_proposal_fk
    FOREIGN KEY (
      tenant_id,
      proposal_id,
      proposal_content_hash,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    )
    REFERENCES memory_patch.memory_patch_proposals (
      tenant_id,
      proposal_id,
      content_hash,
      target_scope,
      owner_user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_approvals_id_not_blank
    CHECK (btrim(approval_id) <> ''),
  CONSTRAINT memory_patch_approvals_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT memory_patch_approvals_exact_scope
    CHECK (
      (
        target_scope = 'SHARED_KNOWLEDGE_HAT'
        AND owner_user_id IS NULL
        AND personal_memory_space_id IS NULL
      )
      OR
      (
        target_scope = 'USER_PERSONAL_HAT'
        AND
        owner_user_id IS NOT NULL
        AND btrim(owner_user_id) <> ''
        AND personal_memory_space_id IS NOT NULL
        AND btrim(personal_memory_space_id) <> ''
      )
    ),
  CONSTRAINT memory_patch_approvals_decision
    CHECK (decision IN ('APPROVE', 'REJECT')),
  CONSTRAINT memory_patch_approvals_human_claim_only
    CHECK (approver_type IN ('USER', 'HUMAN_REVIEWER')),
  CONSTRAINT memory_patch_approvals_approver_not_blank
    CHECK (btrim(approver_id) <> ''),
  CONSTRAINT memory_patch_approvals_reason_not_blank
    CHECK (btrim(reason_code) <> ''),
  CONSTRAINT memory_patch_approvals_proof
    CHECK (approval_proof ~ '^[0-9a-f]{64}$')
);

CREATE TABLE memory_patch.memory_patch_commits (
  tenant_id STRING NOT NULL,
  commit_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  proposal_id STRING NOT NULL,
  proposal_content_hash STRING NOT NULL,
  target_scope STRING NOT NULL,
  approval_id STRING NOT NULL,
  approval_proof STRING NOT NULL,
  approval_decision STRING NOT NULL,
  committed_patch_id STRING NOT NULL,
  owner_user_id STRING,
  personal_memory_space_id STRING,
  actor_type STRING NOT NULL,
  actor_id STRING NOT NULL,
  storage_class STRING NOT NULL,
  committed_at TIMESTAMPTZ NOT NULL,
  commit_hash STRING NOT NULL,
  PRIMARY KEY (tenant_id, commit_id),
  UNIQUE (tenant_id, committed_patch_id),
  CONSTRAINT memory_patch_commits_proposal_fk
    FOREIGN KEY (
      tenant_id,
      proposal_id,
      proposal_content_hash,
      target_scope
    )
    REFERENCES memory_patch.memory_patch_proposals (
      tenant_id,
      proposal_id,
      content_hash,
      target_scope
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_commits_approval_fk
    FOREIGN KEY (
      tenant_id,
      approval_id,
      proposal_id,
      proposal_content_hash,
      target_scope,
      approval_proof,
      approval_decision
    )
    REFERENCES memory_patch.memory_patch_approvals (
      tenant_id,
      approval_id,
      proposal_id,
      proposal_content_hash,
      target_scope,
      approval_proof,
      decision
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_commits_personal_approval_fk
    FOREIGN KEY (
      tenant_id,
      approval_id,
      proposal_id,
      proposal_content_hash,
      target_scope,
      approval_proof,
      approval_decision,
      owner_user_id,
      personal_memory_space_id
    )
    REFERENCES memory_patch.memory_patch_approvals (
      tenant_id,
      approval_id,
      proposal_id,
      proposal_content_hash,
      target_scope,
      approval_proof,
      decision,
      owner_user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_patch_commits_id_not_blank
    CHECK (btrim(commit_id) <> ''),
  CONSTRAINT memory_patch_commits_patch_id_not_blank
    CHECK (btrim(committed_patch_id) <> ''),
  CONSTRAINT memory_patch_commits_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT memory_patch_commits_approval_decision
    CHECK (approval_decision = 'APPROVE'),
  CONSTRAINT memory_patch_commits_exact_scope
    CHECK (
      (
        target_scope = 'SHARED_KNOWLEDGE_HAT'
        AND owner_user_id IS NULL
        AND personal_memory_space_id IS NULL
      )
      OR
      (
        target_scope = 'USER_PERSONAL_HAT'
        AND
        owner_user_id IS NOT NULL
        AND btrim(owner_user_id) <> ''
        AND personal_memory_space_id IS NOT NULL
        AND btrim(personal_memory_space_id) <> ''
      )
    ),
  CONSTRAINT memory_patch_commits_actor
    CHECK (actor_type IN ('COMMIT_SERVICE', 'MIGRATION_SERVICE')),
  CONSTRAINT memory_patch_commits_actor_id_not_blank
    CHECK (btrim(actor_id) <> ''),
  CONSTRAINT memory_patch_commits_storage_class
    CHECK (storage_class = 'CRDB_TRANSACTIONAL'),
  CONSTRAINT memory_patch_commits_hash
    CHECK (commit_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE memory_patch.patch_transition_records (
  tenant_id STRING NOT NULL,
  transition_id STRING NOT NULL,
  proposal_id STRING NOT NULL,
  proposal_content_hash STRING NOT NULL,
  state_before STRING NOT NULL,
  state_after STRING NOT NULL,
  actor_type STRING NOT NULL,
  actor_id STRING NOT NULL,
  transitioned_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, transition_id),
  CONSTRAINT patch_transition_records_proposal_fk
    FOREIGN KEY (tenant_id, proposal_id, proposal_content_hash)
    REFERENCES memory_patch.memory_patch_proposals (
      tenant_id,
      proposal_id,
      content_hash
    )
    ON DELETE RESTRICT,
  CONSTRAINT patch_transition_records_id_not_blank
    CHECK (btrim(transition_id) <> ''),
  CONSTRAINT patch_transition_records_actor_type
    CHECK (
      actor_type IN (
        'USER',
        'HUMAN_REVIEWER',
        'SYSTEM',
        'COMMIT_SERVICE',
        'KNOWLEDGE_KERNEL',
        'KNOWLEDGE_HAT',
        'KNOWLEDGE_HUB',
        'CRITIC_PROMPT_LOOP',
        'MODEL',
        'MODEL_VERIFIER',
        'MIGRATION_SERVICE'
      )
    ),
  CONSTRAINT patch_transition_records_actor_not_blank
    CHECK (btrim(actor_id) <> ''),
  CONSTRAINT patch_transition_records_allowed_edge
    CHECK (
      (state_before = 'DETECTED' AND state_after = 'PROPOSED')
      OR
      (state_before = 'PROPOSED' AND state_after = 'EVIDENCE_BOUND')
      OR
      (state_before = 'EVIDENCE_BOUND' AND state_after = 'VALIDATED')
      OR
      (state_before = 'VALIDATED' AND state_after = 'AWAITING_APPROVAL')
      OR
      (
        state_before = 'AWAITING_APPROVAL'
        AND state_after IN ('APPROVED', 'REJECTED')
      )
      OR
      (state_before = 'APPROVED' AND state_after = 'COMMITTED')
      OR
      (state_before = 'COMMITTED' AND state_after = 'ACTIVE')
      OR
      (
        state_before = 'ACTIVE'
        AND state_after IN ('SUPERSEDED', 'REVOKED')
      )
    )
);

CREATE TABLE memory_patch.memory_items (
  tenant_id STRING NOT NULL,
  memory_item_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  target_scope STRING NOT NULL,
  visibility STRING NOT NULL,
  trust_class STRING NOT NULL,
  content_kind STRING NOT NULL,
  content JSONB NOT NULL,
  scope_dimensions JSONB NOT NULL,
  evidence_references JSONB NOT NULL,
  source_patch_id STRING,
  valid_from TIMESTAMPTZ,
  valid_until TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  active BOOL NOT NULL,
  revoked BOOL NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, memory_item_id),
  CONSTRAINT memory_items_scope_fk
    FOREIGN KEY (tenant_id, hat_scope_id, target_scope)
    REFERENCES memory_patch.hat_scopes (
      tenant_id,
      hat_scope_id,
      target_scope
    )
    ON DELETE RESTRICT,
  CONSTRAINT memory_items_id_not_blank
    CHECK (btrim(memory_item_id) <> ''),
  CONSTRAINT memory_items_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT memory_items_visibility_scope
    CHECK (
      (
        visibility = 'SHARED'
        AND target_scope = 'SHARED_KNOWLEDGE_HAT'
      )
      OR
      (
        visibility = 'PERSONAL'
        AND target_scope = 'USER_PERSONAL_HAT'
      )
    ),
  CONSTRAINT memory_items_trust_class
    CHECK (
      trust_class IN (
        'SHARED_HAT_VERIFIED_MEMORY',
        'PERSONAL_VERIFIED_PATCH',
        'USER_ASSERTED_MEMORY',
        'MODEL_EXPERIENCE_HINT'
      )
    ),
  CONSTRAINT memory_items_content_kind
    CHECK (
      content_kind IN (
        'FACTUAL',
        'PREFERENCE',
        'WORKFLOW',
        'MODEL_EXPERIENCE'
      )
    ),
  CONSTRAINT memory_items_content_json
    CHECK (
      jsonb_typeof(content) IN (
        'object',
        'array',
        'string',
        'number',
        'boolean',
        'null'
      )
    ),
  CONSTRAINT memory_items_scope_dimensions_array
    CHECK (jsonb_typeof(scope_dimensions) = 'array'),
  CONSTRAINT memory_items_evidence_array
    CHECK (jsonb_typeof(evidence_references) = 'array'),
  CONSTRAINT memory_items_verified_source_patch
    CHECK (
      trust_class NOT IN (
        'SHARED_HAT_VERIFIED_MEMORY',
        'PERSONAL_VERIFIED_PATCH'
      )
      OR
      (
        source_patch_id IS NOT NULL
        AND btrim(source_patch_id) <> ''
      )
    ),
  CONSTRAINT memory_items_verified_inert
    CHECK (
      active = false
      OR trust_class NOT IN (
        'SHARED_HAT_VERIFIED_MEMORY',
        'PERSONAL_VERIFIED_PATCH'
      )
    ),
  CONSTRAINT memory_items_model_experience_trust
    CHECK (
      content_kind <> 'MODEL_EXPERIENCE'
      OR trust_class = 'MODEL_EXPERIENCE_HINT'
    ),
  CONSTRAINT memory_items_validity
    CHECK (
      valid_from IS NULL
      OR valid_until IS NULL
      OR valid_from <= valid_until
    ),
  CONSTRAINT memory_items_expiry
    CHECK (expires_at IS NULL OR expires_at > created_at),
  CONSTRAINT memory_items_revocation
    CHECK (revoked = false OR active = false)
);

CREATE INDEX memory_items_scope_retrieval_idx
  ON memory_patch.memory_items (
    tenant_id,
    hat_scope_id,
    active,
    revoked,
    trust_class
  );

CREATE TABLE memory_patch.audit_events (
  tenant_id STRING NOT NULL,
  event_id STRING NOT NULL,
  schema_version STRING NOT NULL,
  event_type STRING NOT NULL,
  actor_type STRING NOT NULL,
  actor_id STRING NOT NULL,
  kernel_run_id STRING,
  user_id STRING,
  personal_memory_space_id STRING,
  payload_hash STRING NOT NULL,
  previous_event_hash STRING,
  event_hash STRING NOT NULL,
  metadata JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, event_id),
  CONSTRAINT audit_events_tenant_fk
    FOREIGN KEY (tenant_id)
    REFERENCES memory_patch.tenants (tenant_id)
    ON DELETE RESTRICT,
  CONSTRAINT audit_events_run_fk
    FOREIGN KEY (tenant_id, kernel_run_id)
    REFERENCES memory_patch.kernel_runs (tenant_id, kernel_run_id)
    ON DELETE RESTRICT,
  CONSTRAINT audit_events_personal_space_fk
    FOREIGN KEY (tenant_id, user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id,
      user_id,
      personal_memory_space_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT audit_events_id_not_blank CHECK (btrim(event_id) <> ''),
  CONSTRAINT audit_events_schema_version
    CHECK (schema_version = '1.0.0'),
  CONSTRAINT audit_events_type_not_blank CHECK (btrim(event_type) <> ''),
  CONSTRAINT audit_events_actor_type
    CHECK (
      actor_type IN (
        'USER',
        'HUMAN_REVIEWER',
        'SYSTEM',
        'COMMIT_SERVICE',
        'KNOWLEDGE_KERNEL',
        'KNOWLEDGE_HAT',
        'KNOWLEDGE_HUB',
        'CRITIC_PROMPT_LOOP',
        'MODEL',
        'MODEL_VERIFIER',
        'MIGRATION_SERVICE'
      )
    ),
  CONSTRAINT audit_events_actor_not_blank CHECK (btrim(actor_id) <> ''),
  CONSTRAINT audit_events_scope_pair
    CHECK (
      (user_id IS NULL AND personal_memory_space_id IS NULL)
      OR
      (
        user_id IS NOT NULL
        AND btrim(user_id) <> ''
        AND personal_memory_space_id IS NOT NULL
        AND btrim(personal_memory_space_id) <> ''
      )
    ),
  CONSTRAINT audit_events_payload_hash
    CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT audit_events_previous_hash
    CHECK (
      previous_event_hash IS NULL
      OR previous_event_hash ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT audit_events_event_hash
    CHECK (event_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT audit_events_metadata_object
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX audit_events_tenant_time_idx
  ON memory_patch.audit_events (tenant_id, occurred_at, event_id);
