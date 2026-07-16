"""Directory browsing, scan preview, and session import routes."""

import os
import traceback

from fastapi import APIRouter, HTTPException

from backend.api.desktop.dependencies import (
    model_dump,
    normalize_flight_date,
    normalize_record_fields,
)
from backend.api.desktop.schemas import ImportRequest, ImportSessionRequest
from backend.database import get_db
from backend.import_pipeline.parser import import_session
from backend.import_pipeline.scanner import scan_folder_sessions


browse_router = APIRouter()
router = APIRouter()


@browse_router.get("/api/folders/browse")
def browse_folder():
    """Open the native folder picker."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected_path = filedialog.askdirectory(
            title="选择飞行数据文件夹", mustexist=True
        )
        root.destroy()
        if selected_path and os.path.isdir(selected_path):
            return {"path": selected_path}
        return {"path": "", "cancelled": True}
    except Exception as exc:
        raise HTTPException(500, f"Folder browser failed: {exc}") from exc


@browse_router.get("/api/folders/subdirs")
def list_subdirs(path: str):
    normalized = os.path.normpath(path)
    if not os.path.isdir(normalized):
        raise HTTPException(400, "Not a directory")
    try:
        entries = [
            name
            for name in sorted(os.listdir(normalized))
            if os.path.isdir(os.path.join(normalized, name))
        ]
        return {"path": normalized, "subdirs": entries}
    except PermissionError as exc:
        raise HTTPException(403, "Permission denied") from exc


@browse_router.get("/api/files/browse")
def browse_file(title: str = "选择文件", filetypes: str = ""):
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        parsed_types: list[tuple[str, str]] = []
        if filetypes:
            parts = [part for part in filetypes.split("|") if part]
            for index in range(0, len(parts) - 1, 2):
                parsed_types.append((parts[index], parts[index + 1]))
        if not parsed_types:
            parsed_types = [("All Files", "*.*")]
        selected_path = filedialog.askopenfilename(
            title=title, filetypes=parsed_types
        )
        root.destroy()
        if selected_path and os.path.isfile(selected_path):
            return {"path": selected_path}
        return {"path": "", "cancelled": True}
    except Exception as exc:
        raise HTTPException(500, f"File browser failed: {exc}") from exc


@router.post("/api/flights/scan")
def scan_folder_api(req: ImportRequest):
    conn = get_db()
    try:
        return scan_folder_sessions(req.source_path, conn=conn)
    finally:
        conn.close()


@router.post("/api/flights/import")
def import_flight_api(req: ImportSessionRequest):
    try:
        record_fields = normalize_record_fields(model_dump(req), include_unset=True)
        flight_date = normalize_flight_date(req.flight_date)
        if not flight_date:
            raise HTTPException(400, "飞行日期必填")
        if req.session_key:
            return import_session(
                os.path.normpath(req.source_path),
                req.aircraft_id,
                req.session_key,
                record_fields=record_fields,
                flight_date_override=flight_date,
            )
        conn = get_db()
        try:
            preview = scan_folder_sessions(req.source_path, conn=conn)
            imported = []
            for session in preview.get("sessions", []):
                result = import_session(
                    os.path.normpath(req.source_path),
                    req.aircraft_id,
                    session["session_key"],
                    record_fields=record_fields,
                    flight_date_override=flight_date,
                )
                if "error" not in result:
                    imported.append(result)
            if not imported:
                return {"error": "No sessions could be imported"}
            return {"imported": imported}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, str(exc)) from exc
