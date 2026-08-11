"""OIDC Authorization Code + PKCE and opaque server-side owner sessions.

The browser receives only random opaque handles.  ID/access tokens are never
stored in cookies or exposed to templates, and tenant/owner identities are
derived only from a cryptographically verified ID token.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlencode

import httpx
import jwt

from .models import OwnerPrincipal


OIDC_ALLOWED_ALGORITHMS = ("RS256", "ES256")
SESSION_COOKIE_NAME = "aioa_pm_session"
OIDC_FLOW_COOKIE_NAME = "aioa_pm_oidc_flow"
SESSION_TTL_SECONDS = 8 * 60 * 60
OIDC_FLOW_TTL_SECONDS = 10 * 60
MAXIMUM_SERVER_SESSIONS = 10_000


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _token_hash(value: str) -> str:
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
        if not self.issuer.startswith("https://"):
            raise ValueError("OIDC issuer must use HTTPS")
        if not self.redirect_uri.startswith("https://"):
            raise ValueError("OIDC redirect URI must use HTTPS")
        if not self.public_origin.startswith("https://"):
            raise ValueError("public origin must use HTTPS")
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
        if not return_path.startswith("/memory") or return_path.startswith("//"):
            return_path = "/memory"
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
            self._pending[_token_hash(handle)] = pending
            return handle, pending

    def consume_pending(
        self, handle: str, *, now: float
    ) -> PendingOidcAuthorization | None:
        if not handle:
            return None
        with self._lock:
            self._purge(now)
            return self._pending.pop(_token_hash(handle), None)

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
            self._sessions[_token_hash(handle)] = session
            return handle, session

    def get_session(self, handle: str, *, now: float) -> OwnerSession | None:
        if not handle:
            return None
        with self._lock:
            self._purge(now)
            return self._sessions.get(_token_hash(handle))

    def delete_session(self, handle: str) -> None:
        if not handle:
            return
        with self._lock:
            self._sessions.pop(_token_hash(handle), None)


class HttpxOidcClient:
    """Generic standards-based OIDC public-client implementation."""

    def __init__(
        self,
        settings: OidcSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=10.0, follow_redirects=False)
        self._metadata: Mapping[str, object] | None = None

    def _provider_metadata(self) -> Mapping[str, object]:
        if self._metadata is None:
            response = self._client.get(
                self.settings.issuer.rstrip("/") + "/.well-known/openid-configuration"
            )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, Mapping) or value.get("issuer") != self.settings.issuer:
                raise ValueError("OIDC provider metadata issuer mismatch")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                endpoint = value.get(field)
                if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
                    raise ValueError(f"OIDC metadata {field} is invalid")
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
        response = self._client.post(
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
        response.raise_for_status()
        token_response = response.json()
        if not isinstance(token_response, Mapping) or not isinstance(
            token_response.get("id_token"), str
        ):
            raise ValueError("OIDC token response omitted the ID token")
        header = jwt.get_unverified_header(str(token_response["id_token"]))
        if header.get("alg") not in OIDC_ALLOWED_ALGORITHMS or not isinstance(
            header.get("kid"), str
        ):
            raise ValueError("OIDC ID token algorithm or key identity is invalid")
        jwks_response = self._client.get(str(metadata["jwks_uri"]))
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
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
