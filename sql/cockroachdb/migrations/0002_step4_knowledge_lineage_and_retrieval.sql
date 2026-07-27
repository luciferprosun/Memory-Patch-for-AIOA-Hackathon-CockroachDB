-- Memory Patch Step 4 — immutable source-to-chunk lineage and lexical search.
-- Vector storage is deliberately deferred until model identity and dimension
-- are pinned by a later bounded decision.

CREATE TABLE memory_patch.knowledge_sources (
  tenant_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  source_kind STRING NOT NULL,
  source_reference STRING NOT NULL,
  provenance JSONB NOT NULL,
  source_observed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, source_id),
  UNIQUE (tenant_id, source_id, hat_scope_id),
  CONSTRAINT knowledge_sources_scope_fk
    FOREIGN KEY (tenant_id, hat_scope_id)
    REFERENCES memory_patch.hat_scopes (tenant_id, hat_scope_id)
    ON DELETE RESTRICT,
  CONSTRAINT knowledge_sources_id_not_blank
    CHECK (btrim(source_id) <> ''),
  CONSTRAINT knowledge_sources_kind_not_blank
    CHECK (btrim(source_kind) <> ''),
  CONSTRAINT knowledge_sources_reference_not_blank
    CHECK (btrim(source_reference) <> ''),
  CONSTRAINT knowledge_sources_provenance_object
    CHECK (jsonb_typeof(provenance) = 'object')
);

CREATE TABLE memory_patch.source_snapshots (
  tenant_id STRING NOT NULL,
  snapshot_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  byte_length INT8 NOT NULL,
  storage_class STRING NOT NULL,
  immutable_object_reference STRING NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL,
  source_observed_at TIMESTAMPTZ,
  provenance JSONB NOT NULL,
  PRIMARY KEY (tenant_id, snapshot_id),
  UNIQUE (tenant_id, snapshot_id, source_id, hat_scope_id),
  UNIQUE (tenant_id, source_id, content_sha256),
  CONSTRAINT source_snapshots_source_fk
    FOREIGN KEY (tenant_id, source_id, hat_scope_id)
    REFERENCES memory_patch.knowledge_sources (
      tenant_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT source_snapshots_id_not_blank
    CHECK (btrim(snapshot_id) <> ''),
  CONSTRAINT source_snapshots_content_sha256
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT source_snapshots_byte_length
    CHECK (byte_length >= 0),
  CONSTRAINT source_snapshots_storage_class
    CHECK (
      storage_class IN (
        'CRDB_TRANSACTIONAL',
        'S3_GLOBAL_LOCKED_SNAPSHOT',
        'S3_USER_PRIVATE_SNAPSHOT',
        'EXTERNAL_DERIVED'
      )
    ),
  CONSTRAINT source_snapshots_object_reference_not_blank
    CHECK (btrim(immutable_object_reference) <> ''),
  CONSTRAINT source_snapshots_provenance_object
    CHECK (jsonb_typeof(provenance) = 'object')
);

CREATE TABLE memory_patch.knowledge_versions (
  tenant_id STRING NOT NULL,
  knowledge_version_id STRING NOT NULL,
  source_id STRING NOT NULL,
  snapshot_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  parent_knowledge_version_id STRING,
  version_ordinal INT8 NOT NULL,
  normalized_content_sha256 STRING NOT NULL,
  normalization_profile STRING NOT NULL,
  is_current BOOL NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  provenance JSONB NOT NULL,
  PRIMARY KEY (tenant_id, knowledge_version_id),
  UNIQUE (
    tenant_id,
    knowledge_version_id,
    source_id,
    hat_scope_id
  ),
  UNIQUE (tenant_id, source_id, version_ordinal),
  CONSTRAINT knowledge_versions_snapshot_fk
    FOREIGN KEY (tenant_id, snapshot_id, source_id, hat_scope_id)
    REFERENCES memory_patch.source_snapshots (
      tenant_id,
      snapshot_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT knowledge_versions_parent_fk
    FOREIGN KEY (
      tenant_id,
      parent_knowledge_version_id,
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
  CONSTRAINT knowledge_versions_id_not_blank
    CHECK (btrim(knowledge_version_id) <> ''),
  CONSTRAINT knowledge_versions_parent_not_self
    CHECK (
      parent_knowledge_version_id IS NULL
      OR parent_knowledge_version_id <> knowledge_version_id
    ),
  CONSTRAINT knowledge_versions_ordinal_positive
    CHECK (version_ordinal > 0),
  CONSTRAINT knowledge_versions_normalized_content_sha256
    CHECK (normalized_content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT knowledge_versions_profile_not_blank
    CHECK (btrim(normalization_profile) <> ''),
  CONSTRAINT knowledge_versions_provenance_object
    CHECK (jsonb_typeof(provenance) = 'object')
);

CREATE UNIQUE INDEX knowledge_versions_one_current_source_idx
  ON memory_patch.knowledge_versions (tenant_id, source_id)
  WHERE is_current;

CREATE TABLE memory_patch.knowledge_chunks (
  tenant_id STRING NOT NULL,
  chunk_id STRING NOT NULL,
  knowledge_version_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  chunk_ordinal INT8 NOT NULL,
  content_text STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  start_offset INT8,
  end_offset INT8,
  language_tag STRING,
  metadata JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, chunk_id),
  UNIQUE (
    tenant_id,
    chunk_id,
    knowledge_version_id,
    source_id,
    hat_scope_id
  ),
  UNIQUE (tenant_id, knowledge_version_id, chunk_ordinal),
  CONSTRAINT knowledge_chunks_version_fk
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
  CONSTRAINT knowledge_chunks_id_not_blank
    CHECK (btrim(chunk_id) <> ''),
  CONSTRAINT knowledge_chunks_ordinal_nonnegative
    CHECK (chunk_ordinal >= 0),
  CONSTRAINT knowledge_chunks_content_not_blank
    CHECK (btrim(content_text) <> ''),
  CONSTRAINT knowledge_chunks_content_sha256
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT knowledge_chunks_offsets
    CHECK (
      (start_offset IS NULL AND end_offset IS NULL)
      OR
      (
        start_offset IS NOT NULL
        AND end_offset IS NOT NULL
        AND start_offset >= 0
        AND end_offset > start_offset
      )
    ),
  CONSTRAINT knowledge_chunks_language_tag_not_blank
    CHECK (language_tag IS NULL OR btrim(language_tag) <> ''),
  CONSTRAINT knowledge_chunks_metadata_object
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX knowledge_chunks_scope_retrieval_idx
  ON memory_patch.knowledge_chunks (
    tenant_id,
    hat_scope_id,
    knowledge_version_id,
    chunk_ordinal
  );

CREATE INDEX knowledge_chunks_content_identity_idx
  ON memory_patch.knowledge_chunks (
    tenant_id,
    hat_scope_id,
    content_sha256
  );

CREATE TABLE memory_patch.chunk_search_documents (
  tenant_id STRING NOT NULL,
  chunk_id STRING NOT NULL,
  knowledge_version_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  search_config STRING NOT NULL,
  search_vector TSVECTOR NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, chunk_id, search_config),
  CONSTRAINT chunk_search_documents_chunk_fk
    FOREIGN KEY (
      tenant_id,
      chunk_id,
      knowledge_version_id,
      source_id,
      hat_scope_id
    )
    REFERENCES memory_patch.knowledge_chunks (
      tenant_id,
      chunk_id,
      knowledge_version_id,
      source_id,
      hat_scope_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT chunk_search_documents_config
    CHECK (search_config IN ('simple', 'english', 'german'))
);

CREATE INDEX chunk_search_documents_scope_idx
  ON memory_patch.chunk_search_documents (
    tenant_id,
    hat_scope_id,
    search_config,
    chunk_id
  );

CREATE INVERTED INDEX chunk_search_documents_vector_idx
  ON memory_patch.chunk_search_documents (search_vector);
