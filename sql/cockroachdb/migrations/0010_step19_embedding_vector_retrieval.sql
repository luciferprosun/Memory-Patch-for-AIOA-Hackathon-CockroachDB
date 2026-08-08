-- Memory Patch Step 19 - immutable local-model embeddings and vector retrieval.
-- VECTOR index prefixes aid planning only; RLS and hard trusted-scope joins
-- remain mandatory for every source and candidate.

CREATE TABLE memory_patch.chunk_embeddings (
  tenant_id STRING NOT NULL,
  chunk_id STRING NOT NULL,
  knowledge_version_id STRING NOT NULL,
  source_id STRING NOT NULL,
  hat_scope_id STRING NOT NULL,
  embedding_model_id STRING NOT NULL,
  embedding_model_revision STRING NOT NULL,
  embedding_model_digest STRING NOT NULL,
  embedding_dimension INT8 NOT NULL,
  content_sha256 STRING NOT NULL,
  embedding_input_digest STRING NOT NULL,
  embedding_bytes_sha256 STRING NOT NULL,
  cache_key STRING NOT NULL,
  generation_backend STRING NOT NULL,
  generation_backend_version STRING NOT NULL,
  generation_backend_fingerprint STRING NOT NULL,
  truncated BOOL NOT NULL,
  record_hash STRING NOT NULL,
  embedding VECTOR(384) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, chunk_id, embedding_model_digest),
  UNIQUE (
    tenant_id,
    chunk_id,
    knowledge_version_id,
    source_id,
    hat_scope_id,
    embedding_model_digest
  ),
  CONSTRAINT chunk_embeddings_chunk_fk
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
  CONSTRAINT chunk_embeddings_model_id_exact
    CHECK (embedding_model_id = 'intfloat/multilingual-e5-small'),
  CONSTRAINT chunk_embeddings_model_revision_exact
    CHECK (
      embedding_model_revision =
        'fd1525a9fd15316a2d503bf26ab031a61d056e98'
    ),
  CONSTRAINT chunk_embeddings_dimension_exact
    CHECK (embedding_dimension = 384),
  CONSTRAINT chunk_embeddings_content_sha256
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chunk_embeddings_model_digest
    CHECK (embedding_model_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chunk_embeddings_input_digest
    CHECK (embedding_input_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chunk_embeddings_bytes_sha256
    CHECK (embedding_bytes_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chunk_embeddings_cache_key
    CHECK (cache_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chunk_embeddings_backend_not_blank
    CHECK (
      btrim(generation_backend) <> ''
      AND btrim(generation_backend_version) <> ''
    ),
  CONSTRAINT chunk_embeddings_backend_fingerprint
    CHECK (generation_backend_fingerprint ~ '^[0-9a-f]{64}$'),
  CONSTRAINT chunk_embeddings_record_hash
    CHECK (record_hash ~ '^[0-9a-f]{64}$')
);

CREATE VECTOR INDEX chunk_embeddings_vector_l2_idx
  ON memory_patch.chunk_embeddings (
    tenant_id,
    hat_scope_id,
    embedding vector_l2_ops
  );

CREATE INDEX chunk_embeddings_model_lookup_idx
  ON memory_patch.chunk_embeddings (
    tenant_id,
    hat_scope_id,
    embedding_model_digest,
    chunk_id
  );

ALTER TABLE memory_patch.chunk_embeddings OWNER TO mp_schema_owner;

REVOKE ALL ON TABLE memory_patch.chunk_embeddings
  FROM PUBLIC, mp_app_runtime, mp_request_context_setter;
GRANT SELECT, INSERT ON TABLE memory_patch.chunk_embeddings
  TO mp_app_runtime;

ALTER TABLE memory_patch.chunk_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_patch.chunk_embeddings FORCE ROW LEVEL SECURITY;

CREATE POLICY chunk_embeddings_s19_select
  ON memory_patch.chunk_embeddings
  FOR SELECT
  TO mp_app_runtime
  USING (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );

CREATE POLICY chunk_embeddings_s19_insert
  ON memory_patch.chunk_embeddings
  FOR INSERT
  TO mp_app_runtime
  WITH CHECK (
    memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)
  );
