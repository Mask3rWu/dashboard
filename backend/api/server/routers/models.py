"""Server model creation and synchronized entity deletion routes."""

from fastapi import APIRouter, Depends, HTTPException

from backend import server_database as db
from backend.api.server.dependencies import connection, require_user
from backend.api.server.schemas import CreateModelRequest, DeleteRequest
from backend.sync import server as server_sync


create_router = APIRouter()
delete_router = APIRouter()


@create_router.post("/api/models")
def create_model(req: CreateModelRequest, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_push")
    try:
        return db.create_model(conn, req.model_dump())
    except Exception as exc:
        if db.is_integrity_error(exc):
            raise HTTPException(400, f"机型已存在或配置重复: {db.integrity_error_detail(exc)}") from exc
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _delete(conn, user, entity_type: str, entity_id: int, req: DeleteRequest | None, capability: str, not_found: str):
    db.require_capability(user, capability)
    try:
        return server_sync.soft_delete_entity(
            conn, entity_type, entity_id, deleted_by=int(user["id"]),
            reason=req.reason if req else None,
        )
    except KeyError as exc:
        raise HTTPException(404, not_found) from exc


@delete_router.delete("/api/models/{model_id}")
def delete_model(model_id: int, req: DeleteRequest | None = None, user=Depends(require_user), conn=Depends(connection)):
    return _delete(conn, user, "model", model_id, req, "delete_models", "Model not found")


@delete_router.delete("/api/aircraft/{aircraft_id}")
def delete_aircraft(aircraft_id: int, req: DeleteRequest | None = None, user=Depends(require_user), conn=Depends(connection)):
    return _delete(conn, user, "aircraft", aircraft_id, req, "delete_aircraft", "Aircraft not found")


@delete_router.delete("/api/flights/{flight_id}")
def delete_flight(flight_id: int, req: DeleteRequest | None = None, user=Depends(require_user), conn=Depends(connection)):
    return _delete(conn, user, "flight", flight_id, req, "delete_flights", "Flight not found")
