"""Canonical import-safe ASGI surface for the Memory Patch demo runtime."""

from .composition import create_canonical_asgi_app


app = create_canonical_asgi_app()


__all__ = ["app"]
