"""Deterministic transport, provenance, and recovery tests for acquisition."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from email.message import Message
from pathlib import Path

from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.acquisition.errors import (
    AcquisitionIntegrityError,
    AcquisitionPolicyError,
    AcquisitionStorageError,
    AcquisitionTransportError,
)
from aioa_memory_kernel.acquisition.http import (
    SafeHttpsClient,
    _StrictRedirectHandler,
)
from aioa_memory_kernel.acquisition.models import AcquisitionPolicy
from aioa_memory_kernel.acquisition.storage import AcquisitionRootGuard, LAYOUT
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)


HOST = "official.example"
CDN_HOST = "cdn.official.example"
ALLOWED_HOSTS = frozenset({HOST})
OBJECT_PATH = "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/object.bin"
OBJECT_URL = f"https://{HOST}/objects/object.bin"
TERMS_URL = f"https://{HOST}/terms"
LICENSE_URL = f"https://{HOST}/license"
ROBOTS_URL = f"https://{HOST}/robots.txt"


class _ManualTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = OBJECT_URL,
        status: int = 200,
        content_type: str = "application/octet-stream",
        content_length: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._body = io.BytesIO(body)
        self._url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is None:
            content_length = str(len(body))
        if content_length:
            self.headers["Content-Length"] = content_length
        for key, value in (extra_headers or {}).items():
            self.headers[key] = value
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True)
class _Redirect:
    url: str
    response: _FakeResponse


class _PlannedOpener:
    def __init__(self, outcomes: list[object], on_redirect) -> None:
        self.outcomes = outcomes
        self.on_redirect = on_redirect
        self.calls: list[tuple[str, int]] = []

    def open(self, request: urllib.request.Request, *, timeout: int):
        self.calls.append((request.full_url, timeout))
        if not self.outcomes:
            raise AssertionError("unexpected network request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, _Redirect):
            self.on_redirect(outcome.url, 1)
            return outcome.response
        return outcome


class _OpenerPlan:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.openers: list[_PlannedOpener] = []

    def factory(self, _allowed_hosts, on_redirect) -> _PlannedOpener:
        opener = _PlannedOpener(self.outcomes, on_redirect)
        self.openers.append(opener)
        return opener


class OfficialAcquisitionHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self._guard_counter = 0

    def _guard(
        self,
        *,
        maximum_response_bytes: int = 1024,
        maximum_retries: int = 0,
    ) -> AcquisitionRootGuard:
        self._guard_counter += 1
        mountpoint = self.base / f"case-{self._guard_counter}" / "mount"
        root = mountpoint / "HAT's libary" / "German Law Official Corpus 1A"
        root.mkdir(parents=True)
        for relative in LAYOUT:
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "00_CONTROL/request-ledger.jsonl").touch()
        (root / "00_CONTROL/object-ledger.jsonl").touch()

        policy = replace(
            AcquisitionPolicy(),
            maximum_root_bytes=16 * 1024 * 1024,
            initial_minimum_free_bytes=2 * 1024 * 1024,
            final_minimum_free_bytes=1024 * 1024,
            maximum_requests=100,
            maximum_response_bytes=maximum_response_bytes,
            maximum_retries=maximum_retries,
        )
        guard = AcquisitionRootGuard.__new__(AcquisitionRootGuard)
        guard.policy = policy
        guard.repository_root = self.base / "repository"
        guard.mountpoint = mountpoint
        guard.parent = root.parent
        guard.root = root
        guard.seed = root.parent / "German law.zip"
        guard._mount_device_id = mountpoint.lstat().st_dev
        guard.request_count = 0
        guard.created_bytes = guard.root_size()
        guard.free_bytes = lambda: 1024 * 1024 * 1024  # type: ignore[method-assign]
        return guard

    @staticmethod
    def _install_robots(
        client: SafeHttpsClient,
        *,
        host: str = HOST,
        body: bytes = b"User-agent: *\nAllow: /\n",
        floor: float = 0,
    ) -> None:
        client.install_robots(
            host=host,
            robots_url=f"https://{host}/robots.txt",
            body=body,
            policy_floor_seconds=floor,
        )

    def _client(
        self,
        guard: AcquisitionRootGuard,
        plan: _OpenerPlan | None = None,
        manual_time: _ManualTime | None = None,
    ) -> SafeHttpsClient:
        manual = manual_time or _ManualTime()
        client = SafeHttpsClient(
            guard,
            clock=lambda: "2026-08-01T08:00:00Z",
            monotonic=manual.monotonic,
            sleeper=manual.sleep,
        )
        if plan is not None:
            client._opener = plan.factory  # type: ignore[method-assign]
        return client

    @staticmethod
    def _download_kwargs(**updates: object) -> dict[str, object]:
        values: dict[str, object] = {
            "source_catalog_id": "SYNTHETIC-OFFICIAL",
            "url": OBJECT_URL,
            "relative_output_path": OBJECT_PATH,
            "allowed_hosts": ALLOWED_HOSTS,
            "allowed_content_types": frozenset({"application/octet-stream"}),
            "terms_reference": TERMS_URL,
            "license_reference": LICENSE_URL,
            "robots_reference": ROBOTS_URL,
        }
        values.update(updates)
        return values

    @staticmethod
    def _ledger(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    @staticmethod
    def _assert_code(
        test: unittest.TestCase,
        expected: str,
        exception_type: type[Exception],
        operation,
    ) -> Exception:
        with test.assertRaises(exception_type) as caught:
            operation()
        test.assertEqual(getattr(caught.exception, "code", None), expected)
        return caught.exception

    def _successful_download(
        self,
        guard: AcquisitionRootGuard,
        *,
        body: bytes = b"official-object",
        validators=(),
    ):
        plan = _OpenerPlan(_FakeResponse(body))
        client = self._client(guard, plan)
        self._install_robots(client)
        receipt = client.download(
            **self._download_kwargs(validators=validators)  # type: ignore[arg-type]
        )
        return client, receipt, plan

    def test_initial_urls_reject_non_https_host_port_userinfo_and_fragment(
        self,
    ) -> None:
        invalid = (
            "http://official.example/object",
            "https://other.example/object",
            "https://official.example:444/object",
            "https://user@official.example/object",
            "https://user:secret@official.example/object",
            "https://official.example/object#fragment",
            "https://official.example:malformed/object",
        )
        for url in invalid:
            with self.subTest(url=url):
                self._assert_code(
                    self,
                    "URL_NOT_ALLOWLISTED",
                    AcquisitionPolicyError,
                    lambda url=url: SafeHttpsClient._validated_url(
                        url, ALLOWED_HOSTS
                    ),
                )
        self.assertEqual(
            SafeHttpsClient._validated_url(
                "https://official.example:443/object?version=1",
                ALLOWED_HOSTS,
            ),
            "https://official.example:443/object?version=1",
        )

    def test_redirect_rejects_port_userinfo_fragment_and_cross_host_before_follow(
        self,
    ) -> None:
        callbacks: list[str] = []
        handler = _StrictRedirectHandler(
            ALLOWED_HOSTS,
            3,
            lambda url, _count: callbacks.append(url),
        )
        request = urllib.request.Request(OBJECT_URL)
        headers = Message()
        invalid = (
            "http://official.example/redirected",
            "https://other.example/redirected",
            "https://official.example:444/redirected",
            "https://user@official.example/redirected",
            "https://official.example/redirected#fragment",
        )
        for url in invalid:
            with self.subTest(url=url):
                self._assert_code(
                    self,
                    "CROSS_HOST_REDIRECT",
                    AcquisitionTransportError,
                    lambda url=url: handler.redirect_request(
                        request, None, 302, "Found", headers, url
                    ),
                )
        self.assertEqual(callbacks, [])

    def test_robots_policy_is_required_and_disallowed_paths_fail_before_network(
        self,
    ) -> None:
        guard = self._guard()
        client = self._client(guard, _OpenerPlan())
        self._assert_code(
            self,
            "ROBOTS_NOT_FROZEN",
            AcquisitionPolicyError,
            lambda: client._request(
                source_catalog_id="SOURCE",
                url=OBJECT_URL,
                allowed_hosts=ALLOWED_HOSTS,
                enforce_robots=True,
            ),
        )
        self.assertEqual(guard.request_count, 0)

        self._install_robots(
            client,
            body=b"User-agent: *\nDisallow: /private\nAllow: /\n",
        )
        self._assert_code(
            self,
            "ROBOTS_DISALLOWED",
            AcquisitionPolicyError,
            lambda: client._request(
                source_catalog_id="SOURCE",
                url=f"https://{HOST}/private/document",
                allowed_hosts=ALLOWED_HOSTS,
                enforce_robots=True,
            ),
        )
        self.assertEqual(guard.request_count, 0)
        self.assertEqual(
            guard.resolve("00_CONTROL/request-ledger.jsonl").read_bytes(), b""
        )

    def test_request_and_redirect_attempts_are_counted_paced_and_resumable(
        self,
    ) -> None:
        guard = self._guard()
        manual = _ManualTime()
        destination = f"https://{CDN_HOST}/final/object.bin"
        response = _FakeResponse(b"ok", url=destination)
        plan = _OpenerPlan(_Redirect(destination, response))
        client = self._client(guard, plan, manual)
        self._install_robots(client, host=HOST, floor=2)
        self._install_robots(client, host=CDN_HOST, floor=5)
        client._last_request = {HOST: 0.0, CDN_HOST: 0.0}

        opened, sequence, retry = client._request(
            source_catalog_id="SOURCE",
            url=OBJECT_URL,
            allowed_hosts=frozenset({HOST, CDN_HOST}),
            enforce_robots=True,
        )
        opened.close()

        self.assertEqual((sequence, retry), (1, 0))
        self.assertEqual(guard.request_count, 2)
        self.assertEqual(manual.sleeps, [2.0, 3.0])
        records = self._ledger(
            guard.resolve("00_CONTROL/request-ledger.jsonl")
        )
        attempts = [
            record
            for record in records
            if record["event"]
            in {"HTTP_REQUEST_ATTEMPT", "HTTP_REDIRECT_ATTEMPT"}
        ]
        self.assertEqual(
            [(record["request_sequence"], record["host"]) for record in attempts],
            [(1, HOST), (2, CDN_HOST)],
        )
        guard.request_count = 0
        guard._load_ledger_state()
        self.assertEqual(guard.request_count, 2)

    def test_content_type_and_declared_size_fail_before_object_publication(
        self,
    ) -> None:
        cases = (
            (
                "content-type",
                _FakeResponse(b"<html>blocked</html>", content_type="text/html"),
                1024,
                "CONTENT_TYPE_MISMATCH",
            ),
            (
                "declared-size",
                _FakeResponse(b"x", content_length="2048"),
                1024,
                "SIZE_LIMIT_EXCEEDED",
            ),
        )
        for name, response, maximum, expected in cases:
            with self.subTest(case=name):
                guard = self._guard(maximum_response_bytes=maximum)
                client = self._client(guard, _OpenerPlan(response))
                self._install_robots(client)
                self._assert_code(
                    self,
                    expected,
                    AcquisitionTransportError,
                    lambda client=client: client.download(
                        **self._download_kwargs()  # type: ignore[arg-type]
                    ),
                )
                self.assertFalse(guard.resolve(OBJECT_PATH).exists())

    def test_stream_size_and_content_length_mismatch_fail_closed(self) -> None:
        cases = (
            (
                "stream-size",
                _FakeResponse(b"12345", content_length=""),
                4,
                AcquisitionStorageError,
                "SIZE_LIMIT_EXCEEDED",
            ),
            (
                "length-mismatch",
                _FakeResponse(b"123", content_length="4"),
                1024,
                AcquisitionIntegrityError,
                "CONTENT_LENGTH_MISMATCH",
            ),
        )
        for name, response, maximum, exception_type, expected in cases:
            with self.subTest(case=name):
                guard = self._guard(maximum_response_bytes=maximum)
                client = self._client(guard, _OpenerPlan(response))
                self._install_robots(client)
                self._assert_code(
                    self,
                    expected,
                    exception_type,
                    lambda client=client: client.download(
                        **self._download_kwargs()  # type: ignore[arg-type]
                    ),
                )
                self.assertFalse(guard.resolve(OBJECT_PATH).exists())

    def test_existing_target_without_provenance_is_never_overwritten(self) -> None:
        guard = self._guard()
        target = guard.resolve(OBJECT_PATH)
        target.write_bytes(b"preexisting")
        client = self._client(guard, _OpenerPlan())
        self._assert_code(
            self,
            "PROVENANCE_INSUFFICIENT",
            AcquisitionIntegrityError,
            lambda: client.download(
                **self._download_kwargs()  # type: ignore[arg-type]
            ),
        )
        self.assertEqual(target.read_bytes(), b"preexisting")
        self.assertEqual(guard.request_count, 0)

    def test_replay_rejects_tampered_digest_and_redigested_binding_change(
        self,
    ) -> None:
        guard = self._guard()
        _client, receipt, _plan = self._successful_download(guard)
        target = guard.resolve(OBJECT_PATH)
        original_object = target.read_bytes()
        sidecar = guard.resolve(
            SafeHttpsClient._sidecar_relative(OBJECT_PATH)
        )
        original_sidecar = sidecar.read_bytes()

        record = json.loads(original_sidecar)
        record["sidecar_digest"] = "0" * 64
        sidecar.write_bytes(canonical_json_bytes(record) + b"\n")
        replay = self._client(guard, _OpenerPlan())
        self._assert_code(
            self,
            "PROVENANCE_INSUFFICIENT",
            AcquisitionIntegrityError,
            lambda: replay.download(
                **self._download_kwargs()  # type: ignore[arg-type]
            ),
        )

        sidecar.write_bytes(original_sidecar)
        record = json.loads(original_sidecar)
        record["terms_reference"] = f"https://{HOST}/changed-terms"
        record["sidecar_digest"] = canonical_sha256(
            record, exclude_fields=("sidecar_digest",)
        )
        sidecar.write_bytes(canonical_json_bytes(record) + b"\n")
        replay = self._client(guard, _OpenerPlan())
        self._assert_code(
            self,
            "PROVENANCE_INSUFFICIENT",
            AcquisitionIntegrityError,
            lambda: replay.download(
                **self._download_kwargs()  # type: ignore[arg-type]
            ),
        )
        self.assertEqual(target.read_bytes(), original_object)
        self.assertEqual(receipt.local_sha256, hashlib.sha256(original_object).hexdigest())

    def test_exact_replay_reruns_validator_without_network_or_duplicate_event(
        self,
    ) -> None:
        guard = self._guard()
        calls: list[bytes] = []

        def validator(path: Path) -> None:
            calls.append(path.read_bytes())
            if path.read_bytes() != b"validated":
                raise AcquisitionIntegrityError(
                    "unexpected replay bytes", code="SYNTHETIC_INVALID"
                )

        _client, first, _plan = self._successful_download(
            guard, body=b"validated", validators=(validator,)
        )
        replay = self._client(guard, _OpenerPlan())
        second = replay.download(
            **self._download_kwargs(validators=(validator,))  # type: ignore[arg-type]
        )

        self.assertEqual(calls, [b"validated", b"validated"])
        self.assertEqual(first, second)
        events = self._ledger(guard.resolve("00_CONTROL/object-ledger.jsonl"))
        self.assertEqual([event["event"] for event in events], ["OBJECT_PUBLISHED"])

    def test_target_without_sidecar_recovers_only_from_bound_intent(self) -> None:
        guard = self._guard()
        client = self._client(guard, _OpenerPlan(_FakeResponse(b"recoverable")))
        self._install_robots(client)
        original_write = guard.write_json_absent

        class SyntheticCrash(RuntimeError):
            pass

        def fail_at_sidecar(relative: str, value: object) -> str:
            if relative.startswith("01_PROVENANCE/source-sidecars/"):
                raise SyntheticCrash("after object publication")
            return original_write(relative, value)

        guard.write_json_absent = fail_at_sidecar  # type: ignore[method-assign]
        with self.assertRaises(SyntheticCrash):
            client.download(
                **self._download_kwargs()  # type: ignore[arg-type]
            )
        guard.write_json_absent = original_write  # type: ignore[method-assign]
        self.assertEqual(guard.resolve(OBJECT_PATH).read_bytes(), b"recoverable")
        self.assertFalse(
            guard.resolve(SafeHttpsClient._sidecar_relative(OBJECT_PATH)).exists()
        )

        validator_calls: list[bytes] = []
        replay = self._client(guard, _OpenerPlan())
        receipt = replay.download(
            **self._download_kwargs(
                validators=(lambda path: validator_calls.append(path.read_bytes()),)
            )  # type: ignore[arg-type]
        )
        self.assertEqual(validator_calls, [b"recoverable"])
        self.assertEqual(receipt.local_sha256, hashlib.sha256(b"recoverable").hexdigest())
        events = self._ledger(guard.resolve("00_CONTROL/object-ledger.jsonl"))
        self.assertEqual([event["event"] for event in events], ["OBJECT_RECONCILED"])

    def test_sidecar_without_target_is_a_conflict_and_does_not_redownload(self) -> None:
        guard = self._guard()
        self._successful_download(guard)
        sidecar = guard.resolve(SafeHttpsClient._sidecar_relative(OBJECT_PATH))
        guard.resolve(OBJECT_PATH).unlink()
        replay = self._client(guard, _OpenerPlan())

        self._assert_code(
            self,
            "PROVENANCE_INSUFFICIENT",
            AcquisitionIntegrityError,
            lambda: replay.download(
                **self._download_kwargs()  # type: ignore[arg-type]
            ),
        )
        self.assertTrue(sidecar.is_file())
        self.assertEqual(guard.request_count, 1)

    def test_missing_object_ledger_event_is_reconciled_on_exact_replay(self) -> None:
        guard = self._guard()
        self._successful_download(guard)
        ledger = guard.resolve("00_CONTROL/object-ledger.jsonl")
        ledger.write_bytes(b"")

        replay = self._client(guard, _OpenerPlan())
        receipt = replay.download(
            **self._download_kwargs()  # type: ignore[arg-type]
        )
        events = self._ledger(ledger)
        self.assertEqual([event["event"] for event in events], ["OBJECT_RECONCILED"])
        self.assertEqual(events[0]["sidecar_digest"], receipt.sidecar_digest)
        self.assertEqual(guard.request_count, 1)

    def test_retry_after_is_honored_with_a_bounded_active_run(self) -> None:
        client = self._client(self._guard())
        self.assertEqual(client._retry_delay({"Retry-After": "3600"}, 0), 3600)
        self._assert_code(
            self,
            "RETRY_AFTER_EXCEEDS_BOUND",
            AcquisitionPolicyError,
            lambda: client._retry_delay({"Retry-After": "3601"}, 0),
        )
        self._assert_code(
            self,
            "RETRY_AFTER_EXCEEDS_BOUND",
            AcquisitionPolicyError,
            lambda: client._retry_delay(
                {"Retry-After": "Wed, 01 Jan 2099 00:00:00 GMT"}, 0
            ),
        )
        self.assertEqual(client._retry_delay({"Retry-After": "invalid"}, 0), 1)


if __name__ == "__main__":
    unittest.main()
