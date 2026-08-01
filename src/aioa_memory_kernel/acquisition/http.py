"""HTTPS-only bounded transport with redirects, robots, rate, and evidence."""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .errors import (
    AcquisitionIntegrityError,
    AcquisitionPolicyError,
    AcquisitionStorageError,
    AcquisitionTransportError,
)
from .models import HttpObjectReceipt
from .storage import AcquisitionRootGuard


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        maximum: int,
        on_redirect: Callable[[str, int], None],
    ) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.maximum = maximum
        self.on_redirect = on_redirect

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        try:
            port = parsed.port
        except ValueError as exc:
            raise AcquisitionTransportError(
                "redirect URL has a malformed port",
                code="CROSS_HOST_REDIRECT",
            ) from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.allowed_hosts
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.fragment)
        ):
            raise AcquisitionTransportError(
                "redirect escaped its HTTPS source allowlist",
                code="CROSS_HOST_REDIRECT",
            )
        count = int(getattr(req, "_acquisition_redirect_count", 0)) + 1
        if count > self.maximum:
            raise AcquisitionTransportError(
                "redirect count exceeds its bound",
                code="REDIRECT_LIMIT_EXCEEDED",
            )
        self.on_redirect(newurl, count)
        redirected = super().redirect_request(
            req, fp, code, msg, headers, newurl
        )
        if redirected is not None:
            setattr(redirected, "_acquisition_redirect_count", count)
        return redirected


class SafeHttpsClient:
    """One-process, one-request-at-a-time official-source downloader."""

    def __init__(
        self,
        root: AcquisitionRootGuard,
        *,
        clock: Callable[[], str] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = root
        self.policy = root.policy
        self.clock = clock
        self.monotonic = monotonic
        self.sleeper = sleeper
        self._last_request: dict[str, float] = {}
        self._minimum_delays: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._robots_digests: dict[str, str] = {}
        self._object_events: dict[str, tuple[str, int, str]] = {}
        self._load_last_request_times()
        self._load_object_events()

    def _load_last_request_times(self) -> None:
        ledger = self.root.resolve("00_CONTROL/request-ledger.jsonl")
        if not ledger.exists():
            return
        # Persisted timestamps are evidence; a restarted process applies one
        # full source delay before its first new request rather than attempting
        # to translate wall-clock time into a monotonic clock.
        seen: set[str] = set()
        with ledger.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                host = record.get("host")
                if (
                    record.get("event")
                    in {"HTTP_REQUEST_ATTEMPT", "HTTP_REDIRECT_ATTEMPT"}
                    and isinstance(host, str)
                ):
                    seen.add(host)
        now = self.monotonic()
        for host in seen:
            self._last_request[host] = now

    def _load_object_events(self) -> None:
        ledger = self.root.resolve("00_CONTROL/object-ledger.jsonl")
        if not ledger.exists():
            return
        with ledger.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("event") not in {
                    "OBJECT_PUBLISHED",
                    "OBJECT_RECONCILED",
                }:
                    continue
                relative = record.get("relative_output_path")
                digest = record.get("local_sha256")
                length = record.get("byte_length")
                sidecar = record.get("sidecar_digest")
                if not (
                    isinstance(relative, str)
                    and isinstance(digest, str)
                    and isinstance(length, int)
                    and isinstance(sidecar, str)
                ):
                    raise AcquisitionIntegrityError(
                        "object ledger contains a malformed publication event",
                        code="PROVENANCE_INSUFFICIENT",
                    )
                identity = (digest, length, sidecar)
                previous = self._object_events.get(relative)
                if previous is not None and previous != identity:
                    raise AcquisitionIntegrityError(
                        "object ledger contains conflicting publication events",
                        code="DUPLICATE_CONTENT_CONFLICT",
                    )
                self._object_events[relative] = identity

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        length = 0
        with path.open("rb", buffering=0) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                length += len(chunk)
        return digest.hexdigest(), length

    @staticmethod
    def _sidecar_relative(relative_output_path: str) -> str:
        return (
            "01_PROVENANCE/source-sidecars/"
            + hashlib.sha256(relative_output_path.encode("utf-8")).hexdigest()
            + ".json"
        )

    @staticmethod
    def _intent_prefix(relative_output_path: str) -> str:
        return hashlib.sha256(
            relative_output_path.encode("utf-8")
        ).hexdigest()

    def _ensure_object_event(
        self,
        receipt: HttpObjectReceipt,
        *,
        event: str,
    ) -> None:
        identity = (
            receipt.local_sha256,
            receipt.byte_length,
            receipt.sidecar_digest,
        )
        previous = self._object_events.get(receipt.relative_output_path)
        if previous is not None:
            if previous != identity:
                raise AcquisitionIntegrityError(
                    "object ledger conflicts with its provenance sidecar",
                    code="DUPLICATE_CONTENT_CONFLICT",
                )
            return
        self.root.append_jsonl(
            "00_CONTROL/object-ledger.jsonl",
            {
                "event": event,
                "source_catalog_id": receipt.source_catalog_id,
                "relative_output_path": receipt.relative_output_path,
                "local_sha256": receipt.local_sha256,
                "byte_length": receipt.byte_length,
                "sidecar_digest": receipt.sidecar_digest,
            },
        )
        self._object_events[receipt.relative_output_path] = identity

    def _verify_receipt_binding(
        self,
        receipt: HttpObjectReceipt,
        *,
        target: Path,
        source_catalog_id: str,
        url: str,
        relative_output_path: str,
        allowed_hosts: frozenset[str],
        allowed_content_types: frozenset[str],
        terms_reference: str,
        license_reference: str,
        robots_reference: str,
        validators: Iterable[Callable[[Path], object]],
    ) -> HttpObjectReceipt:
        expected_digest = receipt.with_digest().sidecar_digest
        if (
            receipt.schema_version != "1.0.0"
            or receipt.sidecar_digest != expected_digest
            or receipt.source_catalog_id != source_catalog_id
            or receipt.requested_url != url
            or receipt.relative_output_path != relative_output_path
            or receipt.terms_reference != terms_reference
            or receipt.license_reference != license_reference
            or receipt.robots_reference != robots_reference
            or receipt.http_status != 200
            or receipt.content_type not in allowed_content_types
            or receipt.validation_status != "PASS"
            or receipt.quarantine_reasons
        ):
            raise AcquisitionIntegrityError(
                "existing object provenance binding differs",
                code="PROVENANCE_INSUFFICIENT",
            )
        self._validated_url(receipt.final_url, allowed_hosts)
        local_sha256, byte_length = self._hash_file(target)
        if (
            local_sha256 != receipt.local_sha256
            or byte_length != receipt.byte_length
        ):
            raise AcquisitionIntegrityError(
                "existing object differs from its provenance receipt",
                code="DUPLICATE_CONTENT_CONFLICT",
            )
        if receipt.content_length_header is not None:
            try:
                declared = int(receipt.content_length_header)
            except ValueError as exc:
                raise AcquisitionIntegrityError(
                    "stored Content-Length is malformed",
                    code="PROVENANCE_INSUFFICIENT",
                ) from exc
            if declared != byte_length:
                raise AcquisitionIntegrityError(
                    "stored Content-Length differs from the object",
                    code="CONTENT_LENGTH_MISMATCH",
                )
        for validator in validators:
            validator(target)
        return receipt

    def _recover_missing_sidecar(
        self,
        *,
        target: Path,
        sidecar_relative: str,
        source_catalog_id: str,
        url: str,
        relative_output_path: str,
        allowed_hosts: frozenset[str],
        allowed_content_types: frozenset[str],
        terms_reference: str,
        license_reference: str,
        robots_reference: str,
        validators: Iterable[Callable[[Path], object]],
    ) -> HttpObjectReceipt:
        prefix = self._intent_prefix(relative_output_path)
        intent_directory = self.root.resolve(
            "00_CONTROL/checkpoints/object-intents"
        )
        intents = sorted(intent_directory.glob(f"{prefix}-*.json"))
        if not intents:
            raise AcquisitionIntegrityError(
                "existing object lacks both sidecar and download intent",
                code="PROVENANCE_INSUFFICIENT",
            )
        intent = json.loads(intents[-1].read_text(encoding="utf-8"))
        if intent.get("intent_digest") != canonical_sha256(
            intent,
            exclude_fields=("intent_digest",),
        ):
            raise AcquisitionIntegrityError(
                "download intent digest is invalid",
                code="PROVENANCE_INSUFFICIENT",
            )
        if (
            intent.get("source_catalog_id") != source_catalog_id
            or intent.get("requested_url") != url
            or intent.get("relative_output_path") != relative_output_path
            or intent.get("terms_reference") != terms_reference
            or intent.get("license_reference") != license_reference
            or intent.get("robots_reference") != robots_reference
            or intent.get("http_status") != 200
            or intent.get("content_type") not in allowed_content_types
        ):
            raise AcquisitionIntegrityError(
                "download intent does not bind the existing object",
                code="PROVENANCE_INSUFFICIENT",
            )
        final_url = intent.get("final_url")
        if not isinstance(final_url, str):
            raise AcquisitionIntegrityError(
                "download intent lacks a final URL",
                code="PROVENANCE_INSUFFICIENT",
            )
        self._validated_url(final_url, allowed_hosts)
        local_sha256, byte_length = self._hash_file(target)
        header_length = intent.get("content_length_header")
        if header_length is not None:
            try:
                declared = int(header_length)
            except (TypeError, ValueError) as exc:
                raise AcquisitionIntegrityError(
                    "download intent Content-Length is malformed",
                    code="PROVENANCE_INSUFFICIENT",
                ) from exc
            if declared != byte_length:
                raise AcquisitionIntegrityError(
                    "download intent Content-Length differs from the object",
                    code="CONTENT_LENGTH_MISMATCH",
                )
        for validator in validators:
            validator(target)
        receipt = HttpObjectReceipt(
            schema_version="1.0.0",
            source_catalog_id=source_catalog_id,
            requested_url=url,
            final_url=final_url,
            retrieved_at=str(intent["retrieved_at"]),
            http_status=200,
            content_type=str(intent["content_type"]),
            content_length_header=(
                None if header_length is None else str(header_length)
            ),
            etag=intent.get("etag"),
            last_modified=intent.get("last_modified"),
            publisher_checksum=None,
            publisher_checksum_algorithm=None,
            local_sha256=local_sha256,
            byte_length=byte_length,
            relative_output_path=relative_output_path,
            terms_reference=terms_reference,
            license_reference=license_reference,
            robots_reference=robots_reference,
            request_sequence=int(intent["request_sequence"]),
            retry_count=int(intent["retry_count"]),
            validation_status="PASS",
            quarantine_reasons=(),
        ).with_digest()
        self.root.write_json_absent(sidecar_relative, receipt)
        self._ensure_object_event(receipt, event="OBJECT_RECONCILED")
        return receipt

    @staticmethod
    def _validated_url(url: str, allowed_hosts: frozenset[str]) -> str:
        parsed = urllib.parse.urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise AcquisitionPolicyError(
                "URL has a malformed port",
                code="URL_NOT_ALLOWLISTED",
            ) from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise AcquisitionPolicyError(
                "URL is outside the fixed HTTPS allowlist",
                code="URL_NOT_ALLOWLISTED",
            )
        return url

    def prepare_bootstrap_delay(self, host: str, seconds: float) -> None:
        """Install a known source floor before fetching its frozen robots."""

        if not host or seconds < 0:
            raise AcquisitionPolicyError(
                "bootstrap pacing policy is malformed",
                code="RATE_POLICY_INVALID",
            )
        self._minimum_delays[host] = max(
            self._minimum_delays.get(host, 0.0),
            float(seconds),
        )

    def install_robots(
        self,
        *,
        host: str,
        robots_url: str,
        body: bytes,
        policy_floor_seconds: float,
    ) -> dict[str, object]:
        if urllib.parse.urlsplit(robots_url).hostname != host:
            raise AcquisitionPolicyError(
                "robots URL does not match its source host",
                code="ROBOTS_HOST_MISMATCH",
            )
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise AcquisitionPolicyError(
                "robots policy is not UTF-8",
                code="ROBOTS_CHANGED",
            ) from exc
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(text.splitlines())
        declared = parser.crawl_delay(self.policy.user_agent)
        if declared is None:
            declared = parser.crawl_delay("*")
        declared_delay = float(declared or 0)
        self._robots[host] = parser
        self._robots_digests[host] = hashlib.sha256(body).hexdigest()
        self._minimum_delays[host] = max(
            float(policy_floor_seconds), declared_delay
        )
        return {
            "host": host,
            "robots_sha256": self._robots_digests[host],
            "declared_crawl_delay_seconds": declared_delay,
            "effective_delay_seconds": self._minimum_delays[host],
        }

    def _enforce_robots(self, host: str, url: str) -> None:
        parser = self._robots.get(host)
        if parser is None:
            raise AcquisitionPolicyError(
                "source robots policy has not been frozen",
                code="ROBOTS_NOT_FROZEN",
            )
        if not parser.can_fetch(self.policy.user_agent, url):
            raise AcquisitionPolicyError(
                "robots policy disallows this object",
                code="ROBOTS_DISALLOWED",
            )

    def _pace(self, host: str) -> None:
        delay = self._minimum_delays.get(host, 0.0)
        previous = self._last_request.get(host)
        now = self.monotonic()
        if previous is not None and delay > 0:
            remaining = previous + delay - now
            if remaining > 0:
                self.sleeper(remaining)
        self._last_request[host] = self.monotonic()

    def _opener(
        self,
        allowed_hosts: frozenset[str],
        on_redirect: Callable[[str, int], None],
    ):
        context = ssl.create_default_context()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
            _StrictRedirectHandler(
                allowed_hosts,
                self.policy.maximum_redirects,
                on_redirect,
            ),
        )

    def bootstrap_bytes(
        self,
        *,
        source_catalog_id: str,
        url: str,
        allowed_hosts: frozenset[str],
        maximum_bytes: int = 1024 * 1024,
        minimum_delay_seconds: float = 0,
    ) -> tuple[bytes, Mapping[str, str], str, int]:
        """Fetch robots before a host policy can be enforced."""

        self._validated_url(url, allowed_hosts)
        parsed = urllib.parse.urlsplit(url)
        assert parsed.hostname is not None
        self._minimum_delays[parsed.hostname] = max(
            self._minimum_delays.get(parsed.hostname, 0),
            minimum_delay_seconds,
        )
        response, sequence, retry = self._request(
            source_catalog_id=source_catalog_id,
            url=url,
            allowed_hosts=allowed_hosts,
            enforce_robots=False,
        )
        with response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > maximum_bytes:
                raise AcquisitionTransportError(
                    "bootstrap body exceeds its bound",
                    code="SIZE_LIMIT_EXCEEDED",
                )
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise AcquisitionTransportError(
                    "bootstrap body exceeds its bound",
                    code="SIZE_LIMIT_EXCEEDED",
                )
            headers = {key: value for key, value in response.headers.items()}
            return body, headers, response.geturl(), sequence

    def _retry_delay(self, headers: Mapping[str, str], retry: int) -> float:
        value = headers.get("Retry-After")
        if value:
            if value.isdecimal():
                delay = float(value)
                if delay > 3600:
                    raise AcquisitionPolicyError(
                        "Retry-After exceeds the bounded active-run wait",
                        code="RETRY_AFTER_EXCEEDS_BOUND",
                    )
                return delay
            try:
                when = email.utils.parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                delay = max(
                    0.0,
                    (when - datetime.now(timezone.utc)).total_seconds(),
                )
                if delay > 3600:
                    raise AcquisitionPolicyError(
                        "Retry-After exceeds the bounded active-run wait",
                        code="RETRY_AFTER_EXCEEDS_BOUND",
                    )
                return delay
            except (TypeError, ValueError):
                pass
        return min(float(2**retry), 60.0)

    def _request(
        self,
        *,
        source_catalog_id: str,
        url: str,
        allowed_hosts: frozenset[str],
        enforce_robots: bool,
    ):
        self._validated_url(url, allowed_hosts)
        host = urllib.parse.urlsplit(url).hostname
        assert host is not None
        if enforce_robots:
            self._enforce_robots(host, url)
        last_error: Exception | None = None
        for retry in range(self.policy.maximum_retries + 1):
            if self.root.request_count >= self.policy.maximum_requests:
                raise AcquisitionPolicyError(
                    "global request budget is exhausted",
                    code="REQUEST_LIMIT_EXCEEDED",
                )
            self._pace(host)
            self.root.request_count += 1
            sequence = self.root.request_count
            started_at = self.clock()
            self.root.append_jsonl(
                "00_CONTROL/request-ledger.jsonl",
                {
                    "event": "HTTP_REQUEST_ATTEMPT",
                    "source_catalog_id": source_catalog_id,
                    "request_sequence": sequence,
                    "requested_url": url,
                    "host": host,
                    "retry_count": retry,
                    "started_at": started_at,
                },
            )
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.policy.user_agent,
                    "Accept-Encoding": "identity",
                    "Accept": "*/*",
                    "Connection": "close",
                },
                method="GET",
            )

            def record_redirect(newurl: str, redirect_count: int) -> None:
                redirect_host = urllib.parse.urlsplit(newurl).hostname
                assert redirect_host is not None
                if enforce_robots:
                    self._enforce_robots(redirect_host, newurl)
                if self.root.request_count >= self.policy.maximum_requests:
                    raise AcquisitionPolicyError(
                        "global request budget is exhausted by a redirect",
                        code="REQUEST_LIMIT_EXCEEDED",
                    )
                self._pace(redirect_host)
                self.root.request_count += 1
                self.root.append_jsonl(
                    "00_CONTROL/request-ledger.jsonl",
                    {
                        "event": "HTTP_REDIRECT_ATTEMPT",
                        "source_catalog_id": source_catalog_id,
                        "request_sequence": self.root.request_count,
                        "requested_url": newurl,
                        "host": redirect_host,
                        "redirect_count": redirect_count,
                        "retry_count": retry,
                        "started_at": self.clock(),
                    },
                )

            try:
                response = self._opener(allowed_hosts, record_redirect).open(
                    request,
                    timeout=self.policy.connect_timeout_seconds,
                )
                try:
                    response.fp.raw._sock.settimeout(
                        self.policy.read_timeout_seconds
                    )
                except AttributeError:
                    # Some injected/test responses have no raw socket.  The
                    # connect timeout remains the stricter bound there.
                    pass
                final = response.geturl()
                self._validated_url(final, allowed_hosts)
                if int(response.status) != 200:
                    response.close()
                    raise AcquisitionTransportError(
                        "official source returned an unexpected HTTP status",
                        code="HTTP_STATUS_UNEXPECTED",
                    )
                self.root.append_jsonl(
                    "00_CONTROL/request-ledger.jsonl",
                    {
                        "event": "HTTP_RESPONSE_OPENED",
                        "source_catalog_id": source_catalog_id,
                        "request_sequence": sequence,
                        "requested_url": url,
                        "final_url": final,
                        "host": host,
                        "status": int(response.status),
                        "retry_count": retry,
                        "started_at": started_at,
                    },
                )
                return response, sequence, retry
            except (AcquisitionTransportError, AcquisitionPolicyError) as exc:
                self.root.append_jsonl(
                    "00_CONTROL/request-ledger.jsonl",
                    {
                        "event": "HTTP_POLICY_ERROR",
                        "source_catalog_id": source_catalog_id,
                        "request_sequence": sequence,
                        "requested_url": url,
                        "host": host,
                        "reason": exc.code,
                        "retry_count": retry,
                        "started_at": started_at,
                    },
                )
                raise
            except urllib.error.HTTPError as exc:
                last_error = exc
                self.root.append_jsonl(
                    "00_CONTROL/request-ledger.jsonl",
                    {
                        "event": "HTTP_ERROR",
                        "source_catalog_id": source_catalog_id,
                        "request_sequence": sequence,
                        "requested_url": url,
                        "host": host,
                        "status": int(exc.code),
                        "retry_count": retry,
                        "started_at": started_at,
                    },
                )
                if exc.code not in (429, 503) or retry >= self.policy.maximum_retries:
                    raise AcquisitionTransportError(
                        "official source returned an unexpected HTTP status",
                        code="HTTP_STATUS_UNEXPECTED",
                    ) from exc
                self.sleeper(self._retry_delay(exc.headers, retry))
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = exc
                self.root.append_jsonl(
                    "00_CONTROL/request-ledger.jsonl",
                    {
                        "event": "HTTP_TRANSPORT_ERROR",
                        "source_catalog_id": source_catalog_id,
                        "request_sequence": sequence,
                        "requested_url": url,
                        "host": host,
                        "retry_count": retry,
                        "started_at": started_at,
                    },
                )
                if retry >= self.policy.maximum_retries:
                    break
                self.sleeper(min(float(2**retry), 60.0))
        raise AcquisitionTransportError(
            "official source transport retries were exhausted",
            code="HTTP_TRANSPORT_FAILED",
        ) from last_error

    def download(
        self,
        *,
        source_catalog_id: str,
        url: str,
        relative_output_path: str,
        allowed_hosts: frozenset[str],
        allowed_content_types: frozenset[str],
        terms_reference: str,
        license_reference: str,
        robots_reference: str,
        validators: Iterable[Callable[[Path], object]] = (),
        enforce_robots: bool = True,
    ) -> HttpObjectReceipt:
        target = self.root.resolve(relative_output_path)
        validator_tuple = tuple(validators)
        sidecar_relative = self._sidecar_relative(relative_output_path)
        sidecar_path = self.root.resolve(sidecar_relative)
        if os.path.lexists(target):
            target = self.root.require_regular_file(relative_output_path)
            if not os.path.lexists(sidecar_path):
                return self._recover_missing_sidecar(
                    target=target,
                    sidecar_relative=sidecar_relative,
                    source_catalog_id=source_catalog_id,
                    url=url,
                    relative_output_path=relative_output_path,
                    allowed_hosts=allowed_hosts,
                    allowed_content_types=allowed_content_types,
                    terms_reference=terms_reference,
                    license_reference=license_reference,
                    robots_reference=robots_reference,
                    validators=validator_tuple,
                )
            sidecar_path = self.root.require_regular_file(sidecar_relative)
            try:
                record = json.loads(sidecar_path.read_text(encoding="utf-8"))
                reasons = record.get("quarantine_reasons")
                if not (
                    isinstance(reasons, list)
                    and all(isinstance(item, str) for item in reasons)
                ):
                    raise TypeError("quarantine reasons are malformed")
                record["quarantine_reasons"] = tuple(reasons)
                receipt = HttpObjectReceipt(**record)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
                raise AcquisitionIntegrityError(
                    "existing provenance sidecar is malformed",
                    code="PROVENANCE_INSUFFICIENT",
                ) from exc
            receipt = self._verify_receipt_binding(
                receipt,
                target=target,
                source_catalog_id=source_catalog_id,
                url=url,
                relative_output_path=relative_output_path,
                allowed_hosts=allowed_hosts,
                allowed_content_types=allowed_content_types,
                terms_reference=terms_reference,
                license_reference=license_reference,
                robots_reference=robots_reference,
                validators=validator_tuple,
            )
            self._ensure_object_event(receipt, event="OBJECT_RECONCILED")
            return receipt
        if os.path.lexists(sidecar_path):
            raise AcquisitionIntegrityError(
                "provenance sidecar exists without its acquired object",
                code="PROVENANCE_INSUFFICIENT",
            )

        response, sequence, retry = self._request(
            source_catalog_id=source_catalog_id,
            url=url,
            allowed_hosts=allowed_hosts,
            enforce_robots=enforce_robots,
        )
        retrieved_at = self.clock()
        with response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in allowed_content_types:
                raise AcquisitionTransportError(
                    "official source returned an unexpected content type",
                    code="CONTENT_TYPE_MISMATCH",
                )
            header_length = response.headers.get("Content-Length")
            declared_length: int | None = None
            if header_length:
                try:
                    declared_length = int(header_length)
                except ValueError as exc:
                    raise AcquisitionTransportError(
                        "content length header is malformed",
                        code="CONTENT_LENGTH_MALFORMED",
                    ) from exc
                if (
                    declared_length < 0
                    or declared_length > self.policy.maximum_response_bytes
                ):
                    raise AcquisitionTransportError(
                        "response size claim exceeds its bound",
                        code="SIZE_LIMIT_EXCEEDED",
                    )
            effective_validators = list(validator_tuple)
            if declared_length is not None:
                def verify_declared_length(path: Path) -> None:
                    if path.stat().st_size != declared_length:
                        raise AcquisitionIntegrityError(
                            "response body length differs from Content-Length",
                            code="CONTENT_LENGTH_MISMATCH",
                        )

                effective_validators.insert(0, verify_declared_length)
            final_url = response.geturl()
            intent = {
                "schema_version": "1.0.0",
                "source_catalog_id": source_catalog_id,
                "requested_url": url,
                "final_url": final_url,
                "retrieved_at": retrieved_at,
                "http_status": int(response.status),
                "content_type": content_type,
                "content_length_header": header_length,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "relative_output_path": relative_output_path,
                "terms_reference": terms_reference,
                "license_reference": license_reference,
                "robots_reference": robots_reference,
                "request_sequence": sequence,
                "retry_count": retry,
            }
            intent["intent_digest"] = canonical_sha256(intent)
            intent_relative = (
                "00_CONTROL/checkpoints/object-intents/"
                f"{self._intent_prefix(relative_output_path)}-"
                f"{sequence:08d}.json"
            )
            self.root.write_json_absent(intent_relative, intent)
            with self.root.stream_writer(relative_output_path) as writer:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                local_sha256, byte_length = writer.publish(
                    effective_validators
                )
            receipt = HttpObjectReceipt(
                schema_version="1.0.0",
                source_catalog_id=source_catalog_id,
                requested_url=url,
                final_url=final_url,
                retrieved_at=retrieved_at,
                http_status=int(response.status),
                content_type=content_type,
                content_length_header=header_length,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                publisher_checksum=None,
                publisher_checksum_algorithm=None,
                local_sha256=local_sha256,
                byte_length=byte_length,
                relative_output_path=relative_output_path,
                terms_reference=terms_reference,
                license_reference=license_reference,
                robots_reference=robots_reference,
                request_sequence=sequence,
                retry_count=retry,
                validation_status="PASS",
                quarantine_reasons=(),
            ).with_digest()
            self.root.write_json_absent(sidecar_relative, receipt)
            self._ensure_object_event(receipt, event="OBJECT_PUBLISHED")
            return receipt


__all__ = ["SafeHttpsClient"]
