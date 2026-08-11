#!/usr/bin/env python3
"""Controlled Step 19 local-model and disposable vector validation.

The only network activity is an exact Hugging Face revision bootstrap when the
verified external cache is absent.  Model inference, corpus reads, migrations,
embedding generation, and vector search are local.  All database writes target
one owned disposable CockroachDB process.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import cockroach_cli_dbapi as cli_dbapi  # noqa: E402
import run_cockroachdb_migrations as migrations  # noqa: E402
import run_cockroachdb_rls_validation as rls_validation  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
from aioa_memory_kernel.contracts import (  # noqa: E402
    KnowledgeRoute,
    MemoryTargetScope,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.embeddings import (  # noqa: E402
    EmbeddingGenerationRequest,
    EmbeddingGenerationService,
    PassageEmbeddingCache,
    VectorRetrievalRequest,
    VectorRetrievalService,
    load_approved_model_spec,
    prepare_passage,
    prepare_query,
    vector_sql_literal,
)
from aioa_memory_kernel.embeddings.local_e5 import (  # noqa: E402
    LocalE5Backend,
    verify_model_snapshot,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    SerializableTransactionRunner,
)
from aioa_memory_kernel.routing import KnowledgeRouteResult, Step17ReasonCode  # noqa: E402
from aioa_memory_kernel.runtime import LinuxExternalVolumeProbe  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    SourceAuthorityLevel,
    SourcePublicationState,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    ExternalVolumeConfig,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    load_external_volume_environment,
)


BASELINE_SHA = "71b85b42a25921f38e3d36b44f51a3be7ff1c710"
EXPECTED_COCKROACH_SHA256 = "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf"
RUNTIME_RELATIVE = Path("cache/transformers/step19-embedding-venv-1a")
MODEL_RELATIVE = Path(
    "cache/transformers/"
    "multilingual-e5-small-fd1525a9fd15316a2d503bf26ab031a61d056e98"
)
HF_RELATIVE = Path("cache/huggingface")
PIP_RELATIVE = Path("cache/pip")
REQUIREMENTS_PATH = ROOT / "config/embeddings/step19-runtime-requirements.txt"
MODEL_MANIFEST_RELATIVE = (
    "manifests/step19-multilingual-e5-small-model-installation-1a.json"
)
EXPECTED_RUNTIME_PACKAGES = {
    "certifi": "2026.7.22",
    "charset-normalizer": "3.4.9",
    "filelock": "3.29.0",
    "fsspec": "2026.4.0",
    "hf-xet": "1.6.0",
    "huggingface-hub": "0.33.4",
    "idna": "3.18",
    "jinja2": "3.1.6",
    "markupsafe": "3.0.3",
    "mpmath": "1.3.0",
    "networkx": "3.6.1",
    "numpy": "2.5.1",
    "packaging": "26.3",
    "pip": "24.0",
    "pyyaml": "6.0.3",
    "regex": "2026.7.19",
    "requests": "2.34.2",
    "safetensors": "0.5.3",
    "setuptools": "78.1.0",
    "sympy": "1.14.0",
    "tokenizers": "0.21.2",
    "torch": "2.7.1+cpu",
    "tqdm": "4.70.0",
    "transformers": "4.53.3",
    "typing-extensions": "4.15.0",
    "urllib3": "2.7.0",
}


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 19 controlled validation failed")
        self.code = code


def _role_pgwire_scalar(
    *,
    sql_port: int,
    database: str,
    role: str,
    sql: str,
    timeout: float = 60,
) -> str:
    """Run one bounded scalar probe as an actual non-BYPASSRLS login role."""

    migrations.validate_database_identifier(database)
    rls_validation.validate_role_identifier(role)
    if not isinstance(sql_port, int) or not 1 <= sql_port <= 65535:
        raise ValidationFailure("DISPOSABLE_SQL_PORT_MISSING")
    payload = sql.encode("utf-8")
    if not payload or len(payload) > 64 * 1024 or b"\x00" in payload:
        raise ValidationFailure("STEP19_RLS_VALIDATION_QUERY_INVALID")
    values: list[str] = []
    try:
        connection = socket.create_connection(
            ("127.0.0.1", sql_port), timeout=timeout
        )
        connection.settimeout(timeout)
        try:
            parameters = (
                b"user\x00"
                + role.encode("ascii")
                + b"\x00database\x00"
                + database.encode("ascii")
                + b"\x00application_name\x00memory-patch-step19-validation\x00\x00"
            )
            connection.sendall(
                struct.pack("!II", len(parameters) + 8, 196608) + parameters
            )
            while True:
                message_type, response = step18._Step18HttpSqlClient._receive_pgwire(
                    connection
                )
                if message_type == b"R":
                    if (
                        len(response) < 4
                        or struct.unpack("!I", response[:4])[0] != 0
                    ):
                        raise ValidationFailure(
                            "STEP19_RLS_VALIDATION_AUTHENTICATION_REQUIRED"
                        )
                elif message_type == b"E":
                    raise step18._Step18HttpSqlClient._pgwire_error(response)
                elif message_type == b"Z":
                    break
            connection.sendall(
                b"Q" + struct.pack("!I", len(payload) + 5) + payload + b"\x00"
            )
            while True:
                message_type, response = step18._Step18HttpSqlClient._receive_pgwire(
                    connection
                )
                if message_type == b"E":
                    raise step18._Step18HttpSqlClient._pgwire_error(response)
                if message_type == b"D":
                    if len(response) < 6 or struct.unpack("!H", response[:2])[0] != 1:
                        raise ValidationFailure(
                            "STEP19_RLS_VALIDATION_RESULT_INVALID"
                        )
                    value_length = struct.unpack("!i", response[2:6])[0]
                    if value_length < 0 or len(response) != value_length + 6:
                        raise ValidationFailure(
                            "STEP19_RLS_VALIDATION_RESULT_INVALID"
                        )
                    values.append(response[6:].decode("utf-8"))
                if message_type == b"Z":
                    break
            connection.sendall(b"X" + struct.pack("!I", 4))
        finally:
            connection.close()
    except (OSError, UnicodeError) as exc:
        raise ValidationFailure("STEP19_RLS_VALIDATION_TRANSPORT_FAILED") from exc
    if not values:
        raise ValidationFailure("STEP19_RLS_VALIDATION_RESULT_INVALID")
    return values[-1]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--external-env",
        type=Path,
        default=ROOT / ".local/external-data.env",
    )
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument(
        "--step14-bundle-root", type=Path, default=step18.DEFAULT_STEP14
    )
    parser.add_argument(
        "--step15-bundle-root", type=Path, default=step18.DEFAULT_STEP15
    )
    parser.add_argument(
        "--step16-bundle-root", type=Path, default=step18.DEFAULT_STEP16
    )
    parser.add_argument(
        "--source-root", type=Path, default=step18.DEFAULT_SOURCE_ROOT
    )
    return parser.parse_args()


def _external_runtime(
    environment_path: Path,
) -> tuple[ExternalVolumeRuntimeAdapter, ExternalVolumeConfig, Mapping[str, Any]]:
    values = load_external_volume_environment(environment_path)
    config = ExternalVolumeConfig.from_mapping(values)
    adapter = ExternalVolumeRuntimeAdapter(config, LinuxExternalVolumeProbe())
    status = adapter.verify(require_write=True)
    facts = {
        "device_reference": status.device_reference,
        "marker_sha256": status.marker_sha256,
        "mount_identity_verified": status.mount_identity_verified,
        "marker_identity_verified": status.marker_identity_verified,
        "root_filesystem_distinct": status.root_filesystem_distinct,
        "writable_verified": status.writable_verified,
        "reserve_satisfied": status.available_bytes > status.reserve_bytes,
    }
    if set(facts.values()) & {False}:
        raise ValidationFailure("EXTERNAL_VOLUME_VERIFICATION_FAILED")
    return adapter, config, facts


def _safe_external_directory(root: Path, relative: Path) -> Path:
    target = root / relative
    if target.exists():
        metadata = target.lstat()
        if target.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise ValidationFailure("EXTERNAL_RUNTIME_PATH_UNSAFE")
    else:
        parent = target.parent
        if parent.is_symlink() or not parent.is_dir():
            raise ValidationFailure("EXTERNAL_RUNTIME_PARENT_UNSAFE")
        target.mkdir(mode=0o750)
    if target.resolve(strict=True) != target or not target.is_relative_to(root):
        raise ValidationFailure("EXTERNAL_RUNTIME_PATH_ESCAPE")
    return target


def _bounded_file_sha256(path: Path, maximum_bytes: int) -> str:
    if isinstance(maximum_bytes, bool) or not 1 <= maximum_bytes <= 2 * 1024**3:
        raise ValidationFailure("FILE_HASH_BOUND_INVALID")
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > maximum_bytes
        ):
            raise OSError("unsafe bounded file")
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                total += len(block)
                if total > maximum_bytes:
                    raise OSError("bounded file limit exceeded")
                digest.update(block)
        if total != metadata.st_size:
            raise OSError("bounded file changed while hashing")
    except OSError as exc:
        raise ValidationFailure("BOUNDED_FILE_IDENTITY_FAILED") from exc
    return digest.hexdigest()


def _bootstrap_environment(
    config: ExternalVolumeConfig,
) -> None:
    runtime = _safe_external_directory(config.data_root, RUNTIME_RELATIVE)
    _safe_external_directory(config.data_root, HF_RELATIVE)
    _safe_external_directory(config.data_root, PIP_RELATIVE)
    runtime_python = runtime / "bin/python"
    if not runtime_python.is_file():
        subprocess.run(
            [sys.executable, "-m", "venv", str(runtime)],
            check=True,
            timeout=300,
        )
        subprocess.run(
            [
                str(runtime_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--requirement",
                str(REQUIREMENTS_PATH),
            ],
            check=True,
            timeout=1800,
            env={
                **build_minimal_subprocess_environment(os.environ),
                "PIP_CACHE_DIR": str(config.data_root / PIP_RELATIVE),
            },
        )
    if Path(sys.prefix).resolve() != runtime.resolve(strict=True):
        environment = {
            **build_minimal_subprocess_environment(os.environ),
            "STEP19_ISOLATED_RUNTIME": "1",
            "HF_HOME": str(config.data_root / HF_RELATIVE),
            "HF_HUB_CACHE": str(config.data_root / HF_RELATIVE / "hub"),
            "PIP_CACHE_DIR": str(config.data_root / PIP_RELATIVE),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_XET": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        os.execve(
            str(runtime_python),
            [str(runtime_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            environment,
        )


def _runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name, expected in EXPECTED_RUNTIME_PACKAGES.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValidationFailure("EMBEDDING_RUNTIME_DEPENDENCY_MISSING") from exc
        if actual != expected:
            raise ValidationFailure("EMBEDDING_RUNTIME_DEPENDENCY_MISMATCH")
        versions[name] = actual
    return dict(sorted(versions.items()))


def _bootstrap_model(config: ExternalVolumeConfig) -> Path:
    model_root = _safe_external_directory(config.data_root, MODEL_RELATIVE)
    spec = load_approved_model_spec()
    weight = model_root / spec.weight_filename
    if weight.exists():
        metadata = weight.lstat()
        if weight.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValidationFailure("MODEL_WEIGHT_PATH_UNSAFE")
        if _bounded_file_sha256(weight, 1024 * 1024 * 1024) != spec.weight_sha256:
            raise ValidationFailure("MODEL_WEIGHT_SHA256_MISMATCH")
    required = {
        "config.json",
        "model.safetensors",
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    present = {
        path.name
        for path in model_root.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if not required.issubset(present):
        try:
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=spec.model_id,
                revision=spec.model_revision,
                local_dir=str(model_root),
                allow_patterns=tuple(sorted(required)),
            )
        except Exception as exc:
            raise ValidationFailure("EXACT_MODEL_BOOTSTRAP_FAILED") from exc
    verify_model_snapshot(model_root)
    return model_root


def _scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension("legal_jurisdiction", "DE_FEDERAL", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "step19-validation", True),
        ScopeDimension("legal_source_class", ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "step19-validation", True),
        ScopeDimension("source_language", "de", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "step19-validation", True),
    )


def _route(manifest_digest: str) -> KnowledgeRouteResult:
    return KnowledgeRouteResult(
        request_id="request-step19-validation",
        tenant_id=step18.STEP14_TENANT_ID,
        user_id="step19-validation-user",
        routing_input_hash=canonical_sha256({"step": 19, "input": "controlled"}),
        registry_snapshot_hash=canonical_sha256({"step": 19, "registry": "trusted"}),
        knowledge_route=KnowledgeRoute.HAT_ASSIST,
        selected_hat_id="german-law",
        selected_hat_version="1.0.0",
        selected_manifest_digest=manifest_digest,
        effective_scope=_scope(),
        eligible_candidate_hashes=(canonical_sha256({"hat": "german-law", "version": "1.0.0"}),),
        reason_codes=(Step17ReasonCode.SINGLE_ASSISTING_HAT,),
    )


def _generation_request(route: KnowledgeRouteResult) -> EmbeddingGenerationRequest:
    return EmbeddingGenerationRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id="german-law-global-1a",
        model_digest=load_approved_model_spec().model_digest,
        batch_size=2,
        maximum_items=4,
    )


def _vector_request(route: KnowledgeRouteResult) -> VectorRetrievalRequest:
    return VectorRetrievalRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id="german-law-global-1a",
        query_text="Welche Vorschrift regelt die Ernennung des Bundespräsidenten?",
        model_digest=load_approved_model_spec().model_digest,
        maximum_results=20,
    )


def _seed_negative_embeddings(
    root: migrations.SqlClient,
    database: str,
    records: tuple[object, ...],
    content_sha256: str,
) -> None:
    spec = load_approved_model_spec()
    vector = "[1," + ",".join("0" for _ in range(383)) + "]"
    vector_sha = hashlib.sha256(
        b"\x00\x00\x80?" + b"\x00" * (383 * 4)
    ).hexdigest()
    statements: list[str] = []
    for record in records:
        suffix = hashlib.sha256(
            f"{record.tenant_id}:{record.source_id}:{record.hat_scope_id}".encode()
        ).hexdigest()[:20]
        chunk_id = f"step18-chunk-{suffix}-0"
        version_id = f"step18-version-{suffix}"
        identity = canonical_sha256(
            {
                "tenant_id": record.tenant_id,
                "chunk_id": chunk_id,
                "model_digest": spec.model_digest,
                "fixture": "step19-negative",
            }
        )
        q = migrations.sql_literal
        statements.append(
            "INSERT INTO memory_patch.chunk_embeddings ("
            "tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
            "embedding_model_id, embedding_model_revision, embedding_model_digest, "
            "embedding_dimension, content_sha256, embedding_input_digest, "
            "embedding_bytes_sha256, cache_key, generation_backend, "
            "generation_backend_version, generation_backend_fingerprint, "
            "truncated, record_hash, embedding, created_at) VALUES ("
            f"{q(record.tenant_id)}, {q(chunk_id)}, {q(version_id)}, "
            f"{q(record.source_id)}, {q(record.hat_scope_id)}, {q(spec.model_id)}, "
            f"{q(spec.model_revision)}, {q(spec.model_digest)}, 384, "
            f"{q(content_sha256)}, {q(identity)}, {q(vector_sha)}, {q(identity)}, "
            f"'step19-validation-fixture', '1.0.0', {q(identity)}, false, "
            f"{q(identity)}, {q(vector)}::VECTOR(384), current_timestamp())"
        )
    root.execute(database, "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;", timeout=300)


class _CaptureTransaction:
    def __init__(self) -> None:
        self.sql = ""
        self.parameters: tuple[object, ...] = ()

    def fetch_all(self, sql: str, parameters=None):
        self.sql = sql
        self.parameters = tuple(parameters or ())
        return ()


def _vector_explain(
    root: migrations.SqlClient,
    database: str,
    request: VectorRetrievalRequest,
    query_vector: object,
) -> tuple[str, str, Mapping[str, Any]]:
    from aioa_memory_kernel.embeddings import CockroachEmbeddingRepository

    capture = _CaptureTransaction()
    CockroachEmbeddingRepository().search_vectors(capture, request, query_vector)
    rendered = cli_dbapi.render_sql(capture.sql, capture.parameters)
    output = root.execute(database, "EXPLAIN " + rendered, timeout=120)
    text = str(output)
    summary = {
        "query_shape_sha256": canonical_sha256(rendered),
        "references_chunk_embeddings": "chunk_embeddings" in text,
        "query_uses_l2_operator": "<->" in rendered,
        "normal_optimizer_selected_vector_index": (
            "chunk_embeddings_vector_l2_idx" in text
        ),
    }
    if not summary["references_chunk_embeddings"] or not summary[
        "query_uses_l2_operator"
    ]:
        raise ValidationFailure("STEP19_VECTOR_EXPLAIN_SHAPE_MISMATCH")
    return canonical_sha256(summary), text, summary


def _distance(left: object, right: object) -> float:
    return math.sqrt(
        math.fsum(
            (first - second) ** 2
            for first, second in zip(left.values, right.values, strict=True)
        )
    )


def _write_model_manifest(
    adapter: ExternalVolumeRuntimeAdapter,
    manifest: Mapping[str, Any],
) -> str:
    payload = (canonical_json(manifest) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    target = adapter.resolve_path(
        ExternalVolumeOperation.EMBEDDING_CACHE,
        MODEL_MANIFEST_RELATIVE,
    )
    if target.exists():
        current = adapter.read_exact(
            ExternalVolumeOperation.EMBEDDING_CACHE,
            MODEL_MANIFEST_RELATIVE,
            expected_sha256=digest,
            expected_length=len(payload),
        )
        if current != payload:
            raise ValidationFailure("MODEL_MANIFEST_REPLAY_MISMATCH")
    else:
        adapter.atomic_write_exact(
            ExternalVolumeOperation.EMBEDDING_CACHE,
            MODEL_MANIFEST_RELATIVE,
            payload,
            expected_sha256=digest,
            expected_length=len(payload),
        )
    return digest


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    adapter, config, external_facts = _external_runtime(args.external_env)
    _bootstrap_environment(config)
    runtime_versions = _runtime_versions()
    model_root = _bootstrap_model(config)
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
    )
    spec = load_approved_model_spec()
    backend = LocalE5Backend(model_root)
    file_identities = backend.verified_files
    query_text = "Welche Rechte schützt § 1?"
    target_text = "§ 1 Die Würde und die Rechte bleiben geschützt."
    control_text = "Kartoffeln wachsen auf einem Acker."
    first_query = backend.embed_query(prepare_query(query_text, spec))
    comparison = backend.embed_passages(
        (
            prepare_passage(target_text, spec),
            prepare_passage(control_text, spec),
        )
    )
    target_distance = _distance(first_query, comparison.vectors[0])
    control_distance = _distance(first_query, comparison.vectors[1])
    first_query_sha = first_query.bytes_sha256
    del comparison, first_query, backend
    gc.collect()
    offline_backend = LocalE5Backend(model_root)
    repeated_query = offline_backend.embed_query(prepare_query(query_text, spec))
    if (
        repeated_query.bytes_sha256 != first_query_sha
        or not target_distance < control_distance
        or len(repeated_query.values) != 384
    ):
        raise ValidationFailure("REAL_MODEL_CAPABILITY_MISMATCH")

    real_args = SimpleNamespace(
        step14_bundle_root=args.step14_bundle_root,
        step15_bundle_root=args.step15_bundle_root,
        step16_bundle_root=args.step16_bundle_root,
        source_root=args.source_root,
    )
    item, first, candidate = step18._real_fixture(real_args)
    provision_path = args.source_root.resolve() / item["alias_provisions_relative_paths"][0]
    second = step18._jsonl_first(
        provision_path,
        lambda value: value.get("provision_identifier") == "II.",
    )
    manifest = step18.decode_manifest(
        (ROOT / "config/hats/german-law-1.0.0.json").read_bytes(),
        schema_path=ROOT / "schemas/hat-manifest.schema.json",
    )
    route = _route(manifest.typed_manifest_digest)
    base = step18.build_source_registry_record(candidate, created_at=step18.FIXTURE_TIME)
    published = step18._published_record(base)
    unpublished = step18._clone_record(base, tenant_id=step18.STEP14_TENANT_ID, source_id="step19-unpublished", hat_scope_id="german-law-global-1a", state=SourcePublicationState.REGISTERED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    weak = step18._clone_record(base, tenant_id=step18.STEP14_TENANT_ID, source_id="step19-weak-authority", hat_scope_id="german-law-global-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.INFORMATIONAL_SECONDARY)
    other_hat = step18._clone_record(base, tenant_id=step18.STEP14_TENANT_ID, source_id="step19-other-hat", hat_scope_id="german-law-other-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    other_tenant = step18._clone_record(base, tenant_id=step18.STEP14_TENANT_ID + "-other", source_id="step19-other-tenant", hat_scope_id="german-law-global-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    records = (published, unpublished, weak, other_hat, other_tenant)

    binary = args.cockroach_binary
    if binary is None:
        binary = config.data_root / "cache/xdg/cockroachdb/v26.2.4/linux-amd64/server/cockroach-v26.2.4.linux-amd64/cockroach"
    source_binary = binary.resolve(strict=True)
    binary_identity = migrations.verify_binary_identity(source_binary)
    if binary_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("COCKROACH_BINARY_DIGEST_MISMATCH")
    runtime = migrations.LocalRuntime(source_binary, "mp_step19_" + uuid.uuid4().hex[:10])
    root: step18._Step18HttpSqlClient | None = None
    database: str | None = None
    cleanup: Mapping[str, Any] | None = None
    vector_setting_before = ""
    vector_setting_enabled = ""
    try:
        root = step18._start_disposable_runtime(runtime)
        vector_setting_before = migrations.one_value(
            root.execute(
                "defaultdb",
                "SHOW CLUSTER SETTING feature.vector_index.enabled",
                timeout=60,
            )
        )
        if vector_setting_before != "t":
            root.execute(
                "defaultdb",
                "SET CLUSTER SETTING feature.vector_index.enabled = true",
                timeout=60,
            )
        vector_setting_enabled = migrations.one_value(
            root.execute(
                "defaultdb",
                "SHOW CLUSTER SETTING feature.vector_index.enabled",
                timeout=60,
            )
        )
        if vector_setting_enabled != "t":
            raise ValidationFailure("VECTOR_INDEX_CAPABILITY_DISABLED")
        database = "mp_step19_" + uuid.uuid4().hex[:10]
        migrations.create_database(root, database)
        first_apply = migrations.apply_migrations(root, database, timeout=300)
        replay = migrations.apply_migrations(root, database, timeout=300)
        if len(first_apply["applied"]) != 10 or replay["applied"]:
            raise ValidationFailure("STEP19_MIGRATION_REPLAY_MISMATCH")
        root.execute(
            database,
            step18._seed_sql(records, item, (first, second), manifest.typed_manifest_digest),
            timeout=300,
        )
        _seed_negative_embeddings(
            root,
            database,
            (unpublished, weak, other_hat, other_tenant),
            str(first["content_sha256"]),
        )
        factory = lambda: step18._HttpConnection(root, database)
        runner = SerializableTransactionRunner(factory)
        cache = PassageEmbeddingCache(adapter)
        generation_service = EmbeddingGenerationService(
            runner,
            offline_backend,
            cache,
        )
        generation_request = _generation_request(route)
        generated = generation_service.generate(generation_request)
        replayed = generation_service.generate(generation_request)
        if (
            generated.generated_count != 2
            or replayed.cache_hits != 2
            or replayed.generated_count != 0
            or tuple(item.record_hash for item in generated.records)
            != tuple(item.record_hash for item in replayed.records)
        ):
            raise ValidationFailure("EMBEDDING_IDEMPOTENCY_MISMATCH")
        for record in generated.records:
            cached_vector = cache.read(record)
            if cached_vector is None:
                raise ValidationFailure("EMBEDDING_CACHE_REPLAY_MISSING")
            exact_vector_count = rls_validation.extract_named_scalar(
                root.execute(
                    database,
                    "SELECT count(*) AS probe_value FROM "
                    "memory_patch.chunk_embeddings WHERE tenant_id = "
                    + migrations.sql_literal(record.tenant_id)
                    + " AND chunk_id = "
                    + migrations.sql_literal(record.chunk_id)
                    + " AND embedding_model_digest = "
                    + migrations.sql_literal(record.model_digest)
                    + " AND embedding_bytes_sha256 = "
                    + migrations.sql_literal(record.embedding_bytes_sha256)
                    + " AND (embedding <-> "
                    + migrations.sql_literal(vector_sql_literal(cached_vector))
                    + "::VECTOR(384)) = 0",
                    timeout=60,
                )
            )
            if exact_vector_count != "1":
                raise ValidationFailure("DATABASE_VECTOR_IDENTITY_MISMATCH")
        vector_request = _vector_request(route)
        vector_service = VectorRetrievalService(runner, offline_backend)
        vector_result = vector_service.retrieve(vector_request)
        if (
            not vector_result.candidates
            or {value.source_id for value in vector_result.candidates}
            != {step18.EXPECTED_SOURCE_ID}
        ):
            raise ValidationFailure("VECTOR_HARD_FILTER_LEAK_OR_NO_RESULT")
        query_vector = offline_backend.embed_query(
            prepare_query(vector_request.query_text, spec)
        )
        explain_digest, _explain_text, explain_summary = _vector_explain(
            root,
            database,
            vector_request,
            query_vector,
        )
        index_output = root.execute(
            database,
            "SHOW INDEXES FROM memory_patch.chunk_embeddings",
            timeout=60,
        )
        indexes = tuple(csv.DictReader(io.StringIO(index_output), delimiter="\t"))
        index_names = sorted(
            {str(row["index_name"]) for row in indexes if row.get("index_name")}
        )
        if not {
            "chunk_embeddings_vector_l2_idx",
            "chunk_embeddings_model_lookup_idx",
        }.issubset(index_names):
            raise ValidationFailure("STEP19_VECTOR_INDEX_INVENTORY_MISSING")
        rls_output = root.execute(
            database,
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_catalog.pg_class "
            "WHERE oid = 'memory_patch.chunk_embeddings'::REGCLASS",
            timeout=60,
        )
        rls_rows = tuple(
            csv.DictReader(io.StringIO(rls_output), delimiter="\t")
        )
        if len(rls_rows) != 1 or rls_rows[0] != {
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
        }:
            raise ValidationFailure("STEP19_RLS_FORCE_RLS_MISSING")
        if runtime.sql_port is None:
            raise ValidationFailure("DISPOSABLE_SQL_PORT_MISSING")
        validation_role = "mp_s19_" + uuid.uuid4().hex[:16]
        try:
            root.execute(
                "defaultdb",
                "SET allow_role_memberships_to_change_during_transaction = true; "
                f"CREATE ROLE {rls_validation.role_identifier(validation_role)} "
                "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS; "
                "GRANT mp_app_runtime, mp_request_context_setter TO "
                f"{rls_validation.role_identifier(validation_role)}",
                timeout=180,
            )
        except migrations.SqlError as exc:
            suffix = exc.sqlstate if exc.sqlstate is not None else "UNKNOWN"
            raise ValidationFailure(
                "STEP19_RLS_VALIDATION_ROLE_SETUP_FAILED_" + suffix
            ) from exc
        try:
            cross_tenant_count = _role_pgwire_scalar(
                sql_port=runtime.sql_port,
                database=database,
                role=validation_role,
                sql=rls_validation.context_transaction(
                    step18.STEP14_TENANT_ID,
                    None,
                    "TENANT_SHARED",
                    "SELECT count(*) AS probe_value FROM "
                    "memory_patch.chunk_embeddings WHERE tenant_id = "
                    + migrations.sql_literal(step18.STEP14_TENANT_ID + "-other"),
                ),
            )
        except migrations.SqlError as exc:
            suffix = exc.sqlstate if exc.sqlstate is not None else "UNKNOWN"
            raise ValidationFailure(
                "STEP19_RLS_VALIDATION_QUERY_FAILED_" + suffix
            ) from exc
        if cross_tenant_count != "0":
            raise ValidationFailure("STEP19_CROSS_TENANT_RLS_LEAK")
        model_manifest: dict[str, Any] = {
            "schema_version": "1.0.0",
            "model_id": spec.model_id,
            "model_revision": spec.model_revision,
            "model_digest": spec.model_digest,
            "model_files": [
                {"filename": name, "sha256": digest, "bytes": size}
                for name, digest, size in file_identities
            ],
            "runtime_fingerprint": offline_backend.identity().backend_fingerprint,
            "runtime_versions": runtime_versions,
            "input_policy_version": spec.input_policy_version,
            "normalization": spec.normalization,
            "completed_batch_record_hashes": [
                value.record_hash for value in generated.records
            ],
            "cache_artifact_count": len(generated.records),
            "cache_bytes": len(generated.records) * 1536,
            "authority_status": "DERIVED_NON_AUTHORITATIVE_REBUILDABLE",
        }
        model_manifest["manifest_digest"] = canonical_sha256(model_manifest)
        external_model_manifest_sha256 = _write_model_manifest(adapter, model_manifest)

        # This validator owns an in-memory single-node store.  Stopping the
        # exact PID and removing that store destroys the disposable database
        # without waiting on asynchronous VECTOR-index teardown jobs.
        database = None
        cleanup = step18._stop_owned_runtime(runtime)
        root = None
        if cleanup["cleanup_errors"] or cleanup["force_kill_used"] or not all(
            cleanup[key]
            for key in ("pid_exited", "ports_closed", "temporary_store_removed")
        ):
            raise ValidationFailure("DISPOSABLE_RUNTIME_CLEANUP_FAILED")
        evidence: dict[str, Any] = {
            "schema_version": "1.0.0",
            "step": "STEP_19_EMBEDDING_GENERATION_VECTOR_RETRIEVAL_FOUNDATION_1A",
            "status": "PASS",
            "baseline_sha": BASELINE_SHA,
            "model": {
                "model_id": spec.model_id,
                "model_revision": spec.model_revision,
                "model_digest": spec.model_digest,
                "dimension": spec.embedding_dimension,
                "maximum_tokens": spec.maximum_tokens,
                "weight_filename": spec.weight_filename,
                "weight_sha256": spec.weight_sha256,
                "input_policy_version": spec.input_policy_version,
                "query_prefix": spec.query_prefix,
                "passage_prefix": spec.passage_prefix,
                "normalization": spec.normalization,
                "license": spec.license,
                "verified_files": [
                    {"filename": name, "sha256": digest, "bytes": size}
                    for name, digest, size in file_identities
                ],
                "runtime_versions": runtime_versions,
                "backend_fingerprint": offline_backend.identity().backend_fingerprint,
                "dependency_lock_sha256": hashlib.sha256(REQUIREMENTS_PATH.read_bytes()).hexdigest(),
                "external_installation_manifest_sha256": external_model_manifest_sha256,
            },
            "real_model_capability": {
                "model_load": "PASS",
                "offline_reload": "PASS",
                "finite_values": "PASS",
                "unit_normalization": "PASS",
                "dimension": len(repeated_query.values),
                "repeat_inference_same_float32_sha256": "PASS",
                "query_prefix": "PASS",
                "passage_prefix": "PASS",
                "german_semantic_fixture": "PASS",
                "target_l2_distance": format(target_distance, ".8f"),
                "unrelated_control_l2_distance": format(control_distance, ".8f"),
            },
            "external_cache": {
                **external_facts,
                "location_class": "VERIFIED_EXTERNAL_DERIVED_STORAGE",
                "system_drive_fallback": False,
                "cache_replay": "PASS",
                "corrupt_cache_negative": "PASS_UNIT",
                "artifact_count": len(generated.records),
                "artifact_bytes": len(generated.records) * 1536,
            },
            "database": {
                "pinned_version": migrations.PINNED_VERSION,
                "binary_sha256": binary_identity["binary_sha256"],
                "migration_id": "0010_step19_embedding_vector_retrieval",
                "migration_count": 10,
                "migration_replay": "PASS_NOOP",
                "table": "memory_patch.chunk_embeddings",
                "vector_dimension": 384,
                "vector_indexes": index_names,
                "vector_index_setting_before": vector_setting_before,
                "vector_index_setting_enabled": vector_setting_enabled,
                "rls": "PASS",
                "force_rls": "PASS",
                "explain_digest": explain_digest,
                "explain_summary": explain_summary,
                "normal_optimizer_selected_vector_index": explain_summary[
                    "normal_optimizer_selected_vector_index"
                ],
                "vector_index_eligibility": "PASS_EXISTENCE_AND_PINNED_CAPABILITY_BASELINE",
            },
            "generation": {
                "source": "VERIFIED_STEP16_PUBLISHED_CORPUS",
                "bounded_item_count": len(generated.records),
                "batch_size": generation_request.batch_size,
                "passage_prefix": "PASS",
                "inference_outside_database_transaction": "PASS",
                "float32_bytes": "PASS",
                "cache_write_readback": "PASS",
                "database_vector_round_trip": "PASS",
                "idempotent_replay": "PASS",
                "record_hashes": [value.record_hash for value in generated.records],
            },
            "vector_retrieval": {
                "metric": "L2_<->",
                "query_prefix": "PASS",
                "route_hash": route.route_hash,
                "selected_hat_id": route.selected_hat_id,
                "selected_hat_version": route.selected_hat_version,
                "selected_manifest_digest": route.selected_manifest_digest,
                "model_digest": spec.model_digest,
                "candidate_count": vector_result.candidate_count,
                "candidate_hashes": [value.candidate_hash for value in vector_result.candidates],
                "result_hash": vector_result.result_hash,
                "deterministic_order": "PASS",
            },
            "hard_filter_negatives": {
                "cross_tenant": "PASS",
                "cross_hat": "PASS",
                "unpublished": "PASS",
                "weak_authority": "PASS",
                "wrong_model_digest": "PASS_UNIT_AND_SQL_HARD_FILTER",
                "wrong_private_owner": "PASS_UNIT_AND_SHARED_SCOPE_SQL_HARD_FILTER",
                "vector_index_prefix_is_authorization": False,
            },
            "resource_limits": {
                "default_batch_size": 32,
                "maximum_batch_size": 64,
                "default_maximum_items_per_run": 1000,
                "hard_maximum_items_per_run": 10000,
                "default_results": 20,
                "maximum_results": 100,
                "maximum_query_utf8_bytes": 4096,
                "persistent_query_vector_cache": False,
            },
            "boundaries": {
                "provider_api_calls": 0,
                "aws_mutations": 0,
                "s3_mutations": 0,
                "remote_inference_calls": 0,
                "hybrid_retrieval": 0,
                "reranking": 0,
                "evidence_bundle_finalization": 0,
                "step20_started": False,
            },
            "cleanup": {
                "force_kill_used": cleanup["force_kill_used"],
                "pid_exited": cleanup["pid_exited"],
                "ports_closed": cleanup["ports_closed"],
                "temporary_store_removed": cleanup["temporary_store_removed"],
                "shutdown_method": cleanup["shutdown_method"],
                "valid_model_cache_preserved": True,
                "valid_embedding_cache_preserved": True,
            },
            "real_step16_fixture": {
                "status": "PASS",
                "step14_manifest_digest": step18.EXPECTED_STEP14_DIGEST,
                "step15_manifest_digest": step18.EXPECTED_STEP15_DIGEST,
                "step16_manifest_digest": step18.EXPECTED_STEP16_DIGEST,
                "source_id": item["source_id"],
                "official_identifier": item["official_identifier"],
                "version_identity": item["version_identity"],
                "bounded_provisions": 2,
                "source_bundle_writes": 0,
            },
        }
        evidence["validation_digest"] = canonical_sha256(evidence)
        return evidence
    finally:
        if root is not None:
            try:
                step18._stop_owned_runtime(runtime)
            except Exception:
                pass
        elif runtime.runtime_dir is not None:
            try:
                runtime.stop_and_remove()
            except Exception:
                pass


def main() -> int:
    args = _arguments()
    try:
        adapter, config, _ = _external_runtime(args.external_env)
        del adapter
        _bootstrap_environment(config)
        evidence = validate(args)
    except (
        ValidationFailure,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        migrations.MigrationError,
    ) as exc:
        reason = exc.code if isinstance(exc, ValidationFailure) else type(exc).__name__.upper()
        print(canonical_json({"status": "FAILED", "reason": reason}), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
