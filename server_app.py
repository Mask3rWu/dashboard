"""Compatibility entry point for ``uvicorn server_app:app``."""

from backend.api.server.app import app

__all__ = ["app"]
