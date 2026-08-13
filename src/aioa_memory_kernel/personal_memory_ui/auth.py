"""OIDC Authorization Code + PKCE and opaque server-side owner sessions.

The browser receives only random opaque handles.  ID/access tokens are never
stored in cookies or exposed to templates, and tenant/owner identities are
derived only from a cryptographically verified ID token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import SplitResult, urlencode, urlsplit

import httpx
import jwt

from .models import OwnerPrincipal


OIDC_ALLOWED_ALGORITHMS = ("RS256", "ES256")
SESSION_COOKIE_NAME = "aioa_pm_session"
OIDC_FLOW_COOKIE_NAME = "aioa_pm_oidc_flow"
SESSION_TTL_SECONDS = 8 * 60 * 60
OIDC_FLOW_TTL_SECONDS = 10 * 60
MAXIMUM_SERVER_SESSIONS = 10_000
MAXIMUM_OIDC_JSON_BYTES = 256 * 1024
OIDC_CALLBACK_PATH = "/memory/oidc/callback"


def _validated_https_url(
    value: str,
    name: str,
    *,
    origin_only: bool = False,
) -> SplitResult:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} is invalid")
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError(f"{name} is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTPS URL without credentials or fragments")
    if origin_only and (parsed.path not in ("", "/") or parsed.query):
        raise ValueError(f"{name} must be an HTTPS origin")
    return parsed


def _url_origin(parsed: SplitResult) -> tuple[str, str, int]:
    hostname = parsed.hostname
    if hostname is None:  # pragma: no cover - guarded by _validated_https_url
        raise ValueError("URL hostname is required")
    return parsed.scheme, hostname.lower(), parsed.port or 443


def _safe_return_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or "%" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return "/memory"
    parsed = urlsplit(value)
    segments = tuple(segment for segment in parsed.path.split("/") if segment)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or (
            parsed.path not in ("/memory", "/memory/")
            and not parsed.path.startswith("/memory/")
        )
        or any(segment in (".", "..") for segment in segments)
    ):
        return "/memory"
    return value


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _token_hash(value: str) -> str | None:
    """Hash one bounded opaque ASCII handle; reject hostile cookie bytes."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        return None
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class OidcSettings:
    issuer: str
    client_id: str
    redirect_uri: str
    public_origin: str
    tenant_claim: str = "tenant_id"
    owner_claim: str = "owner_user_id"
    display_name_claim: str = "name"
    scopes: tuple[str, ...] = ("openid", "profile")

    def __post_init__(self) -> None:
        issuer = _validated_https_url(self.issuer, "OIDC issuer")
        redirect = _validated_https_url(self.redirect_uri, "OIDC redirect URI")
        public = _validated_https_url(
            self.public_origin, "public origin", origin_only=True
        )
        if issuer.query:
            raise ValueError("OIDC issuer must not contain a query")
        if (
            _url_origin(redirect) != _url_origin(public)
            or redirect.path != OIDC_CALLBACK_PATH
            or redirect.query
        ):
            raise ValueError("OIDC redirect URI must be the public-origin callback")
        if not self.client_id or any(not value for value in self.scopes):
            raise ValueError("OIDC client and scopes are required")
        if "openid" not in self.scopes:
            raise ValueError("OIDC openid scope is required")


@dataclass(frozen=True, slots=True)
class PendingOidcAuthorization:
    state: str
    nonce: str
    code_verifier: str
    return_path: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class OwnerSession:
    principal: OwnerPrincipal
    csrf_token: str
    created_at: float
    expires_at: float


class OidcClient(Protocol):
    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str
    ) -> str:
        ...

    def authenticate(self, *, code: str, code_verifier: str, nonce: str) -> OwnerPrincipal:
        ...


class OwnerSessionStore(Protocol):
    def create_pending(self, *, return_path: str, now: float) -> tuple[str, PendingOidcAuthorization]:
        ...

    def consume_pending(self, handle: str, *, now: float) -> PendingOidcAuthorization | None:
        ...

    def create_session(self, principal: OwnerPrincipal, *, now: float) -> tuple[str, OwnerSession]:
        ...

    def get_session(self, handle: str, *, now: float) -> OwnerSession | None:
        ...

    def delete_session(self, handle: str) -> None:
        ...


class MemoryOwnerSessionStore:
    """Bounded server-side session store for local and controlled deployments.

    Production deployments inject a durable server-side implementation with
    the same port.  Only SHA-256 hashes of opaque cookie handles are retained.
    """

    def __init__(self, *, maximum_sessions: int = MAXIMUM_SERVER_SESSIONS) -> None:
        if not 1 <= maximum_sessions <= MAXIMUM_SERVER_SESSIONS:
            raise ValueError("maximum_sessions is outside the safe bound")
        self._maximum = maximum_sessions
        self._pending: dict[str, PendingOidcAuthorization] = {}
        self._sessions: dict[str, OwnerSession] = {}
        self._lock = threading.RLock()

    def _purge(self, now: float) -> None:
        self._pending = {
            key: value for key, value in self._pending.items() if value.expires_at > now
        }
        self._sessions = {
            key: value for key, value in self._sessions.items() if value.expires_at > now
        }

    def create_pending(
        self, *, return_path: str, now: float
    ) -> tuple[str, PendingOidcAuthorization]:
        return_path = _safe_return_path(return_path)
        with self._lock:
            self._purge(now)
            if len(self._pending) >= self._maximum:
                raise RuntimeError("OIDC flow capacity is unavailable")
            handle = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            pending = PendingOidcAuthorization(
                state=secrets.token_urlsafe(32),
                nonce=secrets.token_urlsafe(32),
                code_verifier=verifier,
                return_path=return_path,
                expires_at=now + OIDC_FLOW_TTL_SECONDS,
            )
            handle_hash = _token_hash(handle)
            if handle_hash is None:  # pragma: no cover - generated ASCII token
                raise RuntimeError("OIDC flow handle generation failed safely")
            self._pending[handle_hash] = pending
            return handle, pending

    def consume_pending(
        self, handle: str, *, now: float
    ) -> PendingOidcAuthorization | None:
        if not handle:
            return None
        with self._lock:
            self._purge(now)
            handle_hash = _token_hash(handle)
            return None if handle_hash is None else self._pending.pop(handle_hash, None)

    def create_session(
        self, principal: OwnerPrincipal, *, now: float
    ) -> tuple[str, OwnerSession]:
        if not isinstance(principal, OwnerPrincipal):
            raise TypeError("principal must be OwnerPrincipal")
        with self._lock:
            self._purge(now)
            if len(self._sessions) >= self._maximum:
                raise RuntimeError("owner session capacity is unavailable")
            handle = secrets.token_urlsafe(48)
            session = OwnerSession(
                principal=principal,
                csrf_token=secrets.token_urlsafe(32),
                created_at=now,
                expires_at=now + SESSION_TTL_SECONDS,
            )
            handle_hash = _token_hash(handle)
            if handle_hash is None:  # pragma: no cover - generated ASCII token
                raise RuntimeError("owner session handle generation failed safely")
            self._sessions[handle_hash] = session
            return handle, session

    def get_session(self, handle: str, *, now: float) -> OwnerSession | None:
        if not handle:
            return None
        with self._lock:
            self._purge(now)
            handle_hash = _token_hash(handle)
            return None if handle_hash is None else self._sessions.get(handle_hash)

    def delete_session(self, handle: str) -> None:
        if not handle:
            return
        with self._lock:
            handle_hash = _token_hash(handle)
            if handle_hash is not None:
                self._sessions.pop(handle_hash, None)


class HttpxOidcClient:
    """Generic standards-based OIDC public-client implementation."""

    def __init__(
        self,
        settings: OidcSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._closed = False
        self._client = client or httpx.Client(timeout=10.0, follow_redirects=False)
        self._metadata: Mapping[str, object] | None = None

    def close(self) -> None:
        """Close only the HTTP client created by this adapter."""

        if self._owns_client and not self._closed:
            self._client.close()
            self._closed = True

    def _bounded_json(
        self,
        method: str,
        url: str,
        **request_arguments: object,
    ) -> object:
        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("OIDC JSON contains a duplicate field")
                result[key] = value
            return result

        def reject_constant(value: str) -> object:
            raise ValueError("OIDC JSON contains a non-finite number")

        with self._client.stream(method, url, **request_arguments) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    declared_bytes = int(declared)
                except ValueError as error:
                    raise ValueError("OIDC response length is invalid") from error
                if declared_bytes < 0 or declared_bytes > MAXIMUM_OIDC_JSON_BYTES:
                    raise ValueError("OIDC response exceeds its bound")
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(chunk) > MAXIMUM_OIDC_JSON_BYTES - len(body):
                    raise ValueError("OIDC response exceeds its bound")
                body.extend(chunk)
        try:
            return json.loads(
                bytes(body).decode("utf-8", errors="strict"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("OIDC response JSON is invalid") from error

    def _provider_metadata(self) -> Mapping[str, object]:
        if self._metadata is None:
            value = self._bounded_json(
                "GET",
                self.settings.issuer.rstrip("/")
                + "/.well-known/openid-configuration",
            )
            if not isinstance(value, Mapping) or value.get("issuer") != self.settings.issuer:
                raise ValueError("OIDC provider metadata issuer mismatch")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                endpoint = value.get(field)
                if not isinstance(endpoint, str):
                    raise ValueError(f"OIDC metadata {field} is invalid")
                _validated_https_url(endpoint, f"OIDC metadata {field}")
            self._metadata = value
        return self._metadata

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str
    ) -> str:
        endpoint = str(self._provider_metadata()["authorization_endpoint"])
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "scope": " ".join(self.settings.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{query}"

    def authenticate(
        self, *, code: str, code_verifier: str, nonce: str
    ) -> OwnerPrincipal:
        if not code or not code_verifier or not nonce:
            raise ValueError("OIDC callback values are incomplete")
        metadata = self._provider_metadata()
        token_response = self._bounded_json(
            "POST",
            str(metadata["token_endpoint"]),
            data={
                "grant_type": "authorization_code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        if not isinstance(token_response, Mapping) or not isinstance(
            token_response.get("id_token"), str
        ):
            raise ValueError("OIDC token response omitted the ID token")
        header = jwt.get_unverified_header(str(token_response["id_token"]))
        if header.get("alg") not in OIDC_ALLOWED_ALGORITHMS or not isinstance(
            header.get("kid"), str
        ):
            raise ValueError("OIDC ID token algorithm or key identity is invalid")
        jwks = self._bounded_json("GET", str(metadata["jwks_uri"]))
        keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
        if not isinstance(keys, list):
            raise ValueError("OIDC key set is invalid")
        matches = [key for key in keys if isinstance(key, Mapping) and key.get("kid") == header["kid"]]
        if len(matches) != 1:
            raise ValueError("OIDC signing key is unavailable or ambiguous")
        public_key = jwt.PyJWK.from_dict(dict(matches[0])).key
        claims = jwt.decode(
            str(token_response["id_token"]),
            public_key,
            algorithms=[str(header["alg"])],
            audience=self.settings.client_id,
            issuer=self.settings.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError("OIDC nonce mismatch")
        tenant_id = claims.get(self.settings.tenant_claim)
        owner_user_id = claims.get(self.settings.owner_claim)
        subject = claims.get("sub")
        display_name = claims.get(self.settings.display_name_claim, owner_user_id)
        if not all(isinstance(value, str) and value for value in (tenant_id, owner_user_id, subject, display_name)):
            raise ValueError("OIDC owner identity claims are incomplete")
        return OwnerPrincipal(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            oidc_subject=subject,
            display_name=display_name,
        )


def pkce_challenge(code_verifier: str) -> str:
    return _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())


def current_time() -> float:
    return time.time()


__all__ = [
    "HttpxOidcClient",
    "MemoryOwnerSessionStore",
    "OIDC_ALLOWED_ALGORITHMS",
    "OIDC_FLOW_COOKIE_NAME",
    "OidcClient",
    "OidcSettings",
    "OwnerSession",
    "OwnerSessionStore",
    "PendingOidcAuthorization",
    "SESSION_COOKIE_NAME",
    "current_time",
    "pkce_challenge",
]
