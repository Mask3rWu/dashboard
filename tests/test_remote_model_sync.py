from __future__ import annotations

import uuid

from backend.database import get_db, init_db
from backend.remote_model_sync import annotate_remote_models, sync_model_definition


def _definition(server_id: int, name: str) -> dict:
    return {
        "model": {
            "id": server_id,
            "name": name,
            "client_uid": f"server-model-{server_id}",
            "source_node_id": "server",
            "version": 3,
            "config": {
                "has_header": True,
                "has_uav_send_id": False,
                "extract_serial_from_path": True,
                "data_types": {
                    "telemetry": {
                        "display_label": "遥测",
                        "file_patterns": ["telemetry*.csv"],
                        "is_alert": False,
                        "columns": [
                            {
                                "name": "speed",
                                "label": "速度",
                                "unit": "m/s",
                                "type": "REAL",
                                "ordinal": 0,
                                "scale_factor": 0.1,
                            }
                        ],
                    }
                },
            },
        }
    }


def _remove_synced_model(conn, local_model_id: int) -> None:
    tables = conn.execute(
        "SELECT table_name FROM data_table_registry WHERE model_id=?",
        (local_model_id,),
    ).fetchall()
    for row in tables:
        table_name = str(row["table_name"])
        if table_name.replace("_", "").isalnum():
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute("DELETE FROM column_registry WHERE model_id=?", (local_model_id,))
    conn.execute("DELETE FROM data_table_registry WHERE model_id=?", (local_model_id,))
    conn.execute("DELETE FROM aircraft_models WHERE id=?", (local_model_id,))
    conn.commit()


def test_explicit_model_sync_creates_local_model_and_registry(isolated_data_dir):
    init_db()
    server_id = 800000 + uuid.uuid4().int % 100000
    name = f"Remote Model {uuid.uuid4().hex}"
    conn = get_db()
    local_model_id = None
    try:
        result = sync_model_definition(conn, _definition(server_id, name))
        local_model_id = result["local_model_id"]
        assert result["action"] == "created"
        row = conn.execute(
            """SELECT name, server_id, sync_origin, sync_state, server_version
               FROM aircraft_models WHERE id=?""",
            (result["local_model_id"],),
        ).fetchone()
        assert dict(row) == {
            "name": name,
            "server_id": server_id,
            "sync_origin": "server",
            "sync_state": "server_cache",
            "server_version": 3,
        }
        registry = conn.execute(
            """SELECT cr.column_name, cr.unit, cr.scale_factor
               FROM column_registry cr
               WHERE cr.model_id=? AND cr.data_type_key='telemetry'""",
            (result["local_model_id"],),
        ).fetchone()
        assert registry["column_name"] == "speed"
        assert registry["unit"] == "m/s"
        assert registry["scale_factor"] == 0.1
    finally:
        if local_model_id is not None:
            _remove_synced_model(conn, local_model_id)
        conn.close()


def test_remote_model_list_annotation_uses_explicit_server_link(isolated_data_dir):
    init_db()
    server_id = 900000 + uuid.uuid4().int % 100000
    name = f"Remote Model {uuid.uuid4().hex}"
    conn = get_db()
    local_model_id = None
    try:
        result = sync_model_definition(conn, _definition(server_id, name))
        local_model_id = result["local_model_id"]
        payload = annotate_remote_models(
            conn, {"models": [{"id": server_id}, {"id": server_id + 1}]}
        )
        assert payload["models"][0]["model_synced"] is True
        assert payload["models"][0]["local_model_id"] == result["local_model_id"]
        assert payload["models"][1]["model_synced"] is False
        assert payload["models"][1]["local_model_id"] is None
    finally:
        if local_model_id is not None:
            _remove_synced_model(conn, local_model_id)
        conn.close()
