-- Memory Patch Step 11 - generic deterministic parsing evidence 1A.
-- These rows are transformations and security evidence only. They do not
-- approve publication, execute source instructions, or grant semantic truth.

CREATE TABLE memory_patch.parsed_documents (
  tenant_id STRING NOT NULL,
  document_id STRING NOT NULL,
  saga_id STRING NOT NULL,
  source_id STRING NOT NULL,
  snapshot_id STRING NOT NULL,
  knowledge_version_id STRING NOT NULL,
  knowledge_version_ordinal INT8 NOT NULL,
  hat_scope_id STRING NOT NULL,
  owner_user_id STRING,
  s3_version_id STRING NOT NULL,
  locked_storage_evidence_digest STRING NOT NULL,
  input_sha256 STRING NOT NULL,
  input_byte_length INT8 NOT NULL,
  media_type STRING NOT NULL,
  parser_name STRING NOT NULL,
  parser_version STRING NOT NULL,
  parser_contract_version STRING NOT NULL,
  decoder_profile STRING NOT NULL,
  bom_policy STRING NOT NULL,
  bom_observed BOOL NOT NULL,
  normalization_profile STRING NOT NULL,
  normalization_version STRING NOT NULL,
  normalized_content_text STRING NOT NULL,
  normalized_content_sha256 STRING NOT NULL,
  normalized_character_length INT8 NOT NULL,
  language_tag STRING,
  offset_basis STRING NOT NULL,
  section_count INT8 NOT NULL,
  chunk_count INT8 NOT NULL,
  security_finding_count INT8 NOT NULL,
  section_manifest_digest STRING NOT NULL,
  chunk_manifest_digest STRING NOT NULL,
  finding_manifest_digest STRING NOT NULL,
  quarantine_required BOOL NOT NULL,
  quarantine_reason_codes JSONB NOT NULL,
  quarantine_decision_digest STRING NOT NULL,
  parse_artifact_digest STRING NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL,
  schema_version STRING NOT NULL,
  PRIMARY KEY (tenant_id, document_id),
  UNIQUE (tenant_id, document_id, source_id, hat_scope_id),
  UNIQUE (tenant_id, saga_id),
  UNIQUE (tenant_id, parse_artifact_digest),
  CONSTRAINT parsed_documents_saga_fk
    FOREIGN KEY (tenant_id, saga_id, hat_scope_id)
    REFERENCES memory_patch.ingestion_sagas (
      tenant_id,
      saga_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT parsed_documents_snapshot_fk
    FOREIGN KEY (tenant_id, snapshot_id, source_id, hat_scope_id)
    REFERENCES memory_patch.source_snapshots (
      tenant_id,
      snapshot_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT parsed_documents_version_fk
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
  CONSTRAINT parsed_documents_owner_fk
    FOREIGN KEY (tenant_id, owner_user_id)
    REFERENCES memory_patch.users (tenant_id, user_id)
    ON DELETE RESTRICT,
  CONSTRAINT parsed_documents_identity
    CHECK (
      document_id ~ '^parsedoc-[0-9a-f]{64}$'
      AND saga_id ~ '^ingsaga-[0-9a-f]{64}$'
      AND btrim(source_id) <> ''
      AND btrim(snapshot_id) <> ''
      AND btrim(knowledge_version_id) <> ''
      AND knowledge_version_ordinal > 0
      AND btrim(hat_scope_id) <> ''
      AND btrim(s3_version_id) <> ''
      AND char_length(s3_version_id) <= 1024
    ),
  CONSTRAINT parsed_documents_input
    CHECK (
      locked_storage_evidence_digest ~ '^[0-9a-f]{64}$'
      AND input_sha256 ~ '^[0-9a-f]{64}$'
      AND input_byte_length >= 0
      AND input_byte_length <= 67108864
      AND media_type IN ('text/plain', 'application/json')
    ),
  CONSTRAINT parsed_documents_profiles
    CHECK (
      btrim(parser_name) <> ''
      AND btrim(parser_version) <> ''
      AND lower(parser_version) <> 'latest'
      AND btrim(parser_contract_version) <> ''
      AND lower(parser_contract_version) <> 'latest'
      AND btrim(decoder_profile) <> ''
      AND btrim(bom_policy) <> ''
      AND btrim(normalization_profile) <> ''
      AND btrim(normalization_version) <> ''
      AND lower(normalization_version) <> 'latest'
      AND offset_basis = 'NORMALIZED_UNICODE_CODE_POINTS_NFC'
    ),
  CONSTRAINT parsed_documents_normalized_content
    CHECK (
      normalized_content_sha256 ~ '^[0-9a-f]{64}$'
      AND normalized_character_length = char_length(normalized_content_text)
      AND normalized_character_length > 0
      AND normalized_character_length <= 8388608
      AND (language_tag IS NULL OR btrim(language_tag) <> '')
    ),
  CONSTRAINT parsed_documents_counts
    CHECK (
      section_count > 0
      AND section_count <= 100000
      AND chunk_count > 0
      AND chunk_count <= 100000
      AND security_finding_count >= 0
      AND security_finding_count <= 1024
    ),
  CONSTRAINT parsed_documents_manifests
    CHECK (
      section_manifest_digest ~ '^[0-9a-f]{64}$'
      AND chunk_manifest_digest ~ '^[0-9a-f]{64}$'
      AND finding_manifest_digest ~ '^[0-9a-f]{64}$'
      AND quarantine_decision_digest ~ '^[0-9a-f]{64}$'
      AND parse_artifact_digest ~ '^[0-9a-f]{64}$'
      AND jsonb_typeof(quarantine_reason_codes) = 'array'
      AND octet_length(quarantine_reason_codes::STRING) <= 4096
      AND (
        (quarantine_required AND jsonb_array_length(quarantine_reason_codes) > 0)
        OR
        (NOT quarantine_required AND jsonb_array_length(quarantine_reason_codes) = 0)
      )
      AND schema_version = '1.0.0'
    )
);

CREATE TABLE memory_patch.parsed_sections (
  tenant_id STRING NOT NULL,
  document_id STRING NOT NULL,
  section_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  section_ordinal INT8 NOT NULL,
  parent_section_id STRING,
  section_kind STRING NOT NULL,
  structural_locator STRING,
  normalized_start_offset INT8 NOT NULL,
  normalized_end_offset INT8 NOT NULL,
  offset_basis STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  metadata JSONB NOT NULL,
  parser_profile_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, document_id, section_id),
  UNIQUE (tenant_id, document_id, section_ordinal),
  CONSTRAINT parsed_sections_document_fk
    FOREIGN KEY (tenant_id, document_id, source_id, hat_scope_id)
    REFERENCES memory_patch.parsed_documents (
      tenant_id,
      document_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT parsed_sections_parent_fk
    FOREIGN KEY (tenant_id, document_id, parent_section_id)
    REFERENCES memory_patch.parsed_sections (
      tenant_id,
      document_id,
      section_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT parsed_sections_identity
    CHECK (
      section_id ~ '^parsesection-[0-9a-f]{64}$'
      AND section_ordinal >= 0
      AND (
        parent_section_id IS NULL
        OR parent_section_id ~ '^parsesection-[0-9a-f]{64}$'
      )
      AND section_kind IN ('TEXT_BLOCK', 'JSON_VALUE')
      AND (structural_locator IS NULL OR char_length(structural_locator) <= 2048)
    ),
  CONSTRAINT parsed_sections_range
    CHECK (
      normalized_start_offset >= 0
      AND normalized_end_offset > normalized_start_offset
      AND offset_basis = 'NORMALIZED_UNICODE_CODE_POINTS_NFC'
      AND content_sha256 ~ '^[0-9a-f]{64}$'
      AND parser_profile_digest ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT parsed_sections_metadata
    CHECK (
      jsonb_typeof(metadata) = 'object'
      AND octet_length(metadata::STRING) <= 16384
    )
);

CREATE TABLE memory_patch.parse_security_findings (
  tenant_id STRING NOT NULL,
  document_id STRING NOT NULL,
  finding_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  section_id STRING,
  ruleset_name STRING NOT NULL,
  ruleset_version STRING NOT NULL,
  rule_id STRING NOT NULL,
  category STRING NOT NULL,
  severity STRING NOT NULL,
  normalized_start_offset INT8 NOT NULL,
  normalized_end_offset INT8 NOT NULL,
  evidence_excerpt_sha256 STRING NOT NULL,
  action STRING NOT NULL,
  finding_digest STRING NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, document_id, finding_id),
  UNIQUE (tenant_id, document_id, finding_digest),
  CONSTRAINT parse_security_findings_document_fk
    FOREIGN KEY (tenant_id, document_id, source_id, hat_scope_id)
    REFERENCES memory_patch.parsed_documents (
      tenant_id,
      document_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT parse_security_findings_section_fk
    FOREIGN KEY (tenant_id, document_id, section_id)
    REFERENCES memory_patch.parsed_sections (
      tenant_id,
      document_id,
      section_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT parse_security_findings_identity
    CHECK (
      finding_id ~ '^parsefinding-[0-9a-f]{64}$'
      AND btrim(ruleset_name) <> ''
      AND btrim(ruleset_version) <> ''
      AND lower(ruleset_version) <> 'latest'
      AND btrim(rule_id) <> ''
      AND category IN (
        'ROLE_OR_SYSTEM_INSTRUCTION_MARKER',
        'INSTRUCTION_OVERRIDE_PHRASE',
        'TOOL_OR_COMMAND_EXECUTION_REQUEST',
        'SECRET_OR_CREDENTIAL_EXFILTRATION_REQUEST',
        'REMOTE_OR_INDIRECT_INSTRUCTION',
        'HIDDEN_MARKUP_OR_COMMENT_INSTRUCTION',
        'ENCODED_OR_OBFUSCATED_INSTRUCTION_SIGNAL',
        'ZERO_WIDTH_OR_BIDI_CONTROL_SIGNAL',
        'RAG_POISONING_OR_RETRIEVAL_MANIPULATION_SIGNAL'
      )
      AND severity IN ('INFO', 'WARNING', 'BLOCKING')
      AND action IN ('RECORD_ONLY', 'OPERATOR_REVIEW', 'QUARANTINE')
    ),
  CONSTRAINT parse_security_findings_range
    CHECK (
      normalized_start_offset >= 0
      AND normalized_end_offset > normalized_start_offset
      AND evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
      AND finding_digest ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX parsed_documents_scope_idx
  ON memory_patch.parsed_documents (
    tenant_id,
    hat_scope_id,
    source_id,
    completed_at
  );

CREATE INDEX parsed_sections_document_ordinal_idx
  ON memory_patch.parsed_sections (
    tenant_id,
    document_id,
    section_ordinal
  );

CREATE INDEX parse_security_findings_review_idx
  ON memory_patch.parse_security_findings (
    tenant_id,
    hat_scope_id,
    severity,
    document_id
  );

ALTER TABLE memory_patch.parsed_documents OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.parsed_sections OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.parse_security_findings OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE
  memory_patch.parsed_documents,
  memory_patch.parsed_sections,
  memory_patch.parse_security_findings
FROM PUBLIC, mp_request_context_setter;

GRANT SELECT, INSERT
  ON TABLE memory_patch.parsed_documents
  TO mp_app_runtime;
GRANT SELECT, INSERT
  ON TABLE memory_patch.parsed_sections
  TO mp_app_runtime;
GRANT SELECT, INSERT
  ON TABLE memory_patch.parse_security_findings
  TO mp_app_runtime;

ALTER TABLE memory_patch.parsed_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.parsed_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.parsed_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.parsed_sections FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.parse_security_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.parse_security_findings FORCE ROW LEVEL SECURITY;

CREATE POLICY parsed_documents_s11_select
  ON memory_patch.parsed_documents
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));

CREATE POLICY parsed_documents_s11_insert
  ON memory_patch.parsed_documents
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY parsed_sections_s11_select
  ON memory_patch.parsed_sections
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));

CREATE POLICY parsed_sections_s11_insert
  ON memory_patch.parsed_sections
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY parse_security_findings_s11_select
  ON memory_patch.parse_security_findings
  FOR SELECT
  TO mp_app_runtime
  USING (memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id));

CREATE POLICY parse_security_findings_s11_insert
  ON memory_patch.parse_security_findings
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
