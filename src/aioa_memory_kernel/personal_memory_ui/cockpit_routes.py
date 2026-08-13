"""Authenticated routes for the unified cockpit and bounded D3 run projection."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aioa_memory_kernel.demo_cockpit import CockpitShell

from .auth import SESSION_COOKIE_NAME, OwnerSession
from .backend import PersonalMemoryUiBackend
from .models import (
    PersonalMemoryUiAccessDenied,
    PersonalMemoryUiError,
    PersonalMemoryUiNotFound,
)

def _bounded(value: object, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PersonalMemoryUiError(f"{name} is invalid")
    return value


def _session_digest(request: Request) -> str:
    handle = request.cookies.get(SESSION_COOKIE_NAME, "")
    if (
        not handle
        or len(handle) > 256
        or not handle.isascii()
        or any(ord(character) < 33 or ord(character) == 127 for character in handle)
    ):
        raise PersonalMemoryUiError("authentication required")
    return hashlib.sha256(handle.encode("ascii")).hexdigest()


def register_cockpit_route(
    *,
    app: FastAPI,
    templates: Jinja2Templates,
    require_session: Callable[[Request], OwnerSession],
    context: Callable[..., dict[str, Any]],
    cockpit_shell: CockpitShell,
    backend: PersonalMemoryUiBackend,
    require_csrf: Callable[[Request, OwnerSession], Any] | None = None,
    jury_flow: object | None = None,
) -> None:
    """Register one shared page plus optional current-mode run endpoints."""

    if not isinstance(app, FastAPI):
        raise TypeError("cockpit app must be FastAPI")
    if not isinstance(cockpit_shell, CockpitShell):
        raise TypeError("cockpit shell must be typed")

    def current_jury_flow() -> object | None:
        if jury_flow is None or not bool(getattr(jury_flow, "is_bound", True)):
            return None
        return jury_flow

    @app.get("/memory/demo", response_class=HTMLResponse)
    def cockpit(
        request: Request,
        mode: str | None = Query(default=None, max_length=64),
        run: str | None = Query(default=None, max_length=128),
    ):
        value = require_session(request)
        view = cockpit_shell.project(mode)
        jury_run = None
        dashboard = None
        cases = ()
        service = current_jury_flow()
        if service is not None and view.selected_mode.value == "memory_patch":
            cases = tuple(service.cases)
            dashboard = backend.dashboard(value.principal)
            if run:
                try:
                    jury_run = service.get(
                        value.principal,
                        session_digest=_session_digest(request),
                        run_id=_bounded(run, "run id", 128),
                    )
                except PermissionError:
                    raise PersonalMemoryUiAccessDenied() from None
                except KeyError:
                    raise PersonalMemoryUiNotFound() from None
        return templates.TemplateResponse(
            request,
            "demo.html",
            context(
                request,
                value,
                cockpit=view,
                jury_enabled=service is not None,
                jury_cases=cases,
                jury_run=jury_run,
                dashboard=dashboard,
                message="",
            ),
        )

    if jury_flow is None:
        return
    if require_csrf is None:
        raise TypeError("D3 jury routes require the existing CSRF boundary")

    @app.post("/memory/jury-runs", include_in_schema=False)
    async def create_jury_run(request: Request):
        value = require_session(request)
        form = await require_csrf(request, value)
        service = current_jury_flow()
        if service is None:
            raise PersonalMemoryUiNotFound()
        try:
            projection = service.submit(
                value.principal,
                session_digest=_session_digest(request),
                case_id=_bounded(form.get("case_id", ""), "case id", 128),
                idempotency_key=_bounded(
                    form.get("idempotency_key", ""),
                    "idempotency key",
                    255,
                ),
            )
        except PermissionError:
            raise PersonalMemoryUiAccessDenied() from None
        except (KeyError, ValueError):
            raise PersonalMemoryUiError("guided run request is invalid") from None
        except RuntimeError:
            raise PersonalMemoryUiError("guided run is temporarily unavailable") from None
        return RedirectResponse(
            "/memory/demo?run=" + quote(projection.run_id, safe=""),
            status_code=303,
        )

    @app.get(
        "/memory/jury-runs/{run_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def jury_run_status(request: Request, run_id: str):
        value = require_session(request)
        service = current_jury_flow()
        if service is None:
            raise PersonalMemoryUiNotFound()
        try:
            projection = service.get(
                value.principal,
                session_digest=_session_digest(request),
                run_id=_bounded(run_id, "run id", 128),
            )
        except PermissionError:
            raise PersonalMemoryUiAccessDenied() from None
        except KeyError:
            raise PersonalMemoryUiNotFound() from None
        return templates.TemplateResponse(
            request,
            "partials/demo_current_run.html",
            context(request, value, jury_run=projection, message=""),
        )


__all__ = ["register_cockpit_route"]
