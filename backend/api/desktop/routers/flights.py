"""Flight metadata and raw-file routes."""

import json
import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request

from backend import analysis
from backend.api.desktop.dependencies import (
    model_dump,
    normalize_record_fields,
    require_local_delete_capability,
    server_token,
)
from backend.api.desktop.schemas import (
    DeleteEntityRequest,
    FlightRecordRequest,
    UpdateFlightRequest,
)
from backend.database import get_db
from backend.raw_storage import (
    build_flight_manifest,
    get_raw_directory_for_flight,
    get_raw_files_for_flight,
)
from backend.repositories import flights as flight_repository
from backend.sync import workflow


listing_router = APIRouter()
raw_router = APIRouter()
mutation_router = APIRouter()


def _parse_raw_warnings(value: str | None):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


@listing_router.get("/api/flights")
def list_flights(
    model_id: int | None = None,
    aircraft_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    location: str | None = None,
    weather: str | None = None,
    payload: str | None = None,
):
    conn = get_db()
    try:
        flights = []
        filters = {
            "model_id": model_id,
            "aircraft_id": aircraft_id,
            "date_from": date_from,
            "date_to": date_to,
            "location": location,
            "weather": weather,
            "payload": payload,
        }
        for item in flight_repository.list_flights(conn, filters):
            item["raw_warnings"] = _parse_raw_warnings(item.get("raw_import_warnings"))
            flights.append(item)
        return {"flights": flights}
    finally:
        conn.close()


@listing_router.get("/api/flights/{flight_id}")
def get_flight(flight_id: int):
    conn = get_db()
    try:
        flight = flight_repository.get_flight_detail(conn, flight_id)
        if not flight:
            raise HTTPException(404, "Flight not found")
        result = dict(flight)
        result["raw_warnings"] = _parse_raw_warnings(result.get("raw_import_warnings"))
        result["columns"] = analysis.get_columns_for_flight_api(flight_id)
        return result
    finally:
        conn.close()


@raw_router.get("/api/flights/{flight_id}/raw-files")
def get_flight_raw_files(flight_id: int):
    conn = get_db()
    try:
        flight = flight_repository.get_flight_raw_warning_row(conn, flight_id)
        if not flight:
            raise HTTPException(404, "Flight not found")
        return {
            "flight_id": flight_id,
            "files": get_raw_files_for_flight(conn, flight_id),
            "warnings": _parse_raw_warnings(flight["raw_import_warnings"]),
        }
    finally:
        conn.close()


@raw_router.get("/api/flights/{flight_id}/raw-manifest")
def get_flight_raw_manifest(flight_id: int):
    conn = get_db()
    try:
        manifest = build_flight_manifest(conn, flight_id)
        if not manifest:
            raise HTTPException(404, "Flight not found")
        return manifest
    finally:
        conn.close()


def _open_directory(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


@raw_router.post("/api/flights/{flight_id}/raw-folder/open")
def open_flight_raw_folder(flight_id: int):
    conn = get_db()
    try:
        folder = get_raw_directory_for_flight(conn, flight_id)
        if not folder:
            raise HTTPException(404, "Flight not found")
        if not folder["path"] or not folder.get("file_count"):
            raise HTTPException(404, "No stored raw files for this flight")
        os.makedirs(folder["path"], exist_ok=True)
        _open_directory(folder["path"])
        conn.commit()
        return folder
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Cannot open readable raw file directory: {exc}") from exc
    finally:
        conn.close()


@mutation_router.delete("/api/flights/{flight_id}")
def delete_flight(
    flight_id: int, request: Request, req: DeleteEntityRequest | None = None
):
    conn = get_db()
    try:
        if not flight_repository.flight_exists(conn, flight_id):
            raise HTTPException(404, "Flight not found")
        require_local_delete_capability(conn, request, "flight")
    finally:
        conn.close()
    try:
        return workflow.delete_entity(
            "flight",
            flight_id,
            requested_scope=req.scope if req else "auto",
            reason=req.reason if req else None,
            token=server_token(req, request),
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@mutation_router.patch("/api/flights/{flight_id}")
def update_flight(flight_id: int, req: UpdateFlightRequest):
    if not req.name.strip():
        raise HTTPException(400, "Name cannot be empty")
    conn = get_db()
    try:
        if not flight_repository.flight_exists(conn, flight_id):
            raise HTTPException(404, "Flight not found")
        flight_repository.update_flight_name(conn, flight_id, req.name.strip())
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@mutation_router.patch("/api/flights/{flight_id}/record")
def update_flight_record(flight_id: int, req: FlightRecordRequest):
    data = normalize_record_fields(model_dump(req, exclude_unset=True), include_unset=False)
    if not data:
        return {"ok": True}
    conn = get_db()
    try:
        if not flight_repository.flight_exists(conn, flight_id):
            raise HTTPException(404, "Flight not found")
        flight_repository.update_flight_record(conn, flight_id, data)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
