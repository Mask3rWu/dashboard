"""Collaboration-server FastAPI application factory."""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import load_app_config

load_app_config()

from backend import server_database as db
from backend.api.server.routers.auth import capabilities_router, router as auth_router
from backend.api.server.routers.data import router as data_router
from backend.api.server.routers.models import create_router as model_create_router
from backend.api.server.routers.models import delete_router as model_delete_router
from backend.api.server.routers.sync import router as sync_router
from backend.api.server.routers.users import router as users_router


logger = logging.getLogger("flight_analyzer.server")


def _include_routes(app: FastAPI, *routers) -> None:
    for router in routers:
        app.router.routes.extend(router.routes)


def create_app() -> FastAPI:
    app = FastAPI(title="Flight Analyzer Server", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled server error %s %s: %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        detail = str(exc) or "Internal Server Error"
        return JSONResponse(
            status_code=500,
            content={"detail": detail[:1000], "error_type": type(exc).__name__},
        )

    @app.on_event("startup")
    def startup() -> None:
        db.init_server_schema()

    _include_routes(
        app,
        auth_router,
        users_router,
        capabilities_router,
        model_create_router,
        data_router,
        sync_router,
        model_delete_router,
    )
    return app


app = create_app()
