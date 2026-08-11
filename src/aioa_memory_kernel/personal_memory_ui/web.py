"""FastAPI/Jinja2/HTMX owner workspace for Step 35."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from aioa_memory_kernel.contracts.enums import PersonalMemorySpaceState
from aioa_memory_kernel.state_machines.personal_memory import (
    personal_memory_transition_allowed,
)

from .auth import (
    OIDC_FLOW_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    OidcClient,
    OidcSettings,
    OwnerSession,
    OwnerSessionStore,
    current_time,
    pkce_challenge,
)
from .backend import PersonalMemoryUiBackend
from .models import (
    OwnerPrincipal,
    PersonalMemoryUiError,
)


_PACKAGE = Path(__file__).resolve().parent
_TEMPLATES = _PACKAGE / "templates"
_STATIC = _PACKAGE / "static"
_SAFE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_MAXIMUM_FORM_BYTES = 32 * 1024
_MAXIMUM_FORM_FIELDS = 32


def _bounded_input(value: str, name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum_bytes:
        raise PersonalMemoryUiError(f"{name} exceeds its bound")
    return value


def _action_token(prefix: str) -> str:
    return f"ui-{prefix}-{secrets.token_hex(16)}"


def create_personal_memory_app(
    *,
    backend: PersonalMemoryUiBackend,
    oidc_client: OidcClient,
    oidc_settings: OidcSettings,
    session_store: OwnerSessionStore,
    clock=current_time,
    action_token_factory: Callable[[str], str] = _action_token,
) -> FastAPI:
    """Create an app only from explicitly injected auth and backend boundaries."""

    app = FastAPI(
        title="AIOA Personal Memory",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(
        env=Environment(
            loader=FileSystemLoader(str(_TEMPLATES)),
            autoescape=select_autoescape(("html", "xml"), default=True),
        )
    )
    app.mount("/memory/static", StaticFiles(directory=str(_STATIC)), name="memory-static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    def session(request: Request) -> OwnerSession | None:
        handle = request.cookies.get(SESSION_COOKIE_NAME, "")
        return session_store.get_session(handle, now=clock())

    def require_session(request: Request) -> OwnerSession:
        value = session(request)
        if value is None:
            raise PersonalMemoryUiError("authentication required")
        return value

    async def require_csrf(request: Request, value: OwnerSession) -> dict[str, str]:
        origin = request.headers.get("origin")
        if origin is not None and not secrets.compare_digest(
            origin.rstrip("/"), oidc_settings.public_origin.rstrip("/")
        ):
            raise PersonalMemoryUiError("cross-site mutation denied")
        body = await request.body()
        if len(body) > _MAXIMUM_FORM_BYTES:
            raise PersonalMemoryUiError("mutation payload exceeds its bound")
        form = await request.form()
        if len(form) > _MAXIMUM_FORM_FIELDS:
            raise PersonalMemoryUiError("mutation field count exceeds its bound")
        submitted = str(form.get("csrf_token", ""))
        if not submitted or not secrets.compare_digest(submitted, value.csrf_token):
            raise PersonalMemoryUiError("CSRF validation failed")
        values = {str(key): str(item) for key, item in form.multi_items()}
        for key, item in values.items():
            _bounded_input(key, "form field name", 128)
            _bounded_input(item, key, 16 * 1024)
        return values

    def next_action_token(prefix: str) -> str:
        return _bounded_input(action_token_factory(prefix), "action token", 255)

    def context(request: Request, value: OwnerSession, **extra):
        return {
            "request": request,
            "principal": value.principal,
            "csrf_token": value.csrf_token,
            "private_memory_notice": (
                "This is your private Personal Memory. It can personalize future "
                "responses, but it is not an official source or canonical evidence."
            ),
            "action_token": next_action_token,
            **extra,
        }

    @app.exception_handler(PersonalMemoryUiError)
    async def ui_error(request: Request, error: PersonalMemoryUiError):
        value = session(request)
        if value is None:
            response = RedirectResponse(
                "/memory/login?return_path=" + quote(request.url.path, safe="/"),
                status_code=303,
            )
            return response
        return templates.TemplateResponse(
            request,
            "error.html",
            context(request, value, message=error.safe_message),
            status_code=error.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        # Never serialize exception text, SQL, paths, or credentials to the browser.
        value = session(request)
        if value is None:
            return HTMLResponse("Request failed safely.", status_code=500)
        return templates.TemplateResponse(
            request,
            "error.html",
            context(
                request,
                value,
                message="The request failed safely. Refresh and try again.",
            ),
            status_code=500,
        )

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/memory", status_code=303)

    @app.get("/memory/login", response_class=HTMLResponse)
    def login(request: Request, return_path: str = "/memory"):
        return_path = _bounded_input(return_path, "return_path", 1024)
        handle, pending = session_store.create_pending(
            return_path=return_path, now=clock()
        )
        target = oidc_client.authorization_url(
            state=pending.state,
            nonce=pending.nonce,
            code_challenge=pkce_challenge(pending.code_verifier),
        )
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            OIDC_FLOW_COOKIE_NAME,
            handle,
            max_age=600,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/memory/oidc/callback",
        )
        return response

    @app.get("/memory/oidc/callback", response_class=HTMLResponse)
    def oidc_callback(request: Request, code: str = "", state: str = ""):
        code = _bounded_input(code, "OIDC code", 4096)
        state = _bounded_input(state, "OIDC state", 256)
        handle = request.cookies.get(OIDC_FLOW_COOKIE_NAME, "")
        pending = session_store.consume_pending(handle, now=clock())
        if pending is None or not state or not secrets.compare_digest(state, pending.state):
            return HTMLResponse("OIDC callback failed safely.", status_code=401)
        try:
            principal = oidc_client.authenticate(
                code=code,
                code_verifier=pending.code_verifier,
                nonce=pending.nonce,
            )
            session_handle, _ = session_store.create_session(principal, now=clock())
            session_store.delete_session(request.cookies.get(SESSION_COOKIE_NAME, ""))
        except Exception:
            return HTMLResponse("OIDC callback failed safely.", status_code=401)
        response = RedirectResponse(pending.return_path, status_code=303)
        response.delete_cookie(OIDC_FLOW_COOKIE_NAME, path="/memory/oidc/callback")
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_handle,
            max_age=8 * 60 * 60,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/memory",
        )
        return response

    @app.post("/memory/logout")
    async def logout(request: Request):
        value = require_session(request)
        await require_csrf(request, value)
        session_store.delete_session(request.cookies.get(SESSION_COOKIE_NAME, ""))
        response = RedirectResponse("/memory/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/memory")
        return response

    @app.get("/memory", response_class=HTMLResponse)
    def dashboard(request: Request, message: str = ""):
        value = require_session(request)
        view = backend.dashboard(value.principal)
        safe_message = message[:256]
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            context(request, value, dashboard=view, message=safe_message),
        )

    @app.get("/memory/slots/{space_id}", response_class=HTMLResponse)
    def slot_detail(request: Request, space_id: str, message: str = ""):
        space_id = _bounded_input(space_id, "space_id", 255)
        value = require_session(request)
        slot, patches = backend.slot_detail(value.principal, space_id)
        current = PersonalMemorySpaceState(slot.state)
        transitions = tuple(
            candidate.value
            for candidate in PersonalMemorySpaceState
            if personal_memory_transition_allowed(current, candidate)
        )
        return templates.TemplateResponse(
            request,
            "slot.html",
            context(
                request,
                value,
                slot=slot,
                patches=patches,
                allowed_transitions=transitions,
                message=message[:256],
            ),
        )

    def redirect_slot(space_id: str, result) -> RedirectResponse:
        return RedirectResponse(
            f"/memory/slots/{quote(space_id, safe='')}?message="
            + quote(result.message, safe=""),
            status_code=303,
        )

    @app.post("/memory/slots/{space_id}/configure")
    async def configure_slot(request: Request, space_id: str):
        space_id = _bounded_input(space_id, "space_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.configure_slot(
            value.principal,
            space_id=space_id,
            display_name=form.get("display_name", ""),
            slot_hash=form.get("slot_hash", ""),
            expected_state_version=int(form.get("expected_state_version", "-1")),
            expected_configuration_version=int(
                form.get("expected_configuration_version", "-1")
            ),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return redirect_slot(space_id, result)

    @app.post("/memory/slots/{space_id}/transition")
    async def transition_slot(request: Request, space_id: str):
        space_id = _bounded_input(space_id, "space_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.transition_slot(
            value.principal,
            space_id=space_id,
            target_state=form.get("target_state", ""),
            slot_hash=form.get("slot_hash", ""),
            expected_state_version=int(form.get("expected_state_version", "-1")),
            expected_configuration_version=int(
                form.get("expected_configuration_version", "-1")
            ),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return redirect_slot(space_id, result)

    @app.post("/memory/slots/{space_id}/model-bindings")
    async def update_model_binding(request: Request, space_id: str):
        space_id = _bounded_input(space_id, "space_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.update_model_binding(
            value.principal,
            space_id=space_id,
            action=form.get("action", ""),
            provider_id=form.get("provider_id", ""),
            model_id=form.get("model_id", ""),
            model_revision=form.get("model_revision", ""),
            binding_id=form.get("binding_id", ""),
            binding_hash=form.get("binding_hash", ""),
            slot_hash=form.get("slot_hash", ""),
            expected_state_version=int(form.get("expected_state_version", "-1")),
            expected_configuration_version=int(
                form.get("expected_configuration_version", "-1")
            ),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return redirect_slot(space_id, result)

    @app.post("/memory/proposals/{proposal_id}/approve")
    async def approve_proposal(request: Request, proposal_id: str):
        proposal_id = _bounded_input(proposal_id, "proposal_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.approve_proposal(
            value.principal,
            proposal_id=proposal_id,
            proposal_hash=form.get("proposal_hash", ""),
            expected_state_version=int(form.get("expected_state_version", "-1")),
            expected_state_hash=form.get("expected_state_hash", ""),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return RedirectResponse(
            "/memory?message=" + quote(result.message, safe=""), status_code=303
        )

    @app.post("/memory/patches/{proposal_id}/revoke")
    async def revoke_patch(request: Request, proposal_id: str):
        proposal_id = _bounded_input(proposal_id, "proposal_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.revoke_patch(
            value.principal,
            proposal_id=proposal_id,
            state_hash=form.get("state_hash", ""),
            patch_hash=form.get("patch_hash", ""),
            expected_state_version=int(form.get("expected_state_version", "-1")),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return redirect_slot(form.get("space_id", ""), result)

    @app.post("/memory/slots/{space_id}/export")
    async def export_slot(request: Request, space_id: str):
        space_id = _bounded_input(space_id, "space_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.export_slot(
            value.principal,
            space_id=space_id,
            slot_hash=form.get("slot_hash", ""),
            expected_state_version=int(form.get("expected_state_version", "-1")),
            expected_configuration_version=int(
                form.get("expected_configuration_version", "-1")
            ),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return redirect_slot(space_id, result)

    @app.post("/memory/patches/{proposal_id}/delete")
    async def delete_patch(request: Request, proposal_id: str):
        proposal_id = _bounded_input(proposal_id, "proposal_id", 255)
        value = require_session(request)
        form = await require_csrf(request, value)
        result = backend.delete_patch(
            value.principal,
            proposal_id=proposal_id,
            state_hash=form.get("state_hash", ""),
            patch_hash=form.get("patch_hash", ""),
            slot_hash=form.get("slot_hash", ""),
            idempotency_key=form.get("idempotency_key", ""),
        )
        return redirect_slot(form.get("space_id", ""), result)

    return app


__all__ = ["create_personal_memory_app"]
