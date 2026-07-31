-- Step 12 trusted HAT registry. Global system configuration; no executable code.
ALTER TABLE memory_patch.hat_manifests
  ADD COLUMN IF NOT EXISTS raw_manifest_hash STRING,
  ADD COLUMN IF NOT EXISTS schema_hash STRING,
  ADD COLUMN IF NOT EXISTS canonical_manifest JSONB;

CREATE TABLE memory_patch.hat_runtime_bindings (
  runtime_binding_id STRING PRIMARY KEY,
  hat_id STRING NOT NULL,
  hat_version STRING NOT NULL,
  installation_class STRING NOT NULL,
  implementation_name STRING NOT NULL,
  implementation_version STRING NOT NULL,
  implementation_contract_version STRING NOT NULL,
  implementation_digest STRING NOT NULL,
  binding_digest STRING NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT hat_runtime_bindings_manifest_fk FOREIGN KEY (hat_id, hat_version)
    REFERENCES memory_patch.hat_manifests (hat_id, hat_version) ON DELETE RESTRICT,
  CONSTRAINT hat_runtime_bindings_system_only CHECK (installation_class = 'SYSTEM_INSTALLED'),
  CONSTRAINT hat_runtime_bindings_digests CHECK (implementation_digest ~ '^[0-9a-f]{64}$' AND binding_digest ~ '^[0-9a-f]{64}$')
);

CREATE TABLE memory_patch.hat_registry_entries (
  hat_id STRING NOT NULL,
  hat_version STRING NOT NULL,
  canonical_manifest_digest STRING NOT NULL,
  raw_manifest_digest STRING NOT NULL,
  schema_digest STRING NOT NULL,
  registry_state STRING NOT NULL,
  compatibility_decision STRING NOT NULL,
  state_version INT8 NOT NULL,
  current_event_digest STRING NOT NULL,
  runtime_binding_id STRING,
  operator_receipt_digest STRING,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (hat_id, hat_version),
  CONSTRAINT hat_registry_entries_manifest_fk FOREIGN KEY (hat_id, hat_version)
    REFERENCES memory_patch.hat_manifests (hat_id, hat_version) ON DELETE RESTRICT,
  CONSTRAINT hat_registry_entries_binding_fk FOREIGN KEY (runtime_binding_id)
    REFERENCES memory_patch.hat_runtime_bindings (runtime_binding_id) ON DELETE RESTRICT,
  CONSTRAINT hat_registry_entries_state CHECK (registry_state IN ('REGISTERED','VALIDATED','ENABLED','DISABLED','REJECTED')),
  CONSTRAINT hat_registry_entries_compatibility CHECK (compatibility_decision IN ('COMPATIBLE','INCOMPATIBLE_KERNEL_API','UNSUPPORTED_MANIFEST_SCHEMA')),
  CONSTRAINT hat_registry_entries_digests CHECK (canonical_manifest_digest ~ '^[0-9a-f]{64}$' AND raw_manifest_digest ~ '^[0-9a-f]{64}$' AND schema_digest ~ '^[0-9a-f]{64}$' AND current_event_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT hat_registry_entries_enabled_binding CHECK (registry_state != 'ENABLED' OR (runtime_binding_id IS NOT NULL AND operator_receipt_digest ~ '^[0-9a-f]{64}$')),
  CONSTRAINT hat_registry_entries_version_positive CHECK (state_version > 0),
  CONSTRAINT hat_registry_entries_time_order CHECK (updated_at >= created_at)
);

CREATE TABLE memory_patch.hat_registry_events (
  event_id STRING PRIMARY KEY,
  hat_id STRING NOT NULL,
  hat_version STRING NOT NULL,
  sequence INT8 NOT NULL,
  previous_event_digest STRING,
  from_state STRING,
  to_state STRING NOT NULL,
  actor_type STRING NOT NULL,
  actor_reference STRING NOT NULL,
  reason_codes JSONB NOT NULL,
  manifest_digest STRING NOT NULL,
  compatibility_digest STRING NOT NULL,
  runtime_binding_digest STRING,
  operator_receipt_digest STRING,
  occurred_at TIMESTAMPTZ NOT NULL,
  event_digest STRING NOT NULL UNIQUE,
  UNIQUE (hat_id, hat_version, sequence),
  CONSTRAINT hat_registry_events_entry_fk FOREIGN KEY (hat_id, hat_version)
    REFERENCES memory_patch.hat_registry_entries (hat_id, hat_version) ON DELETE RESTRICT,
  CONSTRAINT hat_registry_events_sequence CHECK (sequence > 0),
  CONSTRAINT hat_registry_events_reason_array CHECK (jsonb_typeof(reason_codes) = 'array'),
  CONSTRAINT hat_registry_events_digests CHECK (manifest_digest ~ '^[0-9a-f]{64}$' AND compatibility_digest ~ '^[0-9a-f]{64}$' AND event_digest ~ '^[0-9a-f]{64}$')
);

ALTER TABLE memory_patch.hat_runtime_bindings OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.hat_registry_entries OWNER TO mp_schema_owner;
ALTER TABLE memory_patch.hat_registry_events OWNER TO mp_schema_owner;
REVOKE ALL ON TABLE memory_patch.hat_runtime_bindings FROM PUBLIC, mp_app_runtime, mp_request_context_setter, mp_security_owner;
REVOKE ALL ON TABLE memory_patch.hat_registry_entries FROM PUBLIC, mp_app_runtime, mp_request_context_setter, mp_security_owner;
REVOKE ALL ON TABLE memory_patch.hat_registry_events FROM PUBLIC, mp_app_runtime, mp_request_context_setter, mp_security_owner;
GRANT SELECT ON TABLE memory_patch.hat_runtime_bindings, memory_patch.hat_registry_entries TO mp_app_runtime;

CREATE FUNCTION memory_patch.reject_hat_registry_event_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'hat registry events are append-only'; END $$;
CREATE TRIGGER hat_registry_events_reject_update_delete
BEFORE UPDATE OR DELETE ON memory_patch.hat_registry_events
FOR EACH ROW EXECUTE FUNCTION memory_patch.reject_hat_registry_event_mutation();
