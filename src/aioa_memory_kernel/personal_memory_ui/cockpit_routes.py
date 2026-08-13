"""Authenticated presentation-only route for the unified demo cockpit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aioa_memory_kernel.demo_cockpit import CockpitShell

from .auth import OwnerSession


def register_cockpit_route(
    *,
    app: FastAPI,
    templates: Jinja2Templates,
    require_session: Callable[[Request], OwnerSession],
    context: Callable[..., dict[str, Any]],
    cockpit_shell: CockpitShell,
) -> None:
    """Register one GET-only route with no business or provider dependency."""

    if not isinstance(app, FastAPI):
        raise TypeError("cockpit app must be FastAPI")
    if not isinstance(cockpit_shell, CockpitShell):
        raise TypeError("cockpit shell must be typed")

    @app.get("/memory/demo", response_class=HTMLResponse)
    def cockpit(
        request: Request,
        mode: str | None = Query(default=None, max_length=64),
    ):
        value = require_session(request)
        view = cockpit_shell.project(mode)
        return templates.TemplateResponse(
            request,
            "demo.html",
            context(request, value, cockpit=view, message=""),
        )


__all__ = ["register_cockpit_route"]
