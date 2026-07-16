"""Aircraft model, column configuration, and aircraft routes."""

import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from backend import model_catalog
from backend.api.desktop.dependencies import (
    require_capability_or_server,
    require_local_delete_capability,
    server_token,
)
from backend.api.desktop.schemas import (
    CreateAircraftRequest,
    CreateModelFromScanRequest,
    CreateModelRequest,
    DeleteEntityRequest,
    ImportModelRequest,
    UpdateAircraftRequest,
    UpdateColumnRequest,
    UpdateDataTypeLabelRequest,
    UpdateModelRequest,
)
from backend.database import get_db
from backend.import_pipeline.format_configs import update_column_metadata
from backend.raw_storage import refresh_raw_storage_paths
from backend.repositories import models as model_repository
from backend.sync import workflow


router = APIRouter()


@router.get("/api/models")
def list_models():
    conn = get_db()
    try:
        return {"models": model_repository.list_models(conn)}
    finally:
        conn.close()


@router.post("/api/models")
def create_model(req: CreateModelRequest):
    if not req.name.strip():
        raise HTTPException(400, "Model name must not be empty")
    conn = get_db()
    try:
        model_id = model_catalog.create_model(conn, req.name)
        return {"id": model_id, "name": req.name}
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(400, f"Model name '{req.name}' already exists") from exc
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/models/from-scan")
def create_model_from_scan(req: CreateModelFromScanRequest):
    conn = get_db()
    try:
        model_id = model_catalog.create_model_from_scan(
            conn, req.name, req.source_path, req.selected_data_types
        )
        return {"id": model_id, "name": req.name}
    except ValueError as exc:
        if str(exc) == "No data types selected; cannot create an empty model":
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(400, f"Model name '{req.name}' already exists") from exc
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@router.patch("/api/models/{model_id}")
def update_model(model_id: int, req: UpdateModelRequest):
    conn = get_db()
    try:
        if not model_repository.model_exists(conn, model_id):
            raise HTTPException(404, "Model not found")
        model_repository.rename_model(conn, model_id, req.name.strip())
        warnings = refresh_raw_storage_paths(conn, model_id=model_id)
        conn.commit()
        return {"ok": True, "raw_warnings": warnings}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.delete("/api/models/{model_id}")
def delete_model(model_id: int, request: Request, req: DeleteEntityRequest | None = None):
    conn = get_db()
    try:
        if not model_repository.model_exists(conn, model_id):
            raise HTTPException(404, "Model not found")
        require_local_delete_capability(conn, request, "model")
    finally:
        conn.close()
    try:
        return workflow.delete_entity(
            "model", model_id,
            requested_scope=req.scope if req else "auto",
            reason=req.reason if req else None,
            token=server_token(req, request),
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/api/models/{model_id}/export")
def export_model(model_id: int):
    out_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else str(Path(__file__).resolve().parents[4])
    conn = get_db()
    try:
        result = model_catalog.export_model(conn, model_id, out_dir)
        if not result:
            raise HTTPException(404, "Model not found")
        return result
    finally:
        conn.close()


@router.post("/api/models/import")
def import_model(req: ImportModelRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Model name must not be empty")
    conn = get_db()
    try:
        return model_catalog.import_model(conn, name, req.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@router.get("/api/models/{model_id}/columns")
def get_model_columns(model_id: int):
    conn = get_db()
    try:
        groups = model_repository.get_model_columns(conn, model_id)
        if groups is None:
            raise HTTPException(404, "Model not found")
        return {"data_types": groups}
    finally:
        conn.close()


@router.patch("/api/models/{model_id}/columns")
def update_model_column(model_id: int, request: Request, data_type_key: str = Query(...), column_name: str = Query(...), req: UpdateColumnRequest | None = None):
    if req is None or (req.display_label is None and req.unit is None and req.scale_factor is None):
        raise HTTPException(400, "At least one of display_label, unit, or scale_factor must be provided")
    conn = get_db()
    try:
        require_capability_or_server(conn, request, "update_columns")
        return update_column_metadata(conn, model_id, data_type_key, column_name, display_label=req.display_label, unit=req.unit, scale_factor=req.scale_factor)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@router.patch("/api/models/{model_id}/data-types/{data_type_key}")
def update_data_type_label(model_id: int, data_type_key: str, req: UpdateDataTypeLabelRequest, request: Request):
    label = req.display_label.strip()
    if not label:
        raise HTTPException(400, "display_label must not be empty")
    conn = get_db()
    try:
        require_capability_or_server(conn, request, "update_columns")
        if not model_repository.update_data_type_label(conn, model_id, data_type_key, label):
            raise HTTPException(404, f"Data type '{data_type_key}' not found for model {model_id}")
        conn.commit()
        return {"ok": True, "data_type_key": data_type_key, "display_label": label}
    finally:
        conn.close()


@router.get("/api/models/{model_id}/aircraft")
def list_aircraft(model_id: int):
    conn = get_db()
    try:
        return {"aircraft": model_repository.list_aircraft(conn, model_id)}
    finally:
        conn.close()


@router.post("/api/models/{model_id}/aircraft")
def create_aircraft(model_id: int, req: CreateAircraftRequest):
    conn = get_db()
    try:
        if not model_repository.model_exists(conn, model_id):
            raise HTTPException(404, "Model not found")
        aircraft_id = model_repository.insert_aircraft(conn, model_id, req.name)
        conn.commit()
        return {"id": aircraft_id, "model_id": model_id, "name": req.name}
    except HTTPException:
        raise
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(400, f"Aircraft '{req.name}' already exists in this model") from exc
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@router.patch("/api/aircraft/{aircraft_id}")
def update_aircraft(aircraft_id: int, req: UpdateAircraftRequest):
    conn = get_db()
    try:
        if not model_repository.aircraft_exists(conn, aircraft_id):
            raise HTTPException(404, "Aircraft not found")
        model_repository.rename_aircraft(conn, aircraft_id, req.name.strip())
        warnings = refresh_raw_storage_paths(conn, aircraft_id=aircraft_id)
        conn.commit()
        return {"ok": True, "raw_warnings": warnings}
    except HTTPException:
        raise
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(400, f"Aircraft name '{req.name}' already exists in this model") from exc
        raise HTTPException(500, str(exc)) from exc
    finally:
        conn.close()


@router.delete("/api/aircraft/{aircraft_id}")
def delete_aircraft(aircraft_id: int, request: Request, req: DeleteEntityRequest | None = None):
    conn = get_db()
    try:
        if not model_repository.aircraft_exists(conn, aircraft_id):
            raise HTTPException(404, "Aircraft not found")
        require_local_delete_capability(conn, request, "aircraft")
    finally:
        conn.close()
    try:
        return workflow.delete_entity(
            "aircraft", aircraft_id,
            requested_scope=req.scope if req else "auto",
            reason=req.reason if req else None,
            token=server_token(req, request),
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
