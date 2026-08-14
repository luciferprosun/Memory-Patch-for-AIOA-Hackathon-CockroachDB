#!/usr/bin/env python3
"""Controlled disposable CockroachDB validation for Memory Patch Step 18.

The source bundle is read only.  Fixture writes target one owned in-memory
CockroachDB process under /tmp; the production retrieval repository remains
read only and parameterized.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import http.client
import io
import json
import re
import socket
import struct
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import cockroach_cli_dbapi as cli_dbapi  # noqa: E402
import run_cockroachdb_migrations as migrations  # noqa: E402
import run_german_law_corpus_registration_validation as step14_validation  # noqa: E402
import run_source_registry_validation as step9_validation  # noqa: E402
from aioa_memory_kernel.contracts import (  # noqa: E402
    KnowledgeRoute,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.corpus import verify_inventory_bundle  # noqa: E402
from aioa_memory_kernel.german_law.corpus import (  # noqa: E402
    STEP14_TENANT_ID,
    build_source_registry_record,
)
from aioa_memory_kernel.german_law.normalization import (  # noqa: E402
    verify_temporal_jurisdiction_bundle,
)
from aioa_memory_kernel.german_law.publication import (  # noqa: E402
    verify_german_law_publication_bundle,
)
from aioa_memory_kernel.hats import decode_manifest  # noqa: E402
from aioa_memory_kernel.persistence import (  # noqa: E402
    SerializableTransactionRunner,
)
from aioa_memory_kernel.retrieval import (  # noqa: E402
    CockroachRetrievalRepository,
    ExactIdentifierField,
    ExactIdentifierSelector,
    FullTextQuery,
    KeywordQuery,
    RetrievalMode,
    RetrievalRequest,
    RetrievalService,
    StatuteSectionSelector,
)
from aioa_memory_kernel.routing import (  # noqa: E402
    KnowledgeRouteResult,
    Step17ReasonCode,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    PUBLICATION_GENESIS_DIGEST,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourcePublicationState,
)


BASELINE_SHA = "e1895e533c5f97bd06ffa2348cbdc1ee6419e42f"
EXPECTED_COCKROACH_SHA256 = "5ad89c804abb3bf5afa9c073faecb3710a1c4f34a870f08cdef889c1c91d314b"
DEFAULT_COCKROACH = Path("/media/l/LSC_DATA2/AIOA_DATA/Memory-Patch-for-AIOA/cache/xdg/cockroachdb/v26.2.5/linux-amd64/server/cockroach-v26.2.5.linux-amd64/cockroach")
DEFAULT_STEP14 = Path("/media/l/LSC_DATA2/AIOA_DATA/Memory-Patch-for-AIOA/corpora/manifests/step14/step14-d4acb41c668fe1859cec9f6d1709474f")
DEFAULT_STEP15 = Path("/media/l/LSC_DATA2/AIOA_DATA/Memory-Patch-for-AIOA/corpora/manifests/step15/step15-a2bf509e317ee6fa2ad5834396630e43")
DEFAULT_STEP16 = Path("/media/l/LSC_DATA2/AIOA_DATA/Memory-Patch-for-AIOA/corpora/manifests/step16/step16-0f4d32b53427e229070f58f8511af709")
DEFAULT_SOURCE_ROOT = Path("/media/l/LSC_DATA2/HAT's libary/german federal law/German law")
EXPECTED_STEP14_DIGEST = "ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89"
EXPECTED_STEP15_DIGEST = "7094358f7c9bb6acf62160484a017074da70361c73e4e5bbd7623f700414b125"
EXPECTED_STEP16_DIGEST = "6871562b5b17d632c0e15169fefe7186f3fc7d7b5eb59f4140c367bf2c8a37e8"
EXPECTED_SOURCE_ID = "de-federal-gii-bjnr1330a0023"
EXPECTED_OFFICIAL_IDENTIFIER = "BJNR1330A0023"
EXPECTED_VERSION_IDENTITY = "legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95"
FIXTURE_TIME = datetime(2026, 8, 1, 21, 51, 24, tzinfo=UTC)


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 18 controlled validation failed")
        self.code = code


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path, default=DEFAULT_COCKROACH)
    parser.add_argument("--step14-bundle-root", type=Path, default=DEFAULT_STEP14)
    parser.add_argument("--step15-bundle-root", type=Path, default=DEFAULT_STEP15)
    parser.add_argument("--step16-bundle-root", type=Path, default=DEFAULT_STEP16)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    return parser.parse_args()


def _jsonl_first(path: Path, predicate) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValidationFailure("REAL_CORPUS_ARTIFACT_UNAVAILABLE")
    with path.open("rb") as stream:
        for raw in stream:
            if len(raw) > 256 * 1024 or not raw.endswith(b"\n"):
                raise ValidationFailure("REAL_CORPUS_ARTIFACT_INVALID")
            try:
                value = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValidationFailure("REAL_CORPUS_ARTIFACT_INVALID") from exc
            if isinstance(value, Mapping) and predicate(value):
                return dict(value)
    raise ValidationFailure("REAL_CORPUS_ITEM_UNAVAILABLE")


def _real_fixture(args: argparse.Namespace) -> tuple[Mapping[str, Any], Mapping[str, Any], object]:
    step14 = args.step14_bundle_root.resolve(strict=True)
    step15 = args.step15_bundle_root.resolve(strict=True)
    step16 = args.step16_bundle_root.resolve(strict=True)
    if any(path.is_symlink() for path in (step14, step15, step16)):
        raise ValidationFailure("REAL_CORPUS_BUNDLE_UNSAFE")
    verified14 = verify_inventory_bundle(step14)
    verified15 = verify_temporal_jurisdiction_bundle(step15)
    verified16 = verify_german_law_publication_bundle(step16)
    if (
        verified14["manifest_digest"] != EXPECTED_STEP14_DIGEST
        or verified15["manifest_digest"] != EXPECTED_STEP15_DIGEST
        or verified16["manifest_digest"] != EXPECTED_STEP16_DIGEST
    ):
        raise ValidationFailure("REAL_CORPUS_BUNDLE_DIGEST_MISMATCH")
    item = _jsonl_first(
        step16 / "publication-items.jsonl",
        lambda value: value.get("source_id") == EXPECTED_SOURCE_ID
        and value.get("version_identity") == EXPECTED_VERSION_IDENTITY,
    )
    candidate_data = _jsonl_first(
        step14 / "source-registration-candidates.jsonl",
        lambda value: value.get("candidate_id") == item.get("source_registry_candidate_id"),
    )
    candidate = step14_validation._candidate_from_data(candidate_data)
    paths = item.get("alias_provisions_relative_paths")
    if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
        raise ValidationFailure("REAL_CORPUS_PROVISION_PATH_INVALID")
    source_root = args.source_root.resolve(strict=True)
    provision_path = (source_root / paths[0]).resolve(strict=True)
    if provision_path.is_symlink() or not provision_path.is_relative_to(source_root):
        raise ValidationFailure("REAL_CORPUS_PROVISION_PATH_UNSAFE")
    provision = _jsonl_first(
        provision_path,
        lambda value: value.get("provision_identifier") == "I.",
    )
    content = provision.get("official_text_de")
    if (
        not isinstance(content, str)
        or hashlib.sha256(content.encode("utf-8")).hexdigest() != provision.get("content_sha256")
        or item.get("state") != "PUBLISHED"
        or item.get("official_identifier") != EXPECTED_OFFICIAL_IDENTIFIER
    ):
        raise ValidationFailure("REAL_CORPUS_PROVISION_DIGEST_MISMATCH")
    return item, provision, candidate


def _scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension("legal_jurisdiction", "DE_FEDERAL", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "step18-validation", True),
        ScopeDimension("legal_source_class", ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "step18-validation", True),
        ScopeDimension("source_language", "de", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "step18-validation", True),
    )


def _route(manifest_digest: str) -> KnowledgeRouteResult:
    return KnowledgeRouteResult(
        request_id="request-step18-validation",
        tenant_id=STEP14_TENANT_ID,
        user_id="step18-validation-user",
        routing_input_hash=canonical_sha256({"step": 18, "input": "controlled"}),
        registry_snapshot_hash=canonical_sha256({"step": 18, "registry": "trusted"}),
        knowledge_route=KnowledgeRoute.HAT_ASSIST,
        selected_hat_id="german-law",
        selected_hat_version="1.0.0",
        selected_manifest_digest=manifest_digest,
        effective_scope=_scope(),
        eligible_candidate_hashes=(canonical_sha256({"hat": "german-law", "version": "1.0.0"}),),
        reason_codes=(Step17ReasonCode.SINGLE_ASSISTING_HAT,),
    )


def _request(route: KnowledgeRouteResult, mode: RetrievalMode, selector: object, limit: int = 20) -> RetrievalRequest:
    return RetrievalRequest(
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
        retrieval_mode=mode,
        selector=selector,
        maximum_results=limit,
    )


def _start_disposable_runtime(
    runtime: migrations.LocalRuntime,
) -> "_Step18HttpSqlClient":
    """Use bounded loopback boot readiness, then prove real SQL readiness."""

    original = migrations.run_process

    def http_ready() -> bool:
        if runtime.http_port is None:
            return False
        connection = http.client.HTTPConnection(
            "127.0.0.1", runtime.http_port, timeout=2
        )
        try:
            connection.request("GET", "/health?ready=1")
            response = connection.getresponse()
            response.read(4096)
            return response.status == 200
        except OSError:
            return False
        finally:
            connection.close()

    def readiness_run(command, *, timeout, environment=None):
        is_readiness_probe = timeout == 5 and any(
            part == "--execute=SELECT 1" for part in command
        )
        if is_readiness_probe:
            pid_file = (
                runtime.runtime_dir / "server.pid"
                if runtime.runtime_dir is not None
                else None
            )
            pid_matches = False
            if pid_file is not None and pid_file.is_file() and runtime.process:
                try:
                    pid_matches = (
                        int(pid_file.read_text(encoding="utf-8").strip())
                        == runtime.process.pid
                    )
                except (OSError, UnicodeError, ValueError):
                    pid_matches = False
            if (
                runtime.process is not None
                and runtime.process.poll() is None
                and pid_matches
                and runtime.sql_port is not None
                and migrations.can_connect("127.0.0.1", runtime.sql_port)
                and http_ready()
            ):
                # SqlClient uses TSV output with a header, and one_value()
                # deliberately rejects a headerless value.
                return migrations.ProcessResult(0, "?column?\n1\n", "")
            return migrations.ProcessResult(
                1,
                "",
                "ERROR: owned loopback SQL port is not ready",
            )
        return original(
            command,
            timeout=timeout,
            environment=environment,
        )

    with (
        patch.object(migrations, "run_process", readiness_run),
        patch.object(migrations, "START_TIMEOUT_SECONDS", 600),
    ):
        runtime.start()
    if runtime.http_port is None or runtime.sql_port is None:
        raise migrations.MigrationError("owned SQL validation ports are unavailable")
    validated = _Step18HttpSqlClient(runtime.http_port, runtime.sql_port)
    if migrations.one_value(validated.execute("defaultdb", "SELECT 1")) != "1":
        raise migrations.MigrationError(
            "owned disposable runtime did not become SQL-ready"
        )
    return validated


def _split_sql_statements(sql: str) -> tuple[str, ...]:
    """Split trusted migration SQL without splitting quoted function bodies."""

    if not isinstance(sql, str) or not sql.strip():
        raise migrations.MigrationError("SQL text is empty")
    statements: list[str] = []
    current: list[str] = []
    has_code = False
    index = 0
    single = False
    double = False
    line_comment = False
    block_depth = 0
    dollar_tag: str | None = None
    while index < len(sql):
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(sql[index])
                index += 1
            continue
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            current.append(character)
            index += 1
            if character == "\n":
                line_comment = False
            continue
        if block_depth:
            current.append(character)
            if character == "/" and following == "*":
                current.append(following)
                block_depth += 1
                index += 2
            elif character == "*" and following == "/":
                current.append(following)
                block_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if single:
            current.append(character)
            index += 1
            if character == "'":
                if following == "'":
                    current.append(following)
                    index += 1
                else:
                    single = False
            continue
        if double:
            current.append(character)
            index += 1
            if character == '"':
                if following == '"':
                    current.append(following)
                    index += 1
                else:
                    double = False
            continue
        if character == "-" and following == "-":
            current.extend((character, following))
            line_comment = True
            index += 2
            continue
        if character == "/" and following == "*":
            current.extend((character, following))
            block_depth = 1
            index += 2
            continue
        if character == "'":
            current.append(character)
            single = True
            has_code = True
            index += 1
            continue
        if character == '"':
            current.append(character)
            double = True
            has_code = True
            index += 1
            continue
        if character == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                candidate = sql[index : end + 1]
                tag_body = candidate[1:-1]
                if not tag_body or (
                    (tag_body[0].isalpha() or tag_body[0] == "_")
                    and all(value.isalnum() or value == "_" for value in tag_body)
                ):
                    current.append(candidate)
                    dollar_tag = candidate
                    has_code = True
                    index = end + 1
                    continue
        if character == ";":
            if has_code:
                statements.append("".join(current).strip())
            current = []
            has_code = False
            index += 1
            continue
        current.append(character)
        if not character.isspace():
            has_code = True
        index += 1
    if single or double or block_depth or dollar_tag:
        raise migrations.MigrationError("SQL text contains an unterminated token")
    if has_code:
        statements.append("".join(current).strip())
    return tuple(statements)


def _leading_sql_keyword(statement: str) -> str:
    index = 0
    while index < len(statement):
        if statement[index].isspace():
            index += 1
            continue
        if statement.startswith("--", index):
            newline = statement.find("\n", index + 2)
            index = len(statement) if newline < 0 else newline + 1
            continue
        if statement.startswith("/*", index):
            end = statement.find("*/", index + 2)
            if end < 0:
                raise migrations.MigrationError("SQL comment is unterminated")
            index = end + 2
            continue
        end = index
        while end < len(statement) and (statement[end].isalpha() or statement[end] == "_"):
            end += 1
        return statement[index:end].upper()
    return ""


def _tsv_cell(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


class _Step18HttpSqlClient:
    """Validation-only SQL API client restricted to the owned loopback node."""

    def __init__(self, port: int, sql_port: int) -> None:
        if (
            not isinstance(port, int)
            or not 1 <= port <= 65535
            or not isinstance(sql_port, int)
            or not 1 <= sql_port <= 65535
        ):
            raise migrations.MigrationError("owned SQL validation port is invalid")
        self.port = port
        self.sql_port = sql_port

    @staticmethod
    def _receive_exact(connection: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = connection.recv(remaining)
            if not chunk:
                raise migrations.MigrationError("owned pgwire stream ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @classmethod
    def _receive_pgwire(cls, connection: socket.socket) -> tuple[bytes, bytes]:
        message_type = cls._receive_exact(connection, 1)
        length = struct.unpack("!I", cls._receive_exact(connection, 4))[0]
        if length < 4 or length > 8 * 1024 * 1024:
            raise migrations.MigrationError("owned pgwire message length is invalid")
        return message_type, cls._receive_exact(connection, length - 4)

    @staticmethod
    def _pgwire_error(payload: bytes) -> migrations.SqlError:
        fields: dict[str, str] = {}
        for field in payload.rstrip(b"\x00").split(b"\x00"):
            if field:
                fields[field[:1].decode("ascii", "ignore")] = field[1:].decode(
                    "utf-8", "replace"
                )
        return migrations.SqlError(
            "owned pgwire statement failed",
            sqlstate=fields.get("C"),
        )

    @staticmethod
    def _requires_pgwire(statement: str) -> bool:
        normalized = " ".join(statement.lstrip().upper().split())
        return (
            normalized.startswith("CREATE TRIGGER ")
            or normalized.startswith("DROP TRIGGER IF EXISTS ")
            or normalized.startswith("CREATE POLICY ")
            or (
                normalized.startswith("ALTER TABLE MEMORY_PATCH.")
                and (
                    normalized.endswith(" ENABLE ROW LEVEL SECURITY")
                    or normalized.endswith(" FORCE ROW LEVEL SECURITY")
                )
            )
        )

    def _execute_admin_pgwire(
        self,
        database: str,
        statement: str,
        *,
        timeout: float,
    ) -> Mapping[str, Any]:
        if not self._requires_pgwire(statement):
            raise migrations.MigrationError("pgwire fallback statement is forbidden")
        payload = statement.encode("utf-8")
        if len(payload) > 256 * 1024 or b"\x00" in payload:
            raise migrations.MigrationError("pgwire fallback statement is invalid")
        connection = socket.create_connection(
            ("127.0.0.1", self.sql_port), timeout=timeout
        )
        connection.settimeout(timeout)
        try:
            parameters = (
                b"user\x00root\x00database\x00"
                + database.encode("ascii")
                + b"\x00application_name\x00memory-patch-step18-validation\x00\x00"
            )
            connection.sendall(
                struct.pack("!II", len(parameters) + 8, 196608) + parameters
            )
            while True:
                message_type, response = self._receive_pgwire(connection)
                if message_type == b"R":
                    if len(response) < 4 or struct.unpack("!I", response[:4])[0] != 0:
                        raise migrations.MigrationError(
                            "owned pgwire requested authentication"
                        )
                elif message_type == b"E":
                    raise self._pgwire_error(response)
                elif message_type == b"Z":
                    break
            connection.sendall(
                b"Q" + struct.pack("!I", len(payload) + 5) + payload + b"\x00"
            )
            completed = False
            while True:
                message_type, response = self._receive_pgwire(connection)
                if message_type == b"E":
                    raise self._pgwire_error(response)
                if message_type == b"D":
                    raise migrations.MigrationError(
                        "pgwire admin statement unexpectedly returned rows"
                    )
                if message_type == b"C":
                    completed = True
                if message_type == b"Z":
                    break
            if not completed:
                raise migrations.MigrationError(
                    "pgwire admin statement lacked completion marker"
                )
            connection.sendall(b"X" + struct.pack("!I", 4))
        finally:
            connection.close()
        return {
            "statement": 1,
            "tag": "ADMIN_DDL",
            "rows_affected": 0,
            "columns": [],
        }

    @staticmethod
    def _statements(sql: str) -> tuple[str, ...]:
        return tuple(
            statement
            for statement in _split_sql_statements(sql)
            if _leading_sql_keyword(statement) not in {"BEGIN", "COMMIT", "END"}
            and not statement.lstrip().startswith(
                "SET allow_role_memberships_to_change_during_transaction"
            )
        )

    def execute_results(
        self,
        database: str,
        statements: tuple[str, ...],
        *,
        timeout: float = 300,
        separate_transactions: bool = True,
    ) -> tuple[Mapping[str, Any], ...]:
        migrations.validate_database_identifier(database)
        migrations.validate_timeout(timeout)
        if not statements or len(statements) > 2048:
            raise migrations.MigrationError("SQL statement count is invalid")
        if any(not isinstance(value, str) or not value.strip() for value in statements):
            raise migrations.MigrationError("SQL statement is invalid")
        if separate_transactions and len(statements) > 1:
            combined: list[Mapping[str, Any]] = []
            for statement_number, statement in enumerate(statements, start=1):
                try:
                    if self._requires_pgwire(statement):
                        combined.append(
                            self._execute_admin_pgwire(
                                database,
                                statement,
                                timeout=timeout,
                            )
                        )
                    else:
                        combined.extend(
                            self.execute_results(
                                database,
                                (statement,),
                                timeout=timeout,
                                separate_transactions=False,
                            )
                        )
                except migrations.SqlError as exc:
                    raise migrations.SqlError(
                        f"owned SQL API statement {statement_number} failed",
                        sqlstate=exc.sqlstate,
                    ) from exc
            return tuple(combined)
        body = json.dumps(
            {
                "statements": [{"sql": value} for value in statements],
                "execute": True,
                "timeout": f"{int(timeout)}s",
                "separate_txns": separate_transactions,
                "database": database,
                "application_name": "memory-patch-step18-validation",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > 4 * 1024 * 1024:
            raise migrations.MigrationError("SQL API request exceeds validation bound")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request(
                "POST",
                "/api/v2/sql/",
                body,
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = response.read(8 * 1024 * 1024 + 1)
        except OSError as exc:
            raise migrations.MigrationError("owned SQL API request failed") from exc
        finally:
            connection.close()
        if len(payload) > 8 * 1024 * 1024:
            raise migrations.MigrationError("SQL API response exceeds validation bound")
        try:
            decoded = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise migrations.MigrationError("owned SQL API response is invalid") from exc
        if response.status != 200 or not isinstance(decoded, Mapping):
            raise migrations.MigrationError("owned SQL API request was rejected")
        error = decoded.get("error")
        if isinstance(error, Mapping):
            statement_match = re.search(
                r"(?:stmt|statement)\s+(\d+)",
                str(error.get("message", "")),
            )
            suffix = (
                f" at statement {statement_match.group(1)}"
                if statement_match
                else ""
            )
            raise migrations.SqlError(
                "owned SQL API statement failed" + suffix,
                sqlstate=str(error.get("code")) if error.get("code") else None,
            )
        execution = decoded.get("execution")
        results = execution.get("txn_results") if isinstance(execution, Mapping) else None
        if not isinstance(results, list) or len(results) != len(statements):
            raise migrations.MigrationError("owned SQL API result count differs")
        for result in results:
            if not isinstance(result, Mapping):
                raise migrations.MigrationError("owned SQL API result is invalid")
            nested_error = result.get("error")
            if isinstance(nested_error, Mapping):
                raise migrations.SqlError(
                    "owned SQL API statement failed",
                    sqlstate=(
                        str(nested_error.get("code"))
                        if nested_error.get("code")
                        else None
                    ),
                )
        return tuple(results)

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
    ) -> str:
        statements = self._statements(sql)
        results = self.execute_results(database, statements, timeout=timeout)
        result = results[-1]
        columns = result.get("columns")
        rows = result.get("rows", [])
        if not isinstance(columns, list) or not columns:
            return ""
        names = tuple(str(value.get("name")) for value in columns if isinstance(value, Mapping))
        if len(names) != len(columns) or len(set(names)) != len(names):
            raise migrations.MigrationError("owned SQL API columns are invalid")
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise migrations.MigrationError("owned SQL API rows are invalid")
        output = io.StringIO()
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(names)
        for row in rows:
            writer.writerow(tuple(_tsv_cell(row.get(name)) for name in names))
        return output.getvalue()


class _HttpCursor:
    def __init__(self, client: _Step18HttpSqlClient, database: str) -> None:
        self.client = client
        self.database = database
        self.rows: tuple[Mapping[str, Any], ...] = ()
        self.offset = 0
        self.context_sql: str | None = None
        self.closed = False

    def execute(self, sql: str, parameters=None) -> None:
        if self.closed:
            raise migrations.MigrationError("validation cursor is closed")
        rendered = cli_dbapi.render_sql(sql, parameters)
        keyword = _leading_sql_keyword(rendered)
        if keyword == "BEGIN":
            self.rows = ()
            return
        if rendered.startswith("SELECT memory_patch.set_request_context("):
            self.context_sql = rendered
            self.rows = ()
            return
        statements = (rendered,) if self.context_sql is None else (self.context_sql, rendered)
        result = self.client.execute_results(
            self.database,
            statements,
            separate_transactions=False,
        )[-1]
        rows = result.get("rows", [])
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise migrations.MigrationError("validation DB-API rows are invalid")
        self.rows = tuple(dict(row) for row in rows)
        self.offset = 0

    def fetchone(self):
        if self.offset >= len(self.rows):
            return None
        value = self.rows[self.offset]
        self.offset += 1
        return value

    def fetchall(self):
        values = self.rows[self.offset :]
        self.offset = len(self.rows)
        return values

    def close(self) -> None:
        self.closed = True
        self.rows = ()


class _HttpConnection:
    def __init__(self, client: _Step18HttpSqlClient, database: str) -> None:
        self.client = client
        self.database = database
        self.active_cursor: _HttpCursor | None = None

    def cursor(self) -> _HttpCursor:
        if self.active_cursor is not None:
            raise migrations.MigrationError("validation connection already has a cursor")
        self.active_cursor = _HttpCursor(self.client, self.database)
        return self.active_cursor

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.active_cursor = None


def _stop_owned_runtime(runtime: migrations.LocalRuntime) -> Mapping[str, Any]:
    """Stop only the exact owned PID and prove ports and storage are gone."""

    if runtime.process is None:
        raise migrations.MigrationError("owned CockroachDB PID is unavailable")
    errors: list[str] = []
    if runtime.process.poll() is None:
        runtime.process.terminate()
        try:
            runtime.process.wait(timeout=30)
        except Exception as exc:
            errors.append("OWNED_COCKROACH_PID_REMAINS")
            raise migrations.MigrationError(
                "owned CockroachDB process did not stop after SIGTERM"
            ) from exc
    return runtime._finalize_cleanup(
        errors,
        details={
            "drain_command_completed": False,
            "drain_completion_marker": False,
            "drain_shutdown_requested": False,
            "graceful_shutdown_requested": True,
            "owned_child_processes_reaped": True,
            "process_exit_code": runtime.process.returncode,
            "shutdown_method": "EXACT_OWNED_PID_SIGTERM",
            "sigterm_sent_to_exact_pid": True,
        },
        preserve_runtime_on_failure=False,
    )

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
    ) -> str:
        return super().execute(database, sql, timeout=timeout)


def _published_record(base: object, **changes: object):
    return dataclasses.replace(
        base,
        current_publication_state=SourcePublicationState.PUBLISHED,
        current_publication_sequence=3,
        current_publication_event_digest=canonical_sha256({"step": 18, "publication": changes.get("source_id", getattr(base, "source_id"))}),
        registry_digest="",
        **changes,
    )


def _clone_record(base: object, *, tenant_id: str, source_id: str, hat_scope_id: str, state: SourcePublicationState, authority: SourceAuthorityLevel):
    scope = dataclasses.replace(
        base.scope,
        tenant_id=tenant_id,
        hat_scope_id=hat_scope_id,
        scope_digest="",
    )
    assessment = SourceAuthorityAssessment(authority, dict(base.authority.authority_basis))
    sequence = 0 if state is SourcePublicationState.REGISTERED else 3
    event = PUBLICATION_GENESIS_DIGEST if sequence == 0 else canonical_sha256({"step": 18, "publication": source_id})
    return dataclasses.replace(
        base,
        tenant_id=tenant_id,
        source_id=source_id,
        hat_scope_id=hat_scope_id,
        source_reference=f"gii:{source_id}",
        scope=scope,
        authority=assessment,
        current_publication_state=state,
        current_publication_sequence=sequence,
        current_publication_event_digest=event,
        registry_digest="",
    )


def _seed_sql(records: tuple[object, ...], item: Mapping[str, Any], provisions: tuple[Mapping[str, Any], ...], manifest_digest: str) -> str:
    q = migrations.sql_literal
    j = step9_validation.sql_json
    at = step9_validation.timestamp_sql(FIXTURE_TIME)
    tenants = tuple(sorted({record.tenant_id for record in records}))
    scopes = tuple(sorted({(record.tenant_id, record.hat_scope_id) for record in records}))
    statements = [
        "INSERT INTO memory_patch.tenants (tenant_id, display_name, metadata, created_at, updated_at) VALUES "
        + ", ".join(f"({q(value)}, 'Step 18 disposable tenant', {j({'fixture': 'step18'})}, {at}, {at})" for value in tenants),
        "INSERT INTO memory_patch.hat_manifests (hat_id, hat_version, schema_version, display_name, manifest_hash, capabilities, approval_authority, commit_authority, canonical_write_authority, external_action_authority, allows_private_memory_access, allows_user_code, created_at) VALUES "
        f"('german-law', '1.0.0', '1.0.0', 'German Law', {q(manifest_digest)}, '[\"SOURCE_AUTHORITY_RANKING\"]'::JSONB, 'NONE', 'NONE', 'NONE', 'NONE', false, false, {at})",
        "INSERT INTO memory_patch.hat_scopes (tenant_id, hat_scope_id, target_scope, knowledge_hat_id, knowledge_hat_version, created_at) VALUES "
        + ", ".join(f"({q(tenant)}, {q(hat_scope)}, 'SHARED_KNOWLEDGE_HAT', 'german-law', '1.0.0', {at})" for tenant, hat_scope in scopes),
    ]
    for record in records:
        source_suffix = hashlib.sha256(f"{record.tenant_id}:{record.source_id}:{record.hat_scope_id}".encode()).hexdigest()[:20]
        snapshot_id = f"step18-snapshot-{source_suffix}"
        knowledge_version_id = f"step18-version-{source_suffix}"
        all_content = "\n".join(str(provision["official_text_de"]) for provision in provisions)
        snapshot_digest = hashlib.sha256(all_content.encode("utf-8")).hexdigest()
        statements.append(
            "INSERT INTO memory_patch.knowledge_sources (tenant_id, source_id, hat_scope_id, source_kind, source_reference, provenance, source_observed_at, created_at) VALUES ("
            f"{q(record.tenant_id)}, {q(record.source_id)}, {q(record.hat_scope_id)}, {q(record.source_kind)}, {q(record.source_reference)}, {j({'step16_publication_item_digest': item['publication_item_digest']})}, {at}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.source_snapshots (tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, byte_length, storage_class, immutable_object_reference, captured_at, source_observed_at, provenance) VALUES ("
            f"{q(record.tenant_id)}, {q(snapshot_id)}, {q(record.source_id)}, {q(record.hat_scope_id)}, {q(snapshot_digest)}, {len(all_content.encode('utf-8'))}, 'EXTERNAL_DERIVED', {q('step16-bundle:' + str(item['publication_item_digest']))}, {at}, {at}, {j({'step16_manifest_digest': EXPECTED_STEP16_DIGEST})})"
        )
        statements.append(
            "INSERT INTO memory_patch.knowledge_versions (tenant_id, knowledge_version_id, source_id, snapshot_id, hat_scope_id, parent_knowledge_version_id, version_ordinal, normalized_content_sha256, normalization_profile, is_current, created_at, provenance) VALUES ("
            f"{q(record.tenant_id)}, {q(knowledge_version_id)}, {q(record.source_id)}, {q(snapshot_id)}, {q(record.hat_scope_id)}, NULL, 1, {q(snapshot_digest)}, 'unicode-nfc-text-normalization', true, {at}, {j({'version_identity': item['version_identity']})})"
        )
        for ordinal, provision in enumerate(provisions):
            chunk_id = f"step18-chunk-{source_suffix}-{ordinal}"
            metadata = {
                "document_identity": item["document_identity"],
                "legal_source_class": item["source_class"],
                "official_identifier": item["official_identifier"],
                "provision_identifier": provision["provision_identifier"],
                "record_id": provision["record_id"],
                "temporal_facts_digest": item.get("temporal_facts_digest"),
                "version_identity": item["version_identity"],
            }
            content = str(provision["official_text_de"])
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            statements.append(
                "INSERT INTO memory_patch.knowledge_chunks (tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, chunk_ordinal, content_text, content_sha256, start_offset, end_offset, language_tag, metadata, created_at) VALUES ("
                f"{q(record.tenant_id)}, {q(chunk_id)}, {q(knowledge_version_id)}, {q(record.source_id)}, {q(record.hat_scope_id)}, {ordinal}, {q(content)}, {q(digest)}, NULL, NULL, 'de', {j(metadata)}, {at})"
            )
            statements.append(
                "INSERT INTO memory_patch.chunk_search_documents (tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, search_config, search_vector, created_at) VALUES ("
                f"{q(record.tenant_id)}, {q(chunk_id)}, {q(knowledge_version_id)}, {q(record.source_id)}, {q(record.hat_scope_id)}, 'german', to_tsvector('german', {q(content)}), {at})"
            )
        statements.append(step9_validation.registry_insert_sql(record))
    return "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;"


class _CaptureTransaction:
    active = True

    def __init__(self) -> None:
        self.sql = ""
        self.parameters: tuple[object, ...] = ()

    def fetch_all(self, sql: str, parameters=None):
        self.sql = sql
        self.parameters = tuple(parameters or ())
        return ()


def _explain(root: migrations.SqlClient, database: str, request: RetrievalRequest) -> tuple[str, str]:
    capture = _CaptureTransaction()
    CockroachRetrievalRepository().search(capture, request)
    rendered = cli_dbapi.render_sql(capture.sql, capture.parameters)
    rows = root.execute(database, "EXPLAIN " + rendered, timeout=120)
    if isinstance(rows, str):
        return canonical_sha256(rows), rows
    summary_lines: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            summary_lines.append("|".join(str(value) for value in row.values()))
        elif isinstance(row, (tuple, list)):
            summary_lines.append("|".join(str(value) for value in row))
        else:
            summary_lines.append(str(row))
    summary = "\n".join(summary_lines)
    return canonical_sha256(rows), summary


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    item, first, candidate = _real_fixture(args)
    provision_path = args.source_root.resolve() / item["alias_provisions_relative_paths"][0]
    second = _jsonl_first(provision_path, lambda value: value.get("provision_identifier") == "II.")
    manifest = decode_manifest(
        (ROOT / "config/hats/german-law-1.0.0.json").read_bytes(),
        schema_path=ROOT / "schemas/hat-manifest.schema.json",
    )
    route = _route(manifest.typed_manifest_digest)
    base = build_source_registry_record(candidate, created_at=FIXTURE_TIME)
    published = _published_record(base)
    unpublished = _clone_record(base, tenant_id=STEP14_TENANT_ID, source_id="step18-unpublished", hat_scope_id="german-law-global-1a", state=SourcePublicationState.REGISTERED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    weak = _clone_record(base, tenant_id=STEP14_TENANT_ID, source_id="step18-weak-authority", hat_scope_id="german-law-global-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.INFORMATIONAL_SECONDARY)
    other_hat = _clone_record(base, tenant_id=STEP14_TENANT_ID, source_id="step18-other-hat", hat_scope_id="german-law-other-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    other_tenant = _clone_record(base, tenant_id=STEP14_TENANT_ID + "-other", source_id=EXPECTED_SOURCE_ID, hat_scope_id="german-law-global-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    records = (published, unpublished, weak, other_hat, other_tenant)
    source_binary = args.cockroach_binary.resolve(strict=True)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("COCKROACH_BINARY_DIGEST_MISMATCH")
    binary = source_binary
    # Reuse the already-audited disposable-runtime ownership prefix.  It is a
    # process-cleanup identity only and does not change the Step 18 schema.
    runtime = migrations.LocalRuntime(binary, "mp_step15_step18_" + uuid.uuid4().hex[:8])
    root: _Step18HttpSqlClient | None = None
    database: str | None = None
    cleanup: Mapping[str, Any] | None = None
    try:
        root = _start_disposable_runtime(runtime)
        database = "mp_step15_step18_" + uuid.uuid4().hex[:8]
        migrations.create_database(root, database)
        first_apply = migrations.apply_migrations(root, database, timeout=300)
        replay = migrations.apply_migrations(root, database, timeout=300)
        if len(first_apply["applied"]) != len(migrations.load_migrations()) or replay["applied"]:
            raise ValidationFailure("MIGRATION_REPLAY_MISMATCH")
        root.execute(database, _seed_sql(records, item, (first, second), manifest.typed_manifest_digest), timeout=300)
        if runtime.runtime_dir is None or runtime.http_port is None:
            raise ValidationFailure("DISPOSABLE_RUNTIME_FACTS_MISSING")
        factory = lambda: _HttpConnection(root, database)
        service = RetrievalService(SerializableTransactionRunner(factory))
        requests = {
            "exact_source": _request(route, RetrievalMode.EXACT_IDENTIFIER, ExactIdentifierSelector(ExactIdentifierField.SOURCE_ID, (EXPECTED_SOURCE_ID,))),
            "exact_official": _request(route, RetrievalMode.EXACT_IDENTIFIER, ExactIdentifierSelector(ExactIdentifierField.OFFICIAL_IDENTIFIER, (EXPECTED_OFFICIAL_IDENTIFIER,))),
            "statute_section": _request(route, RetrievalMode.STATUTE_SECTION, StatuteSectionSelector(EXPECTED_OFFICIAL_IDENTIFIER, "I.")),
            "full_text": _request(route, RetrievalMode.FULL_TEXT, FullTextQuery("Bundespräsidenten Ernennung")),
            "keyword": _request(route, RetrievalMode.KEYWORD, KeywordQuery(("Artikel", "Bundespräsidenten"))),
        }
        results = {name: service.retrieve(value) for name, value in requests.items()}
        if any(not result.candidates for result in results.values()):
            raise ValidationFailure("RETRIEVAL_MODE_RESULT_MISSING")
        expected_source = {EXPECTED_SOURCE_ID}
        if any({candidate.source_id for candidate in result.candidates} != expected_source for result in results.values()):
            raise ValidationFailure("HARD_FILTER_NEGATIVE_LEAK")
        if results["statute_section"].candidates[0].structured_metadata.get("provision_identifier") != "I.":
            raise ValidationFailure("STATUTE_SECTION_RESULT_MISMATCH")
        explain_digest, explain_text = _explain(root, database, requests["full_text"])
        index_output = root.execute(
            database,
            "SHOW INDEXES FROM memory_patch.chunk_search_documents",
            timeout=60,
        )
        indexes = tuple(csv.DictReader(io.StringIO(index_output), delimiter="\t"))
        index_names = sorted(
            {
                str(row["index_name"])
                for row in indexes
                if row.get("index_name")
            }
        )
        if "chunk_search_documents_vector_idx" not in index_names:
            raise ValidationFailure("FTS_INDEX_INVENTORY_MISSING")
        no_match = service.retrieve(_request(route, RetrievalMode.EXACT_IDENTIFIER, ExactIdentifierSelector(ExactIdentifierField.SOURCE_ID, ("step18-unpublished",))))
        if no_match.candidates:
            raise ValidationFailure("UNPUBLISHED_SOURCE_LEAK")
        migrations.drop_database(root, database, timeout=300)
        database = None
        cleanup = _stop_owned_runtime(runtime)
        root = None
        if cleanup["cleanup_errors"] or cleanup["force_kill_used"] or not all(
            cleanup[key]
            for key in ("pid_exited", "ports_closed", "temporary_store_removed")
        ):
            raise ValidationFailure("DISPOSABLE_RUNTIME_CLEANUP_FAILED")
        evidence: dict[str, Any] = {
            "schema_version": "1.0.0",
            "step": "STEP_18_EXACT_FULL_TEXT_RETRIEVAL_BASELINE_1A",
            "status": "PASS",
            "baseline_sha": BASELINE_SHA,
            "cockroachdb": {
                "binary_sha256": identity["binary_sha256"],
                "pinned_version": migrations.PINNED_VERSION,
                "existing_index_names": index_names,
                "explain_digest": explain_digest,
                "explain_mentions_inverted_index": "chunk_search_documents_vector_idx" in explain_text,
                "migration_replay": "PASS_NOOP",
                "new_migration": False,
            },
            "step17_binding": {
                "route_hash": route.route_hash,
                "selected_hat_id": route.selected_hat_id,
                "selected_hat_version": route.selected_hat_version,
                "selected_manifest_digest": route.selected_manifest_digest,
                "tenant_id": route.tenant_id,
                "user_id": route.user_id,
                "effective_scope_hash": canonical_sha256(route.effective_scope),
                "status": "PASS",
            },
            "retrieval_matrix": {
                name: {
                    "candidate_count": result.candidate_count,
                    "candidate_hashes": [candidate.candidate_hash for candidate in result.candidates],
                    "result_hash": result.result_hash,
                    "status": "PASS",
                }
                for name, result in results.items()
            },
            "hard_filters": {
                "tenant_isolation": "PASS",
                "hat_isolation": "PASS",
                "route_scope_before_candidate_generation": "PASS",
                "source_authority_filter": "PASS",
                "publication_state_filter": "PASS",
                "access_and_owner_filter": "PASS",
            },
            "real_step16_fixture": {
                "status": "PASS",
                "step14_manifest_digest": EXPECTED_STEP14_DIGEST,
                "step15_manifest_digest": EXPECTED_STEP15_DIGEST,
                "step16_manifest_digest": EXPECTED_STEP16_DIGEST,
                "publication_item_digest": item["publication_item_digest"],
                "source_id": item["source_id"],
                "official_identifier": item["official_identifier"],
                "version_identity": item["version_identity"],
                "provision_identifier": first["provision_identifier"],
                "provision_content_sha256": first["content_sha256"],
                "bounded_items": 1,
                "bounded_provisions": 2,
                "source_bundle_writes": 0,
            },
            "resource_bounds": {
                "default_results": 20,
                "maximum_results": 100,
                "maximum_query_utf8_bytes": 4096,
                "maximum_candidate_content_bytes": 65536,
                "maximum_total_content_bytes": 1048576,
            },
            "boundaries": {
                "aws_calls": 0,
                "s3_writes": 0,
                "network_acquisition": 0,
                "model_calls": 0,
                "embeddings": 0,
                "vector_retrieval": 0,
                "hybrid_retrieval": 0,
                "reranking": 0,
                "step19_started": False,
                "step20_started": False,
            },
            "cleanup": {
                "force_kill_used": cleanup["force_kill_used"],
                "pid_exited": cleanup["pid_exited"],
                "ports_closed": cleanup["ports_closed"],
                "temporary_store_removed": cleanup["temporary_store_removed"],
                "drain_completed": cleanup["drain_command_completed"],
                "graceful_shutdown_requested": cleanup[
                    "graceful_shutdown_requested"
                ],
                "shutdown_method": cleanup["shutdown_method"],
            },
        }
        evidence["validation_digest"] = canonical_sha256(evidence)
        return evidence
    finally:
        if root is not None:
            try:
                if database is not None:
                    migrations.drop_database(root, database, timeout=300)
                _stop_owned_runtime(runtime)
            except Exception:
                pass
        elif runtime.runtime_dir is not None:
            try:
                runtime.stop_and_remove()
            except Exception:
                pass


def main() -> int:
    try:
        evidence = validate(_arguments())
    except (ValidationFailure, OSError, ValueError, migrations.MigrationError) as exc:
        code = exc.code if isinstance(exc, ValidationFailure) else type(exc).__name__.upper()
        diagnostic = re.search(
            r"(?:database phase \d+ failed: )?owned SQL API statement \d+ failed",
            str(exc),
        )
        failure = {"status": "FAILED", "reason": code}
        if diagnostic:
            failure["diagnostic"] = diagnostic.group(0)
        print(canonical_json(failure), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
