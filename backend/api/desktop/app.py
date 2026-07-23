"""Desktop FastAPI application factory."""

from __future__ import annotations

import os
import traceback
import mimetypes
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.desktop.routers.analysis import router as analysis_router
from backend.api.desktop.routers.auth import router as auth_router
from backend.api.desktop.routers.flights import (
    listing_router as flight_listing_router,
    mutation_router as flight_mutation_router,
    raw_router as flight_raw_router,
)
from backend.api.desktop.routers.imports import browse_router as import_browse_router
from backend.api.desktop.routers.imports import router as imports_router
from backend.api.desktop.routers.models import router as models_router
from backend.api.desktop.routers.remote_data import router as remote_data_router
from backend.api.desktop.routers.runtime import bootstrap_router
from backend.api.desktop.routers.runtime import router as runtime_router
from backend.api.desktop.routers.sync import router as sync_router
from backend.api.desktop.routers.users import router as users_router
from backend.database import DATA_DIR


STARTUP_LOG_PATH = os.path.join(DATA_DIR, "startup.log")


def configure_frontend_mime_types() -> None:
    """Make Vite module assets independent of Windows registry MIME mappings.

    ``mimetypes`` imports file associations from Windows. Some managed desktop
    environments register ``.js`` as ``text/plain``; Chromium then rejects the
    Vite bundle because module scripts require a JavaScript MIME type.
    """
    mimetypes.add_type("application/javascript", ".js", strict=True)
    mimetypes.add_type("application/javascript", ".mjs", strict=True)


def startup_log(message: str) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(STARTUP_LOG_PATH, "a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def include_routes(app: FastAPI, *routers) -> None:
    """Append APIRoutes in order so ``app.routes`` remains a flat contract."""
    for router in routers:
        app.router.routes.extend(router.routes)


def create_app(frontend_dir: str) -> FastAPI:
    configure_frontend_mime_types()
    app = FastAPI(title="Flight Analyzer", version="2.0.0")
    app.state.frontend_dir = frontend_dir
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        startup_log(
            f"UNHANDLED API ERROR {request.method} {request.url.path}: "
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
        detail = str(exc) or "Internal Server Error"
        if len(detail) > 1000:
            detail = detail[:1000] + "..."
        return JSONResponse(
            status_code=500,
            content={"detail": detail, "error_type": type(exc).__name__},
        )

    include_routes(
        app,
        bootstrap_router,
        auth_router,
        users_router,
        import_browse_router,
        models_router,
        flight_listing_router,
        remote_data_router,
        runtime_router,
        flight_raw_router,
        sync_router,
        flight_mutation_router,
        imports_router,
        analysis_router,
    )
    if os.path.isdir(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app
