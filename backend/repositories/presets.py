"""Repository helpers for analysis and filter presets."""

import json


def list_column_presets(conn, model_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM presets WHERE model_id=? ORDER BY name", (model_id,)).fetchall()
    return [{"id": row["id"], "model_id": row["model_id"], "name": row["name"], "columns": json.loads(row["columns_json"])} for row in rows]


def save_column_preset(conn, model_id: int, name: str, columns: list[str]) -> dict:
    conn.execute("INSERT OR REPLACE INTO presets (model_id, name, columns_json) VALUES (?, ?, ?)", (model_id, name, json.dumps(columns)))
    preset_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": preset_id, "model_id": model_id, "name": name, "columns": columns}


def delete_column_preset(conn, preset_id: int) -> None:
    conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))


def list_filter_presets(conn, model_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM filter_presets WHERE model_id=? ORDER BY name", (model_id,)).fetchall()
    return [{"id": row["id"], "model_id": row["model_id"], "name": row["name"], "config": json.loads(row["config_json"])} for row in rows]


def save_filter_preset(conn, model_id: int, name: str, config: dict) -> dict:
    conn.execute("INSERT OR REPLACE INTO filter_presets (model_id, name, config_json) VALUES (?, ?, ?)", (model_id, name, json.dumps(config)))
    preset_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": preset_id, "model_id": model_id, "name": name, "config": config}


def delete_filter_preset(conn, preset_id: int) -> None:
    conn.execute("DELETE FROM filter_presets WHERE id=?", (preset_id,))
