"""Read-only server data-management routes."""

from fastapi import APIRouter, Depends, HTTPException

from backend import server_database as db
from backend import server_data_query
from backend.api.server.dependencies import connection, require_user
from backend.api.server.schemas import ServerFlightSearchRequest


router = APIRouter()


def _require_read(user) -> None:
    db.require_capability(user, "sync_pull")


@router.get("/api/data/models")
def models(user=Depends(require_user), conn=Depends(connection)):
    _require_read(user)
    return server_data_query.list_models(conn)


@router.get("/api/data/models/{model_id}/aircraft")
def aircraft(model_id: int, user=Depends(require_user), conn=Depends(connection)):
    _require_read(user)
    return server_data_query.list_aircraft(conn, model_id)


@router.get("/api/data/models/{model_id}/columns")
def columns(model_id: int, user=Depends(require_user), conn=Depends(connection)):
    _require_read(user)
    return server_data_query.get_model_columns(conn, model_id)


@router.get("/api/data/models/{model_id}/definition")
def model_definition(model_id: int, user=Depends(require_user), conn=Depends(connection)):
    _require_read(user)
    try:
        return server_data_query.get_model_definition(conn, model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/data/flights/search")
def flights(
    req: ServerFlightSearchRequest,
    user=Depends(require_user),
    conn=Depends(connection),
):
    _require_read(user)
    try:
        return server_data_query.search_flights(conn, req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
