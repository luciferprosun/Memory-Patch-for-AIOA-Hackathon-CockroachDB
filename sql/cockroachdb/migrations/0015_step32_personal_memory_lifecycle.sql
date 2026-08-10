-- Memory Patch Step 32 - owner-private terminal lifecycle, deterministic
-- owner export, logical deletion, and review-only shared promotion.
-- No shared publication, canonical-evidence authority, global audit ledger,
-- review UI, Personal Memory UI, provider call, or external execution exists.

ALTER TABLE memory_patch.memory_items
  ADD COLUMN step32_terminal_kind STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step32_terminal_record_hash STRING;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step32_effective_at TIMESTAMPTZ;
ALTER TABLE memory_patch.memory_items
  ADD COLUMN step32_superseded_by_patch_id STRING;

ALTER TABLE memory_patch.memory_items
  DROP CONSTRAINT memory_items_s30_payload;
ALTER TABLE memory_patch.memory_items
  ADD CONSTRAINT memory_items_s30_payload
    CHECK (
      step30_patch_hash IS NULL
      OR (
        target_scope = 'USER_PERSONAL_HAT'
        AND visibility = 'PERSONAL'
        AND trust_class = 'PERSONAL_VERIFIED_PATCH'
        AND content_kind = 'FACTUAL'
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
            AND revoked = false
            AND step30_activation_replay_identity IS NULL
            AND step30_activation_receipt_hash IS NULL
            AND step30_activation_payload IS NULL
            AND step32_terminal_kind IS NULL
          )
          OR
          (
            step30_state_version = 7
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
            AND (
              (
                active = true AND revoked = false
                AND step32_terminal_kind IS NULL
                AND step32_terminal_record_hash IS NULL
                AND step32_effective_at IS NULL
                AND step32_superseded_by_patch_id IS NULL
              )
              OR
              (
                active = false
                AND step32_terminal_kind IN (
                  'SUPERSEDED', 'REVOKED', 'DELETED'
                )
                AND step32_terminal_record_hash ~ '^[0-9a-f]{64}$'
                AND step32_effective_at IS NOT NULL
                AND (
                  (step32_terminal_kind = 'SUPERSEDED'
                    AND revoked = false
                    AND step32_superseded_by_patch_id IS NOT NULL
                    AND step32_superseded_by_patch_id <> memory_item_id)
                  OR
                  (step32_terminal_kind IN ('REVOKED', 'DELETED')
                    AND revoked = true
                    AND step32_superseded_by_patch_id IS NULL)
                )
              )
            )
          )
        )
      )
    );

ALTER TABLE memory_patch.memory_items
  ADD CONSTRAINT memory_items_s32_superseded_patch_fk
    FOREIGN KEY (tenant_id, step32_superseded_by_patch_id)
    REFERENCES memory_patch.memory_items (tenant_id, memory_item_id)
    ON DELETE RESTRICT;

CREATE TABLE memory_patch.personal_memory_patch_supersessions (
  tenant_id STRING NOT NULL,
  supersession_id STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  old_proposal_id STRING NOT NULL,
  old_patch_id STRING NOT NULL,
  old_patch_hash STRING NOT NULL,
  old_state_hash STRING NOT NULL,
  new_proposal_id STRING NOT NULL,
  new_patch_id STRING NOT NULL,
  new_patch_hash STRING NOT NULL,
  new_state_hash STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  effective_at TIMESTAMPTZ NOT NULL,
  state_version INT8 NOT NULL,
  actor_type STRING NOT NULL,
  actor_id STRING NOT NULL,
  supersession_hash STRING NOT NULL,
  record_payload JSONB NOT NULL,
  PRIMARY KEY (tenant_id, supersession_id),
  UNIQUE (tenant_id, owner_user_id, replay_identity),
  UNIQUE (tenant_id, old_patch_id),
  CONSTRAINT personal_memory_patch_supersessions_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id, user_id, personal_memory_space_id
    ) ON DELETE RESTRICT,
  CONSTRAINT personal_memory_patch_supersessions_old_patch_fk
    FOREIGN KEY (tenant_id, old_patch_id)
    REFERENCES memory_patch.memory_items (tenant_id, memory_item_id)
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_patch_supersessions_new_patch_fk
    FOREIGN KEY (tenant_id, new_patch_id)
    REFERENCES memory_patch.memory_items (tenant_id, memory_item_id)
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_patch_supersessions_shape CHECK (
    supersession_id ~ '^personal-memory-supersession-[0-9a-f]{64}$'
    AND old_patch_id <> new_patch_id
    AND old_patch_hash ~ '^[0-9a-f]{64}$'
    AND old_state_hash ~ '^[0-9a-f]{64}$'
    AND new_patch_hash ~ '^[0-9a-f]{64}$'
    AND new_state_hash ~ '^[0-9a-f]{64}$'
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^personal-memory-step32-replay-[0-9a-f]{64}$'
    AND state_version = 8
    AND actor_type = 'HUMAN_OWNER'
    AND actor_id = owner_user_id
    AND supersession_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(record_payload) = 'object'
    AND (record_payload ->> 'supersession_id')
      IS NOT DISTINCT FROM supersession_id
    AND (record_payload ->> 'request_hash')
      IS NOT DISTINCT FROM request_hash
    AND (record_payload ->> 'replay_identity')
      IS NOT DISTINCT FROM replay_identity
    AND (record_payload ->> 'supersession_hash')
      IS NOT DISTINCT FROM supersession_hash
    AND (record_payload ->> 'old_proposal_id')
      IS NOT DISTINCT FROM old_proposal_id
    AND (record_payload ->> 'old_patch_id')
      IS NOT DISTINCT FROM old_patch_id
    AND (record_payload ->> 'old_patch_hash')
      IS NOT DISTINCT FROM old_patch_hash
    AND (record_payload ->> 'old_state_hash')
      IS NOT DISTINCT FROM old_state_hash
    AND (record_payload ->> 'new_proposal_id')
      IS NOT DISTINCT FROM new_proposal_id
    AND (record_payload ->> 'new_patch_id')
      IS NOT DISTINCT FROM new_patch_id
    AND (record_payload ->> 'new_patch_hash')
      IS NOT DISTINCT FROM new_patch_hash
    AND (record_payload ->> 'new_state_hash')
      IS NOT DISTINCT FROM new_state_hash
    AND (record_payload ->> 'tenant_id') IS NOT DISTINCT FROM tenant_id
    AND (record_payload ->> 'owner_user_id')
      IS NOT DISTINCT FROM owner_user_id
    AND (record_payload ->> 'personal_memory_space_id')
      IS NOT DISTINCT FROM personal_memory_space_id
    AND (record_payload ->> 'state') IS NOT DISTINCT FROM 'SUPERSEDED'
    AND ((record_payload ->> 'preserves_history')::BOOL) IS TRUE
    AND ((record_payload ->> 'canonical_evidence')::BOOL) IS FALSE
  )
);

CREATE TABLE memory_patch.personal_memory_patch_revocations (
  tenant_id STRING NOT NULL,
  revocation_id STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  proposal_id STRING NOT NULL,
  patch_id STRING NOT NULL,
  patch_hash STRING NOT NULL,
  active_state_hash STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  effective_at TIMESTAMPTZ NOT NULL,
  state_version INT8 NOT NULL,
  actor_type STRING NOT NULL,
  actor_id STRING NOT NULL,
  revocation_hash STRING NOT NULL,
  record_payload JSONB NOT NULL,
  PRIMARY KEY (tenant_id, revocation_id),
  UNIQUE (tenant_id, owner_user_id, replay_identity),
  UNIQUE (tenant_id, patch_id),
  CONSTRAINT personal_memory_patch_revocations_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id, user_id, personal_memory_space_id
    ) ON DELETE RESTRICT,
  CONSTRAINT personal_memory_patch_revocations_patch_fk
    FOREIGN KEY (tenant_id, patch_id)
    REFERENCES memory_patch.memory_items (tenant_id, memory_item_id)
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_patch_revocations_shape CHECK (
    revocation_id ~ '^personal-memory-revocation-[0-9a-f]{64}$'
    AND patch_hash ~ '^[0-9a-f]{64}$'
    AND active_state_hash ~ '^[0-9a-f]{64}$'
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^personal-memory-step32-replay-[0-9a-f]{64}$'
    AND state_version = 8
    AND actor_type IN ('HUMAN_OWNER', 'DETERMINISTIC_SYSTEM_POLICY')
    AND (actor_type <> 'HUMAN_OWNER' OR actor_id = owner_user_id)
    AND (actor_type <> 'DETERMINISTIC_SYSTEM_POLICY'
      OR actor_id = 'personal-memory-lifecycle-policy-1a')
    AND revocation_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(record_payload) = 'object'
    AND (record_payload ->> 'revocation_id')
      IS NOT DISTINCT FROM revocation_id
    AND (record_payload ->> 'request_hash')
      IS NOT DISTINCT FROM request_hash
    AND (record_payload ->> 'replay_identity')
      IS NOT DISTINCT FROM replay_identity
    AND (record_payload ->> 'revocation_hash')
      IS NOT DISTINCT FROM revocation_hash
    AND (record_payload ->> 'proposal_id')
      IS NOT DISTINCT FROM proposal_id
    AND (record_payload ->> 'patch_id') IS NOT DISTINCT FROM patch_id
    AND (record_payload ->> 'patch_hash') IS NOT DISTINCT FROM patch_hash
    AND (record_payload ->> 'active_state_hash')
      IS NOT DISTINCT FROM active_state_hash
    AND (record_payload ->> 'tenant_id') IS NOT DISTINCT FROM tenant_id
    AND (record_payload ->> 'owner_user_id')
      IS NOT DISTINCT FROM owner_user_id
    AND (record_payload ->> 'personal_memory_space_id')
      IS NOT DISTINCT FROM personal_memory_space_id
    AND (record_payload ->> 'state') IS NOT DISTINCT FROM 'REVOKED'
    AND (record_payload ->> 'actor_type') IS NOT DISTINCT FROM actor_type
    AND (record_payload ->> 'actor_id') IS NOT DISTINCT FROM actor_id
    AND ((record_payload ->> 'content_preserved')::BOOL) IS TRUE
    AND ((record_payload ->> 'deletion_performed')::BOOL) IS FALSE
    AND ((record_payload ->> 'canonical_evidence')::BOOL) IS FALSE
  )
);

CREATE TABLE memory_patch.personal_memory_exports (
  tenant_id STRING NOT NULL,
  export_id STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  slot_hash STRING NOT NULL,
  bundle_hash STRING NOT NULL,
  record_count INT8 NOT NULL,
  bundle_payload JSONB NOT NULL,
  exported_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, export_id),
  UNIQUE (tenant_id, owner_user_id, replay_identity),
  CONSTRAINT personal_memory_exports_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id, user_id, personal_memory_space_id
    ) ON DELETE RESTRICT,
  CONSTRAINT personal_memory_exports_shape CHECK (
    export_id ~ '^personal-memory-export-[0-9a-f]{64}$'
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^personal-memory-step32-replay-[0-9a-f]{64}$'
    AND slot_hash ~ '^[0-9a-f]{64}$'
    AND bundle_hash ~ '^[0-9a-f]{64}$'
    AND record_count BETWEEN 1 AND 1024
    AND octet_length(bundle_payload::STRING) <= 8388608
    AND jsonb_typeof(bundle_payload) = 'object'
    AND (bundle_payload ->> 'export_id') IS NOT DISTINCT FROM export_id
    AND (bundle_payload ->> 'request_hash')
      IS NOT DISTINCT FROM request_hash
    AND (bundle_payload ->> 'replay_identity')
      IS NOT DISTINCT FROM replay_identity
    AND (bundle_payload ->> 'bundle_hash') IS NOT DISTINCT FROM bundle_hash
    AND (bundle_payload ->> 'owner_user_id')
      IS NOT DISTINCT FROM owner_user_id
    AND (bundle_payload ->> 'tenant_id') IS NOT DISTINCT FROM tenant_id
    AND (bundle_payload ->> 'personal_memory_space_id')
      IS NOT DISTINCT FROM personal_memory_space_id
    AND (bundle_payload ->> 'slot_hash') IS NOT DISTINCT FROM slot_hash
    AND jsonb_typeof(bundle_payload -> 'records') = 'array'
    AND jsonb_array_length(bundle_payload -> 'records')
      IS NOT DISTINCT FROM record_count
    AND ((bundle_payload ->> 'owner_private')::BOOL) IS TRUE
    AND ((bundle_payload ->> 'shared_promotion')::BOOL) IS FALSE
    AND ((bundle_payload ->> 'canonical_evidence')::BOOL) IS FALSE
  )
);

CREATE TABLE memory_patch.personal_memory_deletions (
  tenant_id STRING NOT NULL,
  deletion_id STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  proposal_id STRING NOT NULL,
  patch_id STRING NOT NULL,
  patch_hash STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  tombstone_hash STRING NOT NULL,
  deleted_at TIMESTAMPTZ NOT NULL,
  logical_delete BOOL NOT NULL,
  physical_delete BOOL NOT NULL,
  result_hash STRING NOT NULL,
  deletion_payload JSONB NOT NULL,
  PRIMARY KEY (tenant_id, deletion_id),
  UNIQUE (tenant_id, owner_user_id, replay_identity),
  UNIQUE (tenant_id, patch_id),
  CONSTRAINT personal_memory_deletions_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id, user_id, personal_memory_space_id
    ) ON DELETE RESTRICT,
  CONSTRAINT personal_memory_deletions_patch_fk
    FOREIGN KEY (tenant_id, patch_id)
    REFERENCES memory_patch.memory_items (tenant_id, memory_item_id)
    ON DELETE RESTRICT,
  CONSTRAINT personal_memory_deletions_shape CHECK (
    deletion_id ~ '^personal-memory-deletion-[0-9a-f]{64}$'
    AND patch_hash ~ '^[0-9a-f]{64}$'
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^personal-memory-step32-replay-[0-9a-f]{64}$'
    AND tombstone_hash ~ '^[0-9a-f]{64}$'
    AND logical_delete = true
    AND physical_delete = false
    AND result_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(deletion_payload) = 'object'
    AND (deletion_payload ->> 'deletion_id')
      IS NOT DISTINCT FROM deletion_id
    AND (deletion_payload ->> 'request_hash')
      IS NOT DISTINCT FROM request_hash
    AND (deletion_payload ->> 'replay_identity')
      IS NOT DISTINCT FROM replay_identity
    AND (deletion_payload ->> 'result_hash') IS NOT DISTINCT FROM result_hash
    AND (deletion_payload ->> 'proposal_id')
      IS NOT DISTINCT FROM proposal_id
    AND (deletion_payload ->> 'patch_id') IS NOT DISTINCT FROM patch_id
    AND (deletion_payload ->> 'patch_hash') IS NOT DISTINCT FROM patch_hash
    AND (deletion_payload ->> 'tombstone_hash')
      IS NOT DISTINCT FROM tombstone_hash
    AND (deletion_payload ->> 'tenant_id') IS NOT DISTINCT FROM tenant_id
    AND (deletion_payload ->> 'owner_user_id')
      IS NOT DISTINCT FROM owner_user_id
    AND (deletion_payload ->> 'personal_memory_space_id')
      IS NOT DISTINCT FROM personal_memory_space_id
    AND (deletion_payload ->> 'slot_state') IS NOT DISTINCT FROM 'DELETED'
    AND ((deletion_payload ->> 'logical_delete')::BOOL) IS TRUE
    AND ((deletion_payload ->> 'physical_delete')::BOOL) IS FALSE
    AND ((deletion_payload ->> 'revoked')::BOOL) IS FALSE
    AND ((deletion_payload ->> 'shared_artifacts_mutated')::BOOL) IS FALSE
    AND ((deletion_payload ->> 'canonical_evidence')::BOOL) IS FALSE
  )
);

CREATE TABLE memory_patch.shared_memory_promotion_proposals (
  tenant_id STRING NOT NULL,
  promotion_id STRING NOT NULL,
  owner_user_id STRING NOT NULL,
  personal_memory_space_id STRING NOT NULL,
  source_proposal_id STRING NOT NULL,
  source_patch_id STRING NOT NULL,
  source_patch_hash STRING NOT NULL,
  source_state_hash STRING NOT NULL,
  target_hat_id STRING NOT NULL,
  candidate_shared_statement_hash STRING NOT NULL,
  deidentification_policy_digest STRING NOT NULL,
  privacy_decision STRING NOT NULL,
  owner_consent_hash STRING NOT NULL,
  canonical_evidence_compatibility STRING NOT NULL,
  review_required BOOL NOT NULL,
  lifecycle_state STRING NOT NULL,
  request_hash STRING NOT NULL,
  replay_identity STRING NOT NULL,
  proposal_hash STRING NOT NULL,
  promotion_payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, promotion_id),
  UNIQUE (tenant_id, owner_user_id, replay_identity),
  CONSTRAINT shared_memory_promotion_proposals_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id, personal_memory_space_id)
    REFERENCES memory_patch.personal_memory_spaces (
      tenant_id, user_id, personal_memory_space_id
    ) ON DELETE RESTRICT,
  CONSTRAINT shared_memory_promotion_proposals_patch_fk
    FOREIGN KEY (tenant_id, source_patch_id)
    REFERENCES memory_patch.memory_items (tenant_id, memory_item_id)
    ON DELETE RESTRICT,
  CONSTRAINT shared_memory_promotion_proposals_shape CHECK (
    promotion_id ~ '^shared-memory-promotion-[0-9a-f]{64}$'
    AND source_patch_hash ~ '^[0-9a-f]{64}$'
    AND source_state_hash ~ '^[0-9a-f]{64}$'
    AND candidate_shared_statement_hash ~ '^[0-9a-f]{64}$'
    AND deidentification_policy_digest ~ '^[0-9a-f]{64}$'
    AND privacy_decision IN ('PASS', 'REVIEW_REQUIRED', 'FAIL')
    AND owner_consent_hash ~ '^[0-9a-f]{64}$'
    AND canonical_evidence_compatibility IN ('MATCH', 'CONFLICT', 'UNCONFIRMED')
    AND review_required = true
    AND lifecycle_state = 'SHARED_PROMOTION_PROPOSED'
    AND request_hash ~ '^[0-9a-f]{64}$'
    AND replay_identity ~ '^personal-memory-step32-replay-[0-9a-f]{64}$'
    AND proposal_hash ~ '^[0-9a-f]{64}$'
    AND jsonb_typeof(promotion_payload) = 'object'
    AND (promotion_payload ->> 'promotion_id')
      IS NOT DISTINCT FROM promotion_id
    AND (promotion_payload ->> 'request_hash')
      IS NOT DISTINCT FROM request_hash
    AND (promotion_payload ->> 'replay_identity')
      IS NOT DISTINCT FROM replay_identity
    AND (promotion_payload ->> 'source_proposal_id')
      IS NOT DISTINCT FROM source_proposal_id
    AND (promotion_payload ->> 'source_patch_id')
      IS NOT DISTINCT FROM source_patch_id
    AND (promotion_payload ->> 'source_patch_hash')
      IS NOT DISTINCT FROM source_patch_hash
    AND (promotion_payload ->> 'source_state_hash')
      IS NOT DISTINCT FROM source_state_hash
    AND (promotion_payload ->> 'proposal_hash')
      IS NOT DISTINCT FROM proposal_hash
    AND (promotion_payload ->> 'tenant_id') IS NOT DISTINCT FROM tenant_id
    AND (promotion_payload ->> 'owner_user_id')
      IS NOT DISTINCT FROM owner_user_id
    AND (promotion_payload ->> 'personal_memory_space_id')
      IS NOT DISTINCT FROM personal_memory_space_id
    AND (promotion_payload ->> 'target_hat_id')
      IS NOT DISTINCT FROM target_hat_id
    AND (promotion_payload ->> 'candidate_shared_statement_sha256')
      IS NOT DISTINCT FROM candidate_shared_statement_hash
    AND (promotion_payload -> 'deidentification' ->> 'policy_digest')
      IS NOT DISTINCT FROM deidentification_policy_digest
    AND (promotion_payload -> 'deidentification' ->> 'decision')
      IS NOT DISTINCT FROM privacy_decision
    AND (promotion_payload ->> 'owner_consent_hash')
      IS NOT DISTINCT FROM owner_consent_hash
    AND (promotion_payload ->> 'canonical_evidence_compatibility')
      IS NOT DISTINCT FROM canonical_evidence_compatibility
    AND ((promotion_payload ->> 'review_required')::BOOL) IS TRUE
    AND ((promotion_payload ->> 'shared_active')::BOOL) IS FALSE
    AND ((promotion_payload ->> 'source_registry_published')::BOOL) IS FALSE
    AND ((promotion_payload ->> 'canonical_evidence')::BOOL) IS FALSE
  )
);

CREATE INDEX memory_items_s32_owner_terminal_idx
  ON memory_patch.memory_items (
    tenant_id, hat_scope_id, step32_terminal_kind,
    active, revoked, memory_item_id
  );
CREATE INDEX personal_memory_patch_supersessions_s32_owner_idx
  ON memory_patch.personal_memory_patch_supersessions (
    tenant_id, owner_user_id, personal_memory_space_id, effective_at
  );
CREATE INDEX personal_memory_patch_supersessions_s32_successor_idx
  ON memory_patch.personal_memory_patch_supersessions (
    tenant_id, new_patch_id, effective_at
  );
CREATE INDEX personal_memory_patch_revocations_s32_owner_idx
  ON memory_patch.personal_memory_patch_revocations (
    tenant_id, owner_user_id, personal_memory_space_id, effective_at
  );
CREATE INDEX personal_memory_deletions_s32_owner_idx
  ON memory_patch.personal_memory_deletions (
    tenant_id, owner_user_id, personal_memory_space_id, deleted_at
  );
CREATE INDEX shared_memory_promotion_proposals_s32_owner_idx
  ON memory_patch.shared_memory_promotion_proposals (
    tenant_id, owner_user_id, personal_memory_space_id, created_at
  );

CREATE OR REPLACE FUNCTION memory_patch.step32_patch_owner_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_patch_id STRING,
  p_patch_hash STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.memory_items AS item
        JOIN memory_patch.memory_patch_proposals AS proposal
          ON proposal.tenant_id = item.tenant_id
         AND proposal.proposal_id = item.step30_proposal_id
         AND proposal.step30_patch_id = item.memory_item_id
        JOIN memory_patch.personal_memory_spaces AS space
          ON space.tenant_id = proposal.tenant_id
         AND space.user_id = proposal.owner_user_id
         AND space.personal_memory_space_id
           = proposal.personal_memory_space_id
         AND space.hat_scope_id = item.hat_scope_id
       WHERE item.tenant_id = p_tenant_id
         AND proposal.owner_user_id = p_owner_user_id
         AND proposal.personal_memory_space_id
           = p_personal_memory_space_id
         AND item.memory_item_id = p_patch_id
         AND item.step30_patch_hash = p_patch_hash
         AND item.target_scope = 'USER_PERSONAL_HAT'
         AND item.visibility = 'PERSONAL'
         AND item.trust_class = 'PERSONAL_VERIFIED_PATCH'
         AND item.step30_state_version = 7
         AND item.active = true
         AND item.revoked = false
         AND item.step32_terminal_kind IS NULL
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step32_slot_snapshot_matches(
  p_tenant_id STRING,
  p_owner_user_id STRING,
  p_personal_memory_space_id STRING,
  p_slot_hash STRING,
  p_required_state STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT memory_patch.user_context_matches(p_tenant_id, p_owner_user_id)
    AND EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_spaces AS personal_space
       WHERE personal_space.tenant_id = p_tenant_id
         AND personal_space.user_id = p_owner_user_id
         AND personal_space.personal_memory_space_id
           = p_personal_memory_space_id
         AND (
           p_slot_hash IS NULL
           OR personal_space.slot_hash = p_slot_hash
         )
         AND (
           p_required_state IS NULL
           OR personal_space.state = p_required_state
         )
    )
$$;

CREATE OR REPLACE FUNCTION memory_patch.step32_terminal_record_matches(
  p_tenant_id STRING,
  p_patch_id STRING,
  p_patch_hash STRING,
  p_terminal_kind STRING,
  p_terminal_record_hash STRING,
  p_effective_at TIMESTAMPTZ,
  p_superseded_by_patch_id STRING
)
RETURNS BOOL
LANGUAGE SQL
STABLE
SECURITY INVOKER
AS $$
  SELECT CASE p_terminal_kind
    WHEN 'SUPERSEDED' THEN EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_patch_supersessions AS record
       WHERE record.tenant_id = p_tenant_id
         AND record.old_patch_id = p_patch_id
         AND record.old_patch_hash = p_patch_hash
         AND record.new_patch_id = p_superseded_by_patch_id
         AND record.supersession_hash = p_terminal_record_hash
         AND record.effective_at = p_effective_at
         AND memory_patch.user_context_matches(
           record.tenant_id, record.owner_user_id
         )
    )
    WHEN 'REVOKED' THEN EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_patch_revocations AS record
       WHERE record.tenant_id = p_tenant_id
         AND record.patch_id = p_patch_id
         AND record.patch_hash = p_patch_hash
         AND record.revocation_hash = p_terminal_record_hash
         AND record.effective_at = p_effective_at
         AND memory_patch.user_context_matches(
           record.tenant_id, record.owner_user_id
         )
    )
    WHEN 'DELETED' THEN EXISTS (
      SELECT 1
        FROM memory_patch.personal_memory_deletions AS record
       WHERE record.tenant_id = p_tenant_id
         AND record.patch_id = p_patch_id
         AND record.patch_hash = p_patch_hash
         AND record.result_hash = p_terminal_record_hash
         AND record.deleted_at = p_effective_at
         AND memory_patch.user_context_matches(
           record.tenant_id, record.owner_user_id
         )
    )
    ELSE false
  END
$$;

CREATE OR REPLACE FUNCTION memory_patch.guard_step32_memory_item_terminal()
RETURNS TRIGGER
LANGUAGE PLpgSQL
SECURITY INVOKER
AS $$
BEGIN
  IF (OLD).step30_patch_hash IS NULL
    OR (OLD).step30_state_version <> 7
    OR NOT (OLD).active
    OR (OLD).revoked
    OR (OLD).step32_terminal_kind IS NOT NULL
    OR (NEW).active
    OR (NEW).tenant_id IS DISTINCT FROM (OLD).tenant_id
    OR (NEW).memory_item_id IS DISTINCT FROM (OLD).memory_item_id
    OR (NEW).schema_version IS DISTINCT FROM (OLD).schema_version
    OR (NEW).hat_scope_id IS DISTINCT FROM (OLD).hat_scope_id
    OR (NEW).target_scope IS DISTINCT FROM (OLD).target_scope
    OR (NEW).visibility IS DISTINCT FROM (OLD).visibility
    OR (NEW).trust_class IS DISTINCT FROM (OLD).trust_class
    OR (NEW).content_kind IS DISTINCT FROM (OLD).content_kind
    OR (NEW).content IS DISTINCT FROM (OLD).content
    OR (NEW).scope_dimensions IS DISTINCT FROM (OLD).scope_dimensions
    OR (NEW).evidence_references IS DISTINCT FROM (OLD).evidence_references
    OR (NEW).source_patch_id IS DISTINCT FROM (OLD).source_patch_id
    OR (NEW).valid_from IS DISTINCT FROM (OLD).valid_from
    OR (NEW).valid_until IS DISTINCT FROM (OLD).valid_until
    OR (NEW).expires_at IS DISTINCT FROM (OLD).expires_at
    OR (NEW).created_at IS DISTINCT FROM (OLD).created_at
    OR (NEW).step30_proposal_id IS DISTINCT FROM (OLD).step30_proposal_id
    OR (NEW).step30_proposal_hash IS DISTINCT FROM (OLD).step30_proposal_hash
    OR (NEW).step30_approval_receipt_hash
      IS DISTINCT FROM (OLD).step30_approval_receipt_hash
    OR (NEW).step30_validation_receipt_hash
      IS DISTINCT FROM (OLD).step30_validation_receipt_hash
    OR (NEW).step30_commit_receipt_hash
      IS DISTINCT FROM (OLD).step30_commit_receipt_hash
    OR (NEW).step30_patch_hash IS DISTINCT FROM (OLD).step30_patch_hash
    OR (NEW).step30_state_version IS DISTINCT FROM (OLD).step30_state_version
    OR (NEW).step30_state_hash IS DISTINCT FROM (OLD).step30_state_hash
    OR (NEW).step30_activation_replay_identity
      IS DISTINCT FROM (OLD).step30_activation_replay_identity
    OR (NEW).step30_activation_receipt_hash
      IS DISTINCT FROM (OLD).step30_activation_receipt_hash
    OR (NEW).step30_activation_payload
      IS DISTINCT FROM (OLD).step30_activation_payload
    OR (NEW).step32_terminal_kind NOT IN (
      'SUPERSEDED', 'REVOKED', 'DELETED'
    )
    OR (NEW).step32_terminal_record_hash IS NULL
    OR (NEW).step32_effective_at IS NULL
    OR NOT memory_patch.step32_terminal_record_matches(
      (NEW).tenant_id,
      (NEW).memory_item_id,
      (NEW).step30_patch_hash,
      (NEW).step32_terminal_kind,
      (NEW).step32_terminal_record_hash,
      (NEW).step32_effective_at,
      (NEW).step32_superseded_by_patch_id
    )
  THEN
    RAISE EXCEPTION 'Step 32 patch terminal update is not exact'
      USING ERRCODE = '23514';
  END IF;
  IF ((NEW).step32_terminal_kind = 'SUPERSEDED'
        AND ((NEW).revoked OR (NEW).step32_superseded_by_patch_id IS NULL))
    OR ((NEW).step32_terminal_kind IN ('REVOKED', 'DELETED')
        AND (NOT (NEW).revoked
          OR (NEW).step32_superseded_by_patch_id IS NOT NULL))
  THEN
    RAISE EXCEPTION 'Step 32 terminal semantics differ'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS memory_items_s30_guard
  ON memory_patch.memory_items;
CREATE TRIGGER memory_items_s30_guard
  BEFORE INSERT OR UPDATE ON memory_patch.memory_items
  FOR EACH ROW
  WHEN ((NEW).step32_terminal_kind IS NULL)
  EXECUTE FUNCTION memory_patch.guard_step30_memory_item();

DROP TRIGGER IF EXISTS memory_items_s32_terminal_guard
  ON memory_patch.memory_items;
CREATE TRIGGER memory_items_s32_terminal_guard
  BEFORE UPDATE ON memory_patch.memory_items
  FOR EACH ROW
  WHEN ((NEW).step32_terminal_kind IS NOT NULL)
  EXECUTE FUNCTION memory_patch.guard_step32_memory_item_terminal();

ALTER TABLE memory_patch.personal_memory_patch_supersessions
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.personal_memory_patch_revocations
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.personal_memory_exports
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.personal_memory_deletions
  OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.shared_memory_promotion_proposals
  OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step32_patch_owner_matches(
  STRING, STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step32_slot_snapshot_matches(
  STRING, STRING, STRING, STRING, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.step32_terminal_record_matches(
  STRING, STRING, STRING, STRING, STRING, TIMESTAMPTZ, STRING
) OWNER TO mp_schema_owner;
ALTER FUNCTION memory_patch.guard_step32_memory_item_terminal()
  OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE
  memory_patch.personal_memory_patch_supersessions,
  memory_patch.personal_memory_patch_revocations,
  memory_patch.personal_memory_exports,
  memory_patch.personal_memory_deletions,
  memory_patch.shared_memory_promotion_proposals
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper;

GRANT SELECT, INSERT ON TABLE
  memory_patch.personal_memory_patch_supersessions,
  memory_patch.personal_memory_patch_revocations,
  memory_patch.personal_memory_exports,
  memory_patch.personal_memory_deletions,
  memory_patch.shared_memory_promotion_proposals
TO mp_app_runtime;

-- CockroachDB checks inbound foreign-key references while the Step 30
-- Commit Helper inserts a new parent memory_items row.  It therefore needs
-- only relation-level SELECT on the five Step 32 child tables that reference
-- memory_items or personal_memory_spaces. FORCE RLS remains in effect and no
-- SELECT policy is granted
-- to this role, so the helper cannot read lifecycle records through SQL.
GRANT SELECT ON TABLE
  memory_patch.personal_memory_patch_supersessions,
  memory_patch.personal_memory_patch_revocations,
  memory_patch.personal_memory_exports,
  memory_patch.personal_memory_deletions,
  memory_patch.shared_memory_promotion_proposals
TO mp_personal_memory_commit_helper;
GRANT UPDATE ON TABLE memory_patch.memory_items TO mp_app_runtime;
REVOKE DELETE ON TABLE
  memory_patch.personal_memory_patch_supersessions,
  memory_patch.personal_memory_patch_revocations,
  memory_patch.personal_memory_exports,
  memory_patch.personal_memory_deletions,
  memory_patch.shared_memory_promotion_proposals,
  memory_patch.memory_items
FROM mp_app_runtime;

REVOKE ALL ON FUNCTION
  memory_patch.step32_patch_owner_matches(
    STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step32_slot_snapshot_matches(
    STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step32_terminal_record_matches(
    STRING, STRING, STRING, STRING, STRING, TIMESTAMPTZ, STRING
  ),
  memory_patch.guard_step32_memory_item_terminal()
FROM PUBLIC, mp_app_runtime, mp_request_context_setter,
  mp_personal_memory_commit_helper;
GRANT EXECUTE ON FUNCTION
  memory_patch.step32_patch_owner_matches(
    STRING, STRING, STRING, STRING, STRING
  ),
  memory_patch.step32_slot_snapshot_matches(
    STRING, STRING, STRING, STRING, STRING
  )
TO mp_app_runtime;
GRANT EXECUTE ON FUNCTION
  memory_patch.step32_terminal_record_matches(
    STRING, STRING, STRING, STRING, STRING, TIMESTAMPTZ, STRING
  ),
  memory_patch.guard_step32_memory_item_terminal()
TO mp_app_runtime, mp_personal_memory_commit_helper;

-- CockroachDB evaluates every applicable memory_items UPDATE policy while
-- planning the owner-scoped Step 32 terminal update.  The Step 30 membership
-- predicate remains SECURITY INVOKER and returns false for mp_app_runtime;
-- EXECUTE here permits policy evaluation only and grants no Commit Helper
-- membership or commit authority.
GRANT EXECUTE ON FUNCTION
  memory_patch.step30_commit_helper_authorized()
TO mp_app_runtime;

ALTER TABLE memory_patch.personal_memory_patch_supersessions
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_patch_supersessions
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_patch_revocations
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_patch_revocations
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_exports
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_exports
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_deletions
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.personal_memory_deletions
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.shared_memory_promotion_proposals
  ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.shared_memory_promotion_proposals
  FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.memory_items FORCE ROW LEVEL SECURITY;

CREATE POLICY personal_memory_patch_supersessions_s32_select
  ON memory_patch.personal_memory_patch_supersessions
  FOR SELECT TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY personal_memory_patch_supersessions_s32_insert
  ON memory_patch.personal_memory_patch_supersessions
  FOR INSERT TO mp_app_runtime
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
    AND memory_patch.step32_patch_owner_matches(
      tenant_id, owner_user_id, personal_memory_space_id,
      old_patch_id, old_patch_hash
    )
    AND memory_patch.step32_patch_owner_matches(
      tenant_id, owner_user_id, personal_memory_space_id,
      new_patch_id, new_patch_hash
    )
  );

CREATE POLICY personal_memory_patch_revocations_s32_select
  ON memory_patch.personal_memory_patch_revocations
  FOR SELECT TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY personal_memory_patch_revocations_s32_insert
  ON memory_patch.personal_memory_patch_revocations
  FOR INSERT TO mp_app_runtime
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
    AND memory_patch.step32_patch_owner_matches(
      tenant_id, owner_user_id, personal_memory_space_id,
      patch_id, patch_hash
    )
  );

CREATE POLICY personal_memory_exports_s32_select
  ON memory_patch.personal_memory_exports
  FOR SELECT TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY personal_memory_exports_s32_insert
  ON memory_patch.personal_memory_exports
  FOR INSERT TO mp_app_runtime
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
    AND memory_patch.step32_slot_snapshot_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      slot_hash,
      NULL
    )
  );

CREATE POLICY personal_memory_deletions_s32_select
  ON memory_patch.personal_memory_deletions
  FOR SELECT TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY personal_memory_deletions_s32_insert
  ON memory_patch.personal_memory_deletions
  FOR INSERT TO mp_app_runtime
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
    AND memory_patch.step32_slot_snapshot_matches(
      tenant_id,
      owner_user_id,
      personal_memory_space_id,
      NULL,
      'DELETED_PENDING'
    )
    AND memory_patch.step32_patch_owner_matches(
      tenant_id, owner_user_id, personal_memory_space_id,
      patch_id, patch_hash
    )
  );

CREATE POLICY shared_memory_promotion_proposals_s32_select
  ON memory_patch.shared_memory_promotion_proposals
  FOR SELECT TO mp_app_runtime
  USING (memory_patch.user_context_matches(tenant_id, owner_user_id));
CREATE POLICY shared_memory_promotion_proposals_s32_insert
  ON memory_patch.shared_memory_promotion_proposals
  FOR INSERT TO mp_app_runtime
  WITH CHECK (
    memory_patch.user_context_matches(tenant_id, owner_user_id)
    AND review_required
    AND lifecycle_state = 'SHARED_PROMOTION_PROPOSED'
    AND memory_patch.step32_patch_owner_matches(
      tenant_id, owner_user_id, personal_memory_space_id,
      source_patch_id, source_patch_hash
    )
  );

CREATE POLICY memory_items_s32_terminal_update
  ON memory_patch.memory_items
  FOR UPDATE TO mp_app_runtime
  USING (
    active = true AND revoked = false
    AND step30_state_version = 7
    AND step32_terminal_kind IS NULL
    AND content ->> 'tenant_id' = tenant_id
    AND content ->> 'patch_id' = memory_item_id
    AND content ->> 'patch_hash' = step30_patch_hash
    AND memory_patch.user_context_matches(
      tenant_id, content ->> 'owner_user_id'
    )
  )
  WITH CHECK (
    active = false
    AND step32_terminal_kind IN ('SUPERSEDED', 'REVOKED', 'DELETED')
    AND step32_terminal_record_hash ~ '^[0-9a-f]{64}$'
    AND step32_effective_at IS NOT NULL
    AND content ->> 'tenant_id' = tenant_id
    AND content ->> 'patch_id' = memory_item_id
    AND content ->> 'patch_hash' = step30_patch_hash
    AND memory_patch.user_context_matches(
      tenant_id, content ->> 'owner_user_id'
    )
  );
