"""Cross-table aircraft model configuration import and export workflows."""

from __future__ import annotations

import json
import os
from datetime import datetime

from backend.import_pipeline.format_configs import (
    build_model_config_from_db,
    generate_config_from_scan,
    register_model_tables,
    save_model_config_to_db,
)
from backend.repositories import models as model_repository
from backend.repositories import presets as preset_repository


def create_model(conn, name: str) -> int:
    model_id = model_repository.insert_model(conn, name)
    register_model_tables(conn, model_id)
    return model_id


def create_model_from_scan(conn, name: str, source_path: str, selected_data_types: list[str] | None) -> int:
    config = generate_config_from_scan(source_path)
    if selected_data_types is not None:
        keep = set(selected_data_types)
        config["data_types"] = {key: value for key, value in config["data_types"].items() if key in keep}
    if not config.get("data_types"):
        raise ValueError("No data types selected; cannot create an empty model")
    model_id = model_repository.insert_model(conn, name)
    save_model_config_to_db(conn, model_id, config)
    register_model_tables(conn, model_id, config=config)
    conn.commit()
    return model_id


def export_model(conn, model_id: int, out_dir: str) -> dict | None:
    model = model_repository.get_model(conn, model_id)
    if not model:
        return None
    config = build_model_config_from_db(conn, model_id) or {"data_types": {}}
    export = {
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "model": {
            "name": model["name"],
            "has_header": bool(model["has_header"]),
            "has_uav_send_id": bool(model["has_uav_send_id"]),
            "extract_serial_from_path": bool(model["extract_serial_from_path"]),
        },
        "data_types": config.get("data_types", {}),
        "presets": [
            {"name": item["name"], "columns": item["columns"]}
            for item in preset_repository.list_column_presets(conn, model_id)
        ],
        "filter_presets": [
            {"name": item["name"], "config": item["config"]}
            for item in preset_repository.list_filter_presets(conn, model_id)
        ],
    }
    filename = f"{model['name']}_export.json"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(export, file, ensure_ascii=False, indent=2)
    return {"ok": True, "path": path, "filename": filename}


def import_model(conn, requested_name: str, data: dict) -> dict:
    if data.get("version") != 1:
        raise ValueError(f"Unsupported export version: {data.get('version')}")
    model_data = data.get("model", {})
    data_types = data.get("data_types", {})
    if not model_data or not data_types:
        raise ValueError("Invalid export data: missing model or data_types")
    name = model_repository.unique_model_name(conn, requested_name)
    model_id = model_repository.insert_model(
        conn,
        name,
        has_header=bool(model_data.get("has_header")),
        has_uav_send_id=bool(model_data.get("has_uav_send_id")),
        extract_serial_from_path=bool(model_data.get("extract_serial_from_path")),
    )
    config = {
        "has_header": bool(model_data.get("has_header")),
        "has_uav_send_id": bool(model_data.get("has_uav_send_id")),
        "extract_serial_from_path": bool(model_data.get("extract_serial_from_path")),
        "data_types": data_types,
    }
    register_model_tables(conn, model_id, config=config, commit=False)
    for item in data.get("presets", []):
        try:
            preset_repository.save_column_preset(conn, model_id, item["name"], item.get("columns", []))
        except Exception:
            pass
    for item in data.get("filter_presets", []):
        try:
            preset_repository.save_filter_preset(conn, model_id, item["name"], item.get("config", {}))
        except Exception:
            pass
    conn.commit()
    return {"id": model_id, "name": name}
