"""Step 19 local embeddings, cache, vector persistence, and scope tests."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import math
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.enums import (
    KnowledgeRoute,
    MemoryTargetScope,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.embeddings import (
    APPROVED_EMBEDDING_DIMENSION,
    APPROVED_INPUT_POLICY,
    APPROVED_MAXIMUM_TOKENS,
    APPROVED_MODEL_ID,
    APPROVED_MODEL_REVISION,
    APPROVED_NORMALIZATION,
    APPROVED_PASSAGE_PREFIX,
    APPROVED_QUERY_PREFIX,
    APPROVED_WEIGHT_FILENAME,
    APPROVED_WEIGHT_SHA256,
    EMBEDDING_BYTES_LENGTH,
    CockroachEmbeddingRepository,
    EmbeddingBackendIdentity,
    EmbeddingBoundaryError,
    EmbeddingGenerationRequest,
    EmbeddingGenerationService,
    EmbeddingRecord,
    EmbeddingSource,
    EmbeddingVector,
    PassageEmbeddingBatch,
    PassageEmbeddingCache,
    Step19ReasonCode,
    VectorRetrievalCandidate,
    VectorRetrievalRequest,
    VectorRetrievalService,
    load_approved_model_spec,
    normalize_embedding_vector,
    passage_cache_key,
    prepare_passage,
    prepare_query,
    vector_from_float32_bytes,
    vector_sql_literal,
    verify_embedding_record_hash,
    verify_vector_candidate_hash,
)
from aioa_memory_kernel.embeddings.local_e5 import LocalE5Backend
from aioa_memory_kernel.embeddings.repository import (
    record_from_row,
    source_from_row,
    vector_candidate_from_row,
)
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.routing import KnowledgeRouteResult, Step17ReasonCode
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)
from aioa_memory_kernel.storage import (
    EXTERNAL_VOLUME_CREATION_HEAD,
    EXTERNAL_VOLUME_EXPECTED_REMOTE,
    EXTERNAL_VOLUME_MARKER_NAME,
    EXTERNAL_VOLUME_PROJECT_ID,
    ExternalMountIdentity,
    ExternalVolumeConfig,
    ExternalVolumeRuntimeAdapter,
)
from aioa_memory_kernel.storage import external_volume as external_volume_module


ROOT = REPOSITORY_ROOT
EMBEDDING_ROOT = ROOT / "src/aioa_memory_kernel/embeddings"
RETRIEVAL_ROOT = ROOT / "src/aioa_memory_kernel/retrieval"
MIGRATION_PATH = (
    ROOT
    / "sql/cockroachdb/migrations/0010_step19_embedding_vector_retrieval.sql"
)
EVIDENCE_PATH = (
    ROOT
    / "docs/evidence/retrieval/step19-embedding-vector-validation.json"
)
STEP19_DOCUMENTS = (
    ROOT / "docs/architecture/EMBEDDING_GENERATION_VECTOR_RETRIEVAL_FOUNDATION_1A.md",
    ROOT / "docs/adr/ADR-026-embedding-generation-vector-retrieval-foundation.md",
    ROOT / "docs/operations/STEP_19_EMBEDDING_VECTOR_VALIDATION_1A.md",
    ROOT / "docs/audits/STEP_19_EMBEDDING_VECTOR_RETRIEVAL_CLOSURE_1A.md",
)
DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
DIGEST_C = "3" * 64
CONTENT = "§ 1 Die Würde und die Rechte bleiben geschützt."
CONTENT_SHA256 = hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()
SPEC = load_approved_model_spec()


def scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension("legal_jurisdiction", "DE_FEDERAL", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
        ScopeDimension("legal_source_class", ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "policy", True),
        ScopeDimension("source_language", "de", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
    )


def route(kind: KnowledgeRoute = KnowledgeRoute.HAT_ASSIST) -> KnowledgeRouteResult:
    selected = kind in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}
    reason = {
        KnowledgeRoute.HAT_ASSIST: Step17ReasonCode.SINGLE_ASSISTING_HAT,
        KnowledgeRoute.HAT_ENFORCE: Step17ReasonCode.MANDATORY_HAT_POLICY,
        KnowledgeRoute.PASS_THROUGH: Step17ReasonCode.NO_ELIGIBLE_HAT,
        KnowledgeRoute.AMBIGUOUS: Step17ReasonCode.MULTIPLE_HAT_CONFLICT,
    }[kind]
    return KnowledgeRouteResult(
        request_id="request-step19-1",
        tenant_id="tenant-step19",
        user_id="user-step19",
        routing_input_hash=DIGEST_A,
        registry_snapshot_hash=DIGEST_B,
        knowledge_route=kind,
        selected_hat_id="german-law" if selected else None,
        selected_hat_version="1.0.0" if selected else None,
        selected_manifest_digest=DIGEST_C if selected else None,
        effective_scope=scope(),
        eligible_candidate_hashes=(DIGEST_A,) if selected else (),
        reason_codes=(reason,),
    )


def private_route() -> KnowledgeRouteResult:
    value = route()
    private_scope = tuple(
        sorted(
            (
                *value.effective_scope,
                ScopeDimension("personal_memory_space_id", "space-1", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "policy", True),
                ScopeDimension("target_scope", MemoryTargetScope.USER_PERSONAL_HAT.value, ScopeValueType.STRING, ScopeComparisonMode.EXACT, "policy", True),
            ),
            key=lambda item: item.name,
        )
    )
    object.__setattr__(value, "effective_scope", private_scope)
    object.__setattr__(value, "route_hash", canonical_sha256(value, exclude_fields=("route_hash",)))
    return value


def generation_request(
    route_value: KnowledgeRouteResult | None = None,
    **overrides: object,
) -> EmbeddingGenerationRequest:
    selected = route_value or route()
    values: dict[str, object] = {
        "route": selected,
        "tenant_id": selected.tenant_id,
        "user_id": selected.user_id,
        "request_id": selected.request_id,
        "route_hash": selected.route_hash,
        "selected_hat_id": selected.selected_hat_id,
        "selected_hat_version": selected.selected_hat_version,
        "selected_manifest_digest": selected.selected_manifest_digest,
        "effective_scope": selected.effective_scope,
        "hat_scope_id": "german-law-global-1a" if selected.selected_hat_id else None,
        "model_digest": SPEC.model_digest,
    }
    values.update(overrides)
    return EmbeddingGenerationRequest(**values)  # type: ignore[arg-type]


def vector_request(
    route_value: KnowledgeRouteResult | None = None,
    **overrides: object,
) -> VectorRetrievalRequest:
    selected = route_value or route()
    values: dict[str, object] = {
        "route": selected,
        "tenant_id": selected.tenant_id,
        "user_id": selected.user_id,
        "request_id": selected.request_id,
        "route_hash": selected.route_hash,
        "selected_hat_id": selected.selected_hat_id,
        "selected_hat_version": selected.selected_hat_version,
        "selected_manifest_digest": selected.selected_manifest_digest,
        "effective_scope": selected.effective_scope,
        "hat_scope_id": "german-law-global-1a" if selected.selected_hat_id else None,
        "query_text": "Welche Rechte schützt § 1?",
        "model_digest": SPEC.model_digest,
    }
    values.update(overrides)
    return VectorRetrievalRequest(**values)  # type: ignore[arg-type]


def unit_vector(index: int = 0) -> EmbeddingVector:
    values = [0.0] * APPROVED_EMBEDDING_DIMENSION
    values[index] = 1.0
    return EmbeddingVector(tuple(values))


def embedding_source(**overrides: object) -> EmbeddingSource:
    values: dict[str, object] = {
        "tenant_id": "tenant-step19",
        "hat_scope_id": "german-law-global-1a",
        "source_id": "source-1",
        "knowledge_version_id": "version-1",
        "chunk_id": "chunk-1",
        "chunk_ordinal": 0,
        "content": CONTENT,
        "content_sha256": CONTENT_SHA256,
        "version_ordinal": 1,
        "source_scope_digest": DIGEST_A,
        "source_registry_digest": DIGEST_B,
        "source_artifact_digest": DIGEST_C,
        "effective_scope": scope(),
    }
    values.update(overrides)
    return EmbeddingSource(**values)  # type: ignore[arg-type]


def embedding_record(
    vector: EmbeddingVector | None = None,
    source: EmbeddingSource | None = None,
    **overrides: object,
) -> EmbeddingRecord:
    selected_vector = vector or unit_vector()
    selected_source = source or embedding_source()
    prepared = prepare_passage(selected_source.content, SPEC)
    prepared_sha = hashlib.sha256(prepared.encode("utf-8")).hexdigest()
    values: dict[str, object] = {
        "tenant_id": selected_source.tenant_id,
        "hat_scope_id": selected_source.hat_scope_id,
        "source_id": selected_source.source_id,
        "knowledge_version_id": selected_source.knowledge_version_id,
        "chunk_id": selected_source.chunk_id,
        "content_sha256": selected_source.content_sha256,
        "model_id": SPEC.model_id,
        "model_revision": SPEC.model_revision,
        "model_digest": SPEC.model_digest,
        "embedding_dimension": SPEC.embedding_dimension,
        "embedding_input_digest": canonical_sha256({"source_hash": selected_source.source_hash, "prepared_sha256": prepared_sha}),
        "embedding_bytes_sha256": selected_vector.bytes_sha256,
        "cache_key": passage_cache_key(
            model_digest=SPEC.model_digest,
            content_sha256=selected_source.content_sha256,
            prepared_passage_sha256=prepared_sha,
            input_policy_version=SPEC.input_policy_version,
        ),
        "generation_backend": "fake-step19",
        "generation_backend_version": "1.0.0",
        "generation_backend_fingerprint": DIGEST_A,
        "truncated": False,
    }
    values.update(overrides)
    return EmbeddingRecord(**values)  # type: ignore[arg-type]


def database_row(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-step19",
        "hat_scope_id": "german-law-global-1a",
        "source_id": "source-1",
        "knowledge_version_id": "version-1",
        "chunk_id": "chunk-1",
        "chunk_ordinal": "0",
        "content_sha256": CONTENT_SHA256,
        "content_text": CONTENT,
        "language_tag": "de",
        "authority_level": "AUTHORITATIVE_SECONDARY",
        "authority_basis": json.dumps({"official_identifier": "BJNR000010949"}),
        "source_kind": "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
        "source_reference": "gii:BJNR000010949",
        "publication_state": "PUBLISHED",
        "access_class": "PUBLIC",
        "target_scope": "SHARED_KNOWLEDGE_HAT",
        "owner_user_id": None,
        "personal_memory_space_id": None,
        "scope_digest": DIGEST_A,
        "registry_digest": DIGEST_B,
        "artifact_digest": DIGEST_C,
        "snapshot_id": "snapshot-1",
        "metadata": json.dumps({"official_identifier": "BJNR000010949"}),
        "version_ordinal": "1",
        "is_current": "true",
        "snapshot_content_sha256": DIGEST_A,
        "scope_dimensions": json.dumps({"jurisdiction": "DE_FEDERAL", "language": "de"}),
        "embedding_model_digest": SPEC.model_digest,
        "embedding_bytes_sha256": unit_vector().bytes_sha256,
        "vector_distance": "0.12500000",
    }
    value.update(overrides)
    return value


def embedding_record_row(**overrides: object) -> dict[str, object]:
    record = embedding_record()
    value: dict[str, object] = {
        "tenant_id": record.tenant_id,
        "hat_scope_id": record.hat_scope_id,
        "source_id": record.source_id,
        "knowledge_version_id": record.knowledge_version_id,
        "chunk_id": record.chunk_id,
        "content_sha256": record.content_sha256,
        "embedding_model_id": record.model_id,
        "embedding_model_revision": record.model_revision,
        "embedding_model_digest": record.model_digest,
        "embedding_dimension": record.embedding_dimension,
        "embedding_input_digest": record.embedding_input_digest,
        "embedding_bytes_sha256": record.embedding_bytes_sha256,
        "cache_key": record.cache_key,
        "generation_backend": record.generation_backend,
        "generation_backend_version": record.generation_backend_version,
        "generation_backend_fingerprint": record.generation_backend_fingerprint,
        "truncated": record.truncated,
        "record_hash": record.record_hash,
    }
    value.update(overrides)
    return value


class FakeTransaction:
    def __init__(self, rows: tuple[dict[str, object], ...] = ()) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def fetch_all(self, sql: str, parameters: object = None) -> tuple[dict[str, object], ...]:
        self.calls.append((sql, tuple(parameters or ())))
        return self.rows

    def fetch_one(self, sql: str, parameters: object = None) -> dict[str, object] | None:
        self.calls.append((sql, tuple(parameters or ())))
        return self.rows[0] if self.rows else None

    def execute(self, sql: str, parameters: object = None) -> None:
        self.calls.append((sql, tuple(parameters or ())))


class FakeBackend:
    def __init__(self) -> None:
        self.passages: list[tuple[str, ...]] = []
        self.queries: list[str] = []
        self._identity = EmbeddingBackendIdentity.create(
            backend_name="fake-step19",
            backend_version="1.0.0",
            model_spec=SPEC,
            runtime_components={"fake": "1.0.0"},
        )

    def identity(self) -> EmbeddingBackendIdentity:
        return self._identity

    @staticmethod
    def _vector(text: str) -> EmbeddingVector:
        index = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:4], 16) % APPROVED_EMBEDDING_DIMENSION
        return unit_vector(index)

    def embed_passages(self, prepared_passages: tuple[str, ...]) -> PassageEmbeddingBatch:
        self.passages.append(prepared_passages)
        return PassageEmbeddingBatch(
            tuple(self._vector(text) for text in prepared_passages),
            tuple(False for _ in prepared_passages),
        )

    def embed_query(self, prepared_query: str) -> EmbeddingVector:
        self.queries.append(prepared_query)
        return self._vector(prepared_query)


class FakeRepository:
    def __init__(
        self,
        *,
        sources: tuple[EmbeddingSource, ...] = (),
        candidates: tuple[VectorRetrievalCandidate, ...] = (),
    ) -> None:
        self.sources = sources
        self.candidates = candidates
        self.records: dict[tuple[str, str], EmbeddingRecord] = {}

    def select_sources(self, transaction: object, request: EmbeddingGenerationRequest) -> tuple[EmbeddingSource, ...]:
        return self.sources

    def find_record(self, transaction: object, source: EmbeddingSource, model_digest: str) -> EmbeddingRecord | None:
        return self.records.get((source.chunk_id, model_digest))

    def insert_record(self, transaction: object, record: EmbeddingRecord, vector: EmbeddingVector) -> None:
        key = (record.chunk_id, record.model_digest)
        if key in self.records:
            raise AssertionError("duplicate fake insert")
        self.records[key] = record

    def search_vectors(self, transaction: object, request: VectorRetrievalRequest, query_vector: EmbeddingVector) -> tuple[VectorRetrievalCandidate, ...]:
        return self.candidates


def patched_runner() -> tuple[SerializableTransactionRunner, mock._patch]:
    runner = SerializableTransactionRunner(lambda: None)
    patcher = mock.patch.object(
        runner,
        "run",
        side_effect=lambda context, callback, operation_kind=None: callback(object()),
    )
    patcher.start()
    return runner, patcher


def fake_cache() -> PassageEmbeddingCache:
    value = object.__new__(PassageEmbeddingCache)
    value.read = mock.Mock(return_value=None)  # type: ignore[method-assign]
    value.store = mock.Mock(side_effect=lambda record, vector: vector)  # type: ignore[method-assign]
    return value


class ModelContractTests(unittest.TestCase):
    def test_exact_approved_model_identity(self) -> None:
        self.assertEqual(SPEC.model_id, APPROVED_MODEL_ID)
        self.assertEqual(SPEC.model_revision, APPROVED_MODEL_REVISION)
        self.assertEqual(SPEC.embedding_dimension, 384)
        self.assertEqual(SPEC.maximum_tokens, 512)
        self.assertEqual(SPEC.weight_sha256, APPROVED_WEIGHT_SHA256)

    def test_model_config_is_canonical_and_hash_bound(self) -> None:
        path = ROOT / "config/embeddings/multilingual-e5-small-step19-1a.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path.read_bytes(), (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
        self.assertEqual(value["config_digest"], SPEC.model_digest)

    def test_floating_revisions_are_rejected(self) -> None:
        for revision in ("main", "latest", "refs/heads/main"):
            with self.subTest(revision=revision), self.assertRaises(EmbeddingBoundaryError):
                dataclasses.replace(SPEC, model_revision=revision)

    def test_wrong_dimension_and_max_tokens_are_rejected(self) -> None:
        for changes in ({"embedding_dimension": 383}, {"embedding_dimension": 385}, {"maximum_tokens": 513}):
            with self.subTest(changes=changes), self.assertRaises(EmbeddingBoundaryError):
                dataclasses.replace(SPEC, **changes)

    def test_malformed_weight_hash_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            dataclasses.replace(SPEC, weight_sha256="bad")

    def test_changed_revision_changes_model_digest(self) -> None:
        changed = dataclasses.replace(SPEC, model_revision="0" * 40)
        self.assertNotEqual(changed.model_digest, SPEC.model_digest)

    def test_changed_prefix_policy_changes_model_digest(self) -> None:
        changed = dataclasses.replace(SPEC, query_prefix="search: ")
        self.assertNotEqual(changed.model_digest, SPEC.model_digest)

    def test_changed_normalization_changes_model_digest(self) -> None:
        changed = dataclasses.replace(SPEC, normalization="STRICT_L2")
        self.assertNotEqual(changed.model_digest, SPEC.model_digest)

    def test_caller_cannot_choose_model_in_request(self) -> None:
        self.assertNotIn("model_id", inspect.signature(VectorRetrievalRequest).parameters)
        self.assertNotIn("model_revision", inspect.signature(VectorRetrievalRequest).parameters)
        self.assertEqual(inspect.signature(load_approved_model_spec).parameters, {})

    def test_local_backend_caller_cannot_supply_model_spec(self) -> None:
        self.assertEqual(tuple(inspect.signature(LocalE5Backend).parameters), ("model_directory",))

    def test_runtime_requires_safetensors_without_remote_code(self) -> None:
        source = (EMBEDDING_ROOT / "local_e5.py").read_text(encoding="utf-8")
        self.assertIn("use_safetensors=True", source)
        self.assertIn("trust_remote_code=False", source)
        self.assertNotIn("pytorch_model.bin", source)
        self.assertNotIn("pickle", source.casefold())

    def test_exact_prefix_and_license_contract(self) -> None:
        self.assertEqual((SPEC.query_prefix, SPEC.passage_prefix), (APPROVED_QUERY_PREFIX, APPROVED_PASSAGE_PREFIX))
        self.assertEqual(SPEC.input_policy_version, APPROVED_INPUT_POLICY)
        self.assertEqual(SPEC.normalization, APPROVED_NORMALIZATION)
        self.assertEqual((SPEC.weight_filename, SPEC.license), (APPROVED_WEIGHT_FILENAME, "MIT"))


class EmbeddingVectorTests(unittest.TestCase):
    def test_exactly_384_float32_values_are_accepted(self) -> None:
        value = unit_vector()
        self.assertEqual(len(value.values), 384)
        self.assertEqual(len(value.float32_bytes), 1536)

    def test_wrong_dimensions_are_rejected(self) -> None:
        for length in (383, 385):
            with self.subTest(length=length), self.assertRaisesRegex(EmbeddingBoundaryError, "EMBEDDING_VECTOR_INVALID"):
                EmbeddingVector(tuple([1.0] + [0.0] * (length - 1)))

    def test_nonfinite_values_are_rejected(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            values = [0.0] * 384
            values[0] = value
            with self.subTest(value=value), self.assertRaises(EmbeddingBoundaryError):
                EmbeddingVector(tuple(values))

    def test_zero_and_non_normalized_vectors_are_rejected(self) -> None:
        for values in ((0.0,) * 384, (2.0,) + (0.0,) * 383):
            with self.assertRaises(EmbeddingBoundaryError):
                EmbeddingVector(values)

    def test_trusted_normalizer_produces_unit_vector(self) -> None:
        value = normalize_embedding_vector((3.0, 4.0) + (0.0,) * 382)
        self.assertAlmostEqual(math.sqrt(sum(item * item for item in value.values)), 1.0, places=6)

    def test_zero_vector_cannot_be_normalized(self) -> None:
        with self.assertRaises(EmbeddingBoundaryError):
            normalize_embedding_vector((0.0,) * 384)

    def test_float32_bytes_and_sha_are_deterministic(self) -> None:
        first = unit_vector(12)
        second = unit_vector(12)
        self.assertEqual(first.float32_bytes, second.float32_bytes)
        self.assertEqual(first.bytes_sha256, second.bytes_sha256)
        self.assertEqual(first.bytes_sha256, hashlib.sha256(first.float32_bytes).hexdigest())

    def test_exact_float32_replay_is_accepted(self) -> None:
        value = unit_vector(3)
        self.assertEqual(vector_from_float32_bytes(value.float32_bytes), value)

    def test_wrong_cache_byte_length_is_rejected(self) -> None:
        for payload in (b"", b"\x00" * (EMBEDDING_BYTES_LENGTH - 1), b"\x00" * (EMBEDDING_BYTES_LENGTH + 1)):
            with self.assertRaisesRegex(EmbeddingBoundaryError, "CACHE_INTEGRITY_INVALID"):
                vector_from_float32_bytes(payload)

    def test_vector_sql_literal_is_internal_and_bounded(self) -> None:
        literal = vector_sql_literal(unit_vector())
        self.assertTrue(literal.startswith("[1,0,0"))
        self.assertEqual(literal.count(","), 383)


class CacheFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.mountpoint = Path(self.temporary.name) / "external"
        self.data_root = self.mountpoint / "AIOA_DATA" / "Memory-Patch-for-AIOA"
        self.data_root.mkdir(parents=True)
        for relative in external_volume_module._REQUIRED_DIRECTORIES:
            (self.data_root / relative).mkdir(parents=True, exist_ok=True)
        marker = {
            "schema_version": "1.0.0",
            "project_id": EXTERNAL_VOLUME_PROJECT_ID,
            "purpose": "external-data-volume",
            "device_uuid": "11111111-2222-3333-4444-555555555555",
            "device_label": "STEP19_TEST",
            "filesystem_type": "ext4",
            "created_at_utc": "2026-07-25T05:25:40Z",
            "repository_remote": EXTERNAL_VOLUME_EXPECTED_REMOTE,
            "repository_head_at_creation": EXTERNAL_VOLUME_CREATION_HEAD,
        }
        marker_path = self.data_root / EXTERNAL_VOLUME_MARKER_NAME
        marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        marker_path.chmod(0o640)
        config = ExternalVolumeConfig.from_mapping({
            "AIOA_EXTERNAL_MOUNTPOINT": str(self.mountpoint),
            "AIOA_EXTERNAL_DATA_ROOT": str(self.data_root),
            "AIOA_EXTERNAL_DEVICE_UUID": marker["device_uuid"],
            "AIOA_EXTERNAL_DEVICE_LABEL": marker["device_label"],
            "AIOA_EXTERNAL_FILESYSTEM_TYPE": "ext4",
            "AIOA_EXTERNAL_DEVICE_TRANSPORT": "usb",
        })
        device = self.mountpoint.stat().st_dev
        identity = ExternalMountIdentity(
            target=self.mountpoint,
            source="/dev/synthetic1",
            filesystem_type="ext4",
            mount_options=frozenset({"rw", "nodev", "nosuid"}),
            device_uuid=str(marker["device_uuid"]),
            device_label=str(marker["device_label"]),
            device_transport="usb",
            device_read_only=False,
            source_is_block_device=True,
            total_bytes=128 * 1024**3,
            available_bytes=96 * 1024**3,
            mount_device_id=device,
            system_root_device_id=device + 1,
        )

        class Probe:
            def inspect(self, mountpoint: Path) -> ExternalMountIdentity:
                return identity

        self.adapter = ExternalVolumeRuntimeAdapter(config, Probe())
        self.cache = PassageEmbeddingCache(self.adapter)

    def tearDown(self) -> None:
        self.temporary.cleanup()


class CacheTests(CacheFixture):
    def test_same_model_and_content_give_same_cache_key(self) -> None:
        self.assertEqual(embedding_record().cache_key, embedding_record().cache_key)

    def test_model_and_content_changes_change_cache_identity(self) -> None:
        base = embedding_source()
        changed_content = "Anderer kanonischer Inhalt."
        changed = embedding_source(content=changed_content, content_sha256=hashlib.sha256(changed_content.encode()).hexdigest())
        base_prepared = hashlib.sha256(prepare_passage(base.content, SPEC).encode()).hexdigest()
        changed_prepared = hashlib.sha256(prepare_passage(changed.content, SPEC).encode()).hexdigest()
        first = passage_cache_key(model_digest=SPEC.model_digest, content_sha256=base.content_sha256, prepared_passage_sha256=base_prepared, input_policy_version=SPEC.input_policy_version)
        second = passage_cache_key(model_digest=SPEC.model_digest, content_sha256=changed.content_sha256, prepared_passage_sha256=changed_prepared, input_policy_version=SPEC.input_policy_version)
        third = passage_cache_key(model_digest="4" * 64, content_sha256=base.content_sha256, prepared_passage_sha256=base_prepared, input_policy_version=SPEC.input_policy_version)
        self.assertEqual(len({first, second, third}), 3)

    def test_exact_cache_write_and_replay(self) -> None:
        vector = unit_vector(5)
        record = embedding_record(vector)
        self.assertEqual(self.cache.store(record, vector), vector)
        self.assertEqual(self.cache.read(record), vector)

    def test_corrupt_cache_hash_is_rejected(self) -> None:
        vector = unit_vector(6)
        record = embedding_record(vector)
        self.cache.store(record, vector)
        path = self.data_root / "embeddings" / self.cache.relative_path(record)
        path.write_bytes(b"\x00" * EMBEDDING_BYTES_LENGTH)
        with self.assertRaisesRegex(EmbeddingBoundaryError, "CACHE_INTEGRITY_INVALID"):
            self.cache.read(record)

    def test_symlink_cache_file_is_rejected(self) -> None:
        vector = unit_vector(7)
        record = embedding_record(vector)
        target = self.data_root / "embeddings" / self.cache.relative_path(record)
        other = Path(self.temporary.name) / "other.f32"
        other.write_bytes(vector.float32_bytes)
        target.symlink_to(other)
        with self.assertRaisesRegex(EmbeddingBoundaryError, "CACHE_INTEGRITY_INVALID"):
            self.cache.read(record)

    def test_special_cache_file_is_rejected(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        record = embedding_record(unit_vector(8))
        target = self.data_root / "embeddings" / self.cache.relative_path(record)
        os.mkfifo(target)
        self.assertTrue(stat.S_ISFIFO(target.lstat().st_mode))
        with self.assertRaisesRegex(EmbeddingBoundaryError, "CACHE_INTEGRITY_INVALID"):
            self.cache.read(record)

    def test_cache_cannot_override_source_or_model_identity(self) -> None:
        record = embedding_record()
        with self.assertRaises(EmbeddingBoundaryError):
            dataclasses.replace(record, model_digest="4" * 64)
        with self.assertRaises((EmbeddingBoundaryError, ContractValidationError)):
            dataclasses.replace(record, tenant_id="")

    def test_system_drive_fallback_is_forbidden(self) -> None:
        self.assertFalse(self.cache.system_drive_fallback_allowed)
        self.assertFalse(self.adapter.system_drive_fallback_allowed)


class RouteBindingTests(unittest.TestCase):
    def test_hat_assist_and_enforce_are_accepted(self) -> None:
        self.assertEqual(vector_request().route.knowledge_route, KnowledgeRoute.HAT_ASSIST)
        self.assertEqual(vector_request(route(KnowledgeRoute.HAT_ENFORCE)).route.knowledge_route, KnowledgeRoute.HAT_ENFORCE)

    def test_pass_through_returns_no_vector_retrieval(self) -> None:
        backend = FakeBackend()
        runner, patcher = patched_runner()
        try:
            result = VectorRetrievalService(runner, backend, FakeRepository()).retrieve(vector_request(route(KnowledgeRoute.PASS_THROUGH)))
            self.assertEqual(result.reason_codes, (Step19ReasonCode.NO_HAT_SELECTED,))
            self.assertEqual(backend.queries, [])
            runner.run.assert_not_called()  # type: ignore[attr-defined]
        finally:
            patcher.stop()

    def test_ambiguous_route_is_denied_before_model_or_database(self) -> None:
        backend = FakeBackend()
        runner, patcher = patched_runner()
        try:
            with self.assertRaisesRegex(EmbeddingBoundaryError, "AMBIGUOUS_ROUTE"):
                VectorRetrievalService(runner, backend, FakeRepository()).retrieve(vector_request(route(KnowledgeRoute.AMBIGUOUS)))
            self.assertEqual(backend.queries, [])
            runner.run.assert_not_called()  # type: ignore[attr-defined]
        finally:
            patcher.stop()

    def test_stale_route_hash_is_denied(self) -> None:
        value = route()
        object.__setattr__(value, "route_hash", "0" * 64)
        with self.assertRaisesRegex(EmbeddingBoundaryError, "ROUTE_HASH_INVALID"):
            vector_request(value)

    def test_wrong_bound_identities_are_denied(self) -> None:
        changes = {
            "tenant_id": "tenant-other",
            "user_id": "user-other",
            "request_id": "request-other",
            "selected_hat_id": "other-hat",
            "selected_hat_version": "2.0.0",
            "selected_manifest_digest": DIGEST_A,
        }
        for field, changed in changes.items():
            with self.subTest(field=field), self.assertRaises(EmbeddingBoundaryError):
                vector_request(**{field: changed})

    def test_effective_scope_cannot_be_widened(self) -> None:
        widened = tuple(sorted(scope() + (ScopeDimension("federal_state", "BY", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "caller", True),), key=lambda item: item.name))
        with self.assertRaisesRegex(EmbeddingBoundaryError, "ROUTE_SCOPE_MISMATCH"):
            vector_request(effective_scope=widened)

    def test_personal_scope_requires_exact_route_owner_space(self) -> None:
        value = private_route()
        accepted = vector_request(value, effective_scope=value.effective_scope, personal_memory_space_id="space-1")
        self.assertEqual(accepted.personal_memory_space_id, "space-1")
        with self.assertRaisesRegex(EmbeddingBoundaryError, "ROUTE_SCOPE_MISMATCH"):
            vector_request(value, effective_scope=value.effective_scope, personal_memory_space_id="space-other")


class RepositoryAndHardScopeTests(unittest.TestCase):
    def test_embedding_source_selection_uses_shared_hard_scope(self) -> None:
        transaction = FakeTransaction((database_row(),))
        values = CockroachEmbeddingRepository().select_sources(transaction, generation_request())
        self.assertEqual(values[0].chunk_id, "chunk-1")
        sql, parameters = transaction.calls[0]
        for token in (
            "sre.tenant_id = %s",
            "sre.hat_scope_id = %s",
            "hs.knowledge_hat_id = %s",
            "hs.knowledge_hat_version = %s",
            "hm.manifest_hash = %s",
            "current_publication_state = 'PUBLISHED'",
            "authority_level IN ('OFFICIAL_PRIMARY', 'AUTHORITATIVE_SECONDARY')",
            "access_class IN ('PUBLIC', 'TENANT_RESTRICTED')",
            "sre.model_generated = false",
        ):
            self.assertIn(token, sql)
        self.assertIn("tenant-step19", parameters)
        self.assertIn(SPEC.model_digest, generation_request().model_digest)

    def test_every_lineage_join_preserves_tenant_and_hat_scope(self) -> None:
        transaction = FakeTransaction()
        CockroachEmbeddingRepository().select_sources(transaction, generation_request())
        sql = transaction.calls[0][0]
        self.assertGreaterEqual(sql.count("tenant_id ="), 5)
        self.assertGreaterEqual(sql.count("hat_scope_id ="), 5)

    def test_private_scope_binds_owner_and_personal_space_before_candidates(self) -> None:
        value = private_route()
        request = generation_request(value, effective_scope=value.effective_scope, personal_memory_space_id="space-1")
        transaction = FakeTransaction()
        CockroachEmbeddingRepository().select_sources(transaction, request)
        sql, parameters = transaction.calls[0]
        self.assertIn("access_class = 'USER_PRIVATE'", sql)
        self.assertIn("sre.owner_user_id = %s", sql)
        self.assertIn("hs.owner_user_id = %s", sql)
        self.assertIn("space-1", parameters)
        self.assertIn("user-step19", parameters)

    def test_vector_query_is_parameterized_and_model_filtered(self) -> None:
        transaction = FakeTransaction((database_row(),))
        candidates = CockroachEmbeddingRepository().search_vectors(transaction, vector_request(query_text="x'); DROP TABLE source_registry_entries; --"), unit_vector())
        self.assertEqual(len(candidates), 1)
        sql, parameters = transaction.calls[0]
        self.assertIn("ce.embedding_model_digest = %s", sql)
        self.assertIn("ce.embedding <-> %s::VECTOR(384)", sql)
        self.assertIn(SPEC.model_digest, parameters)
        self.assertNotIn("DROP TABLE", sql)

    def test_vector_query_orders_by_l2_then_immutable_tiebreakers(self) -> None:
        transaction = FakeTransaction()
        CockroachEmbeddingRepository().search_vectors(transaction, vector_request(), unit_vector())
        sql = transaction.calls[0][0]
        self.assertIn("ORDER BY ce.embedding <-> %s::VECTOR(384)", sql)
        self.assertIn("kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id", sql)

    def test_vector_query_fetches_only_one_truncation_sentinel(self) -> None:
        transaction = FakeTransaction()
        CockroachEmbeddingRepository().search_vectors(transaction, vector_request(maximum_results=7), unit_vector())
        self.assertEqual(transaction.calls[0][1][-1], 8)

    def test_vector_candidate_requires_published_supported_authority(self) -> None:
        for field, values in (
            ("publication_state", ("REGISTERED", "QUARANTINED", "WITHDRAWN", "REJECTED")),
            ("authority_level", ("INFORMATIONAL_SECONDARY", "USER_SUPPLIED", "DERIVED", "UNKNOWN")),
        ):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(EmbeddingBoundaryError):
                    vector_candidate_from_row(database_row(**{field: value}), vector_request())

    def test_unbounded_or_invalid_database_distance_fails_closed(self) -> None:
        for value in ("NaN", "Infinity", "-1", "1e999999"):
            with self.subTest(value=value):
                with self.assertRaises(EmbeddingBoundaryError) as caught:
                    vector_candidate_from_row(
                        database_row(vector_distance=value),
                        vector_request(),
                    )
                self.assertIs(
                    caught.exception.reason_code,
                    Step19ReasonCode.SCHEMA_UNSUPPORTED,
                )

    def test_wrong_model_candidate_is_rejected(self) -> None:
        with self.assertRaisesRegex(EmbeddingBoundaryError, "MODEL_IDENTITY_INVALID"):
            vector_candidate_from_row(database_row(embedding_model_digest="4" * 64), vector_request())

    def test_public_shared_candidate_remains_eligible(self) -> None:
        candidate = vector_candidate_from_row(database_row(), vector_request())
        self.assertEqual(candidate.access_class, SourceAccessClass.PUBLIC)
        self.assertEqual(candidate.publication_state, SourcePublicationState.PUBLISHED)
        self.assertEqual(candidate.authority_level, SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)

    def test_prefix_index_is_not_authorization(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("tenant_id,\n    hat_scope_id,\n    embedding vector_l2_ops", sql)
        repository = (EMBEDDING_ROOT / "repository.py").read_text(encoding="utf-8")
        self.assertIn("trusted_scope_prefix(request)", repository)


class GenerationAndVectorServiceTests(unittest.TestCase):
    def test_batch_generation_is_deterministic_and_uses_passage_prefix(self) -> None:
        sources = (
            embedding_source(chunk_id="chunk-1", chunk_ordinal=0),
            embedding_source(chunk_id="chunk-2", chunk_ordinal=1),
        )
        backend = FakeBackend()
        repository = FakeRepository(sources=sources)
        cache = fake_cache()
        runner, patcher = patched_runner()
        try:
            result = EmbeddingGenerationService(runner, backend, cache, repository).generate(generation_request(batch_size=1))
            self.assertEqual(tuple(record.chunk_id for record in result.records), ("chunk-1", "chunk-2"))
            self.assertTrue(all(batch[0].startswith(APPROVED_PASSAGE_PREFIX) for batch in backend.passages))
            self.assertTrue(all(not batch[0].startswith(APPROVED_QUERY_PREFIX) for batch in backend.passages))
        finally:
            patcher.stop()

    def test_generation_exact_replay_uses_cache_without_model(self) -> None:
        source = embedding_source()
        vector = FakeBackend._vector(prepare_passage(source.content, SPEC))
        record = embedding_record(vector, source)
        repository = FakeRepository(sources=(source,))
        repository.records[(source.chunk_id, SPEC.model_digest)] = record
        cache = fake_cache()
        cache.read.return_value = vector  # type: ignore[attr-defined]
        backend = FakeBackend()
        runner, patcher = patched_runner()
        try:
            result = EmbeddingGenerationService(runner, backend, cache, repository).generate(generation_request())
            self.assertEqual((result.cache_hits, result.generated_count), (1, 0))
            self.assertEqual(backend.passages, [])
        finally:
            patcher.stop()

    def test_conflicting_replay_fails_closed(self) -> None:
        source = embedding_source()
        repository = FakeRepository(sources=(source,))
        repository.records[(source.chunk_id, SPEC.model_digest)] = embedding_record(source=source, content_sha256=DIGEST_A)
        runner, patcher = patched_runner()
        try:
            with self.assertRaisesRegex(EmbeddingBoundaryError, "EMBEDDING_RECORD_CONFLICT"):
                EmbeddingGenerationService(runner, FakeBackend(), fake_cache(), repository).generate(generation_request())
        finally:
            patcher.stop()

    def test_batch_and_item_limits_are_enforced(self) -> None:
        for field, value, reason in (("batch_size", 65, "BATCH_LIMIT_EXCEEDED"), ("maximum_items", 10001, "ITEM_LIMIT_EXCEEDED")):
            with self.subTest(field=field), self.assertRaisesRegex(EmbeddingBoundaryError, reason):
                generation_request(**{field: value})

    def test_model_inference_occurs_between_short_database_calls(self) -> None:
        source = embedding_source()
        backend = FakeBackend()
        repository = FakeRepository(sources=(source,))
        runner, patcher = patched_runner()
        try:
            EmbeddingGenerationService(runner, backend, fake_cache(), repository).generate(generation_request())
            operations = [call.kwargs["operation_kind"] for call in runner.run.call_args_list]  # type: ignore[attr-defined]
            self.assertEqual(operations, ["STEP19_SELECT_EMBEDDING_SOURCES", "STEP19_FIND_EMBEDDING_RECORD", "STEP19_INSERT_EMBEDDING_RECORD"])
            self.assertEqual(len(backend.passages), 1)
        finally:
            patcher.stop()

    def test_query_uses_query_prefix_and_remains_ephemeral(self) -> None:
        backend = FakeBackend()
        runner, patcher = patched_runner()
        try:
            result = VectorRetrievalService(runner, backend, FakeRepository()).retrieve(vector_request())
            self.assertEqual(result.reason_codes, (Step19ReasonCode.NO_MATCH,))
            self.assertEqual(backend.queries, [backend.queries[0]])
            self.assertTrue(backend.queries[0].startswith(APPROVED_QUERY_PREFIX))
            self.assertNotIn("query_text", result.__dataclass_fields__)
        finally:
            patcher.stop()

    def test_nearest_candidate_order_and_tie_break_are_deterministic(self) -> None:
        first = vector_candidate_from_row(database_row(chunk_id="chunk-b", chunk_ordinal="1", vector_distance="0.20000000"), vector_request())
        nearer = vector_candidate_from_row(database_row(chunk_id="chunk-a", chunk_ordinal="2", vector_distance="0.10000000"), vector_request())
        tied = vector_candidate_from_row(database_row(chunk_id="chunk-c", chunk_ordinal="3", vector_distance="0.20000000"), vector_request())
        runner, patcher = patched_runner()
        try:
            result = VectorRetrievalService(runner, FakeBackend(), FakeRepository(candidates=(tied, first, nearer))).retrieve(vector_request())
            self.assertEqual(tuple(item.chunk_id for item in result.candidates), ("chunk-a", "chunk-b", "chunk-c"))
        finally:
            patcher.stop()

    def test_result_limit_and_query_byte_limit_are_enforced(self) -> None:
        with self.assertRaisesRegex(EmbeddingBoundaryError, "RESULT_LIMIT_EXCEEDED"):
            vector_request(maximum_results=101)
        for text in ("", "x" * 4097):
            with self.assertRaisesRegex(EmbeddingBoundaryError, "QUERY_TOO_LARGE"):
                vector_request(query_text=text)

    def test_wrong_tenant_hat_scope_and_private_owner_fail_closed(self) -> None:
        cases = (
            database_row(tenant_id="tenant-other"),
            database_row(hat_scope_id="hat-other"),
        )
        for row in cases:
            candidate = vector_candidate_from_row(row, vector_request())
            runner, patcher = patched_runner()
            try:
                with self.assertRaises(EmbeddingBoundaryError):
                    VectorRetrievalService(runner, FakeBackend(), FakeRepository(candidates=(candidate,))).retrieve(vector_request())
            finally:
                patcher.stop()

    def test_vector_similarity_cannot_upgrade_authority(self) -> None:
        with self.assertRaisesRegex(EmbeddingBoundaryError, "SOURCE_NOT_ELIGIBLE"):
            vector_candidate_from_row(database_row(authority_level="DERIVED", vector_distance="0.00000000"), vector_request())


class IntegrityAndMigrationTests(unittest.TestCase):
    def test_database_record_hash_is_verified_on_replay(self) -> None:
        record = record_from_row(embedding_record_row())
        verify_embedding_record_hash(record)
        with self.assertRaises(EmbeddingBoundaryError) as caught:
            record_from_row(embedding_record_row(record_hash=DIGEST_C))
        self.assertIs(
            caught.exception.reason_code,
            Step19ReasonCode.EMBEDDING_RECORD_CONFLICT,
        )

    def test_embedding_record_hash_is_deterministic_and_tamper_detected(self) -> None:
        first = embedding_record()
        second = embedding_record()
        self.assertEqual(first.record_hash, second.record_hash)
        object.__setattr__(first, "chunk_id", "tampered")
        with self.assertRaises(IntegrityError):
            verify_embedding_record_hash(first)

    def test_vector_candidate_hash_uses_decimal_not_float(self) -> None:
        candidate = vector_candidate_from_row(database_row(vector_distance=0.5), vector_request())
        self.assertEqual(candidate.vector_distance, "0.5")
        self.assertIsInstance(candidate.vector_distance, str)
        object.__setattr__(candidate, "chunk_id", "tampered")
        with self.assertRaises(IntegrityError):
            verify_vector_candidate_hash(candidate)

    def test_migration_pins_vector_dimension_indexes_and_lineage(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        for token in (
            "CREATE TABLE memory_patch.chunk_embeddings",
            "embedding VECTOR(384) NOT NULL",
            "CHECK (embedding_dimension = 384)",
            "chunk_embeddings_chunk_fk",
            "CREATE VECTOR INDEX chunk_embeddings_vector_l2_idx",
            "embedding vector_l2_ops",
            "CREATE INDEX chunk_embeddings_model_lookup_idx",
        ):
            self.assertIn(token, sql)

    def test_migration_enables_and_forces_rls_with_scoped_policies(self) -> None:
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertEqual(sql.count("hat_scope_context_matches(tenant_id, hat_scope_id)"), 2)
        self.assertNotIn("BYPASSRLS", sql)
        self.assertNotIn("TO PUBLIC", sql)

    def test_embedding_insert_is_only_owned_mutation(self) -> None:
        tree = ast.parse((EMBEDDING_ROOT / "repository.py").read_text(encoding="utf-8"))
        strings = "\n".join(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)).upper()
        self.assertIn("INSERT INTO MEMORY_PATCH.CHUNK_EMBEDDINGS", strings)
        for forbidden in ("UPDATE MEMORY_PATCH.KNOWLEDGE", "DELETE FROM ", "TRUNCATE TABLE", "ALTER TABLE", "DROP TABLE"):
            self.assertNotIn(forbidden, strings)

    def test_manifest_declares_exact_step19_checksum(self) -> None:
        manifest = json.loads((ROOT / "sql/cockroachdb/migrations/manifest.json").read_text())
        step19 = next(
            value
            for value in manifest["migrations"]
            if value["migration_id"] == "0010_step19_embedding_vector_retrieval"
        )
        self.assertEqual(
            step19["sha256"], hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
        )


class StepBoundaryTests(unittest.TestCase):
    def test_validation_evidence_is_canonical_hash_bound_and_passed(self) -> None:
        raw = EVIDENCE_PATH.read_bytes()
        evidence = json.loads(raw)
        self.assertEqual(
            raw,
            (json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        claimed = evidence.pop("validation_digest")
        self.assertEqual(claimed, canonical_sha256(evidence))
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["model"]["weight_sha256"], APPROVED_WEIGHT_SHA256)
        self.assertEqual(evidence["generation"]["database_vector_round_trip"], "PASS")
        self.assertEqual(
            evidence["database"]["explain_digest"],
            canonical_sha256(evidence["database"]["explain_summary"]),
        )
        self.assertTrue(
            evidence["database"]["explain_summary"]["query_uses_l2_operator"]
        )
        self.assertTrue(evidence["cleanup"]["pid_exited"])
        self.assertTrue(evidence["cleanup"]["ports_closed"])
        self.assertFalse(evidence["boundaries"]["step20_started"])

    def test_step19_documents_freeze_the_step20_boundary(self) -> None:
        for path in STEP19_DOCUMENTS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("Step 19", text, path)
            self.assertIn("Step 20", text, path)
            self.assertRegex(
                text,
                r"(?i)(?:NOT STARTED|defer|out of scope|does not implement|Step 20 owns|implementation: absent)",
            )

    def test_ordinary_import_does_not_load_heavy_runtime(self) -> None:
        init_tree = ast.parse((EMBEDDING_ROOT / "__init__.py").read_text(encoding="utf-8"))
        imports = {node.module for node in ast.walk(init_tree) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn(".local_e5", imports)

    def test_no_remote_model_provider_network_or_cloud_imports(self) -> None:
        forbidden = {"requests", "urllib", "httpx", "socket", "boto3", "openai"}
        for path in EMBEDDING_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertTrue(imported.isdisjoint(forbidden), path)

    def test_no_hybrid_reranker_or_final_evidence_bundle(self) -> None:
        public = set(__import__("aioa_memory_kernel.embeddings", fromlist=["*"]).__all__)
        for forbidden in ("HybridRetriever", "Reranker", "EvidenceBundle", "ReciprocalRankFusion"):
            self.assertNotIn(forbidden, public)
        source = "\n".join(path.read_text(encoding="utf-8") for path in EMBEDDING_ROOT.glob("*.py"))
        self.assertNotIn("reciprocal_rank", source)
        self.assertNotIn("context_budget", source)

    def test_step18_contracts_and_modes_remain_unchanged(self) -> None:
        from aioa_memory_kernel.retrieval import RetrievalMode

        self.assertEqual(
            tuple(item.value for item in RetrievalMode),
            ("EXACT_IDENTIFIER", "STATUTE_SECTION", "FULL_TEXT", "KEYWORD"),
        )
        self.assertNotIn("VECTOR", {item.value for item in RetrievalMode})

    def test_shared_trusted_scope_is_used_by_lexical_and_vector_paths(self) -> None:
        lexical = (RETRIEVAL_ROOT / "repository.py").read_text(encoding="utf-8")
        vector = (EMBEDDING_ROOT / "repository.py").read_text(encoding="utf-8")
        self.assertIn("trusted_scope_prefix(request)", lexical)
        self.assertIn("trusted_scope_prefix(request)", vector)

    def test_model_output_has_no_authority_fields(self) -> None:
        fields = set(EmbeddingVector.__dataclass_fields__)
        for forbidden in ("tenant_id", "hat_scope_id", "authority_level", "publication_state", "route_hash"):
            self.assertNotIn(forbidden, fields)


if __name__ == "__main__":
    unittest.main()
