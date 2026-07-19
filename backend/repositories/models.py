"""Repository helpers for aircraft models, columns, and aircraft."""

from collections import OrderedDict


def list_models(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT am.*,
           (SELECT COUNT(*) FROM aircraft a
            WHERE a.model_id = am.id AND a.deleted_at IS NULL AND a.server_deleted_at IS NULL) as aircraft_count,
           COALESCE((SELECT COUNT(*) FROM flights f JOIN aircraft a2 ON a2.id = f.aircraft_id
                     WHERE a2.model_id = am.id AND f.deleted_at IS NULL AND f.server_deleted_at IS NULL
                       AND a2.deleted_at IS NULL AND a2.server_deleted_at IS NULL), 0) as total_flights,
           COALESCE((SELECT SUM(f2.duration_sec) FROM flights f2 JOIN aircraft a3 ON a3.id = f2.aircraft_id
                     WHERE a3.model_id = am.id AND f2.deleted_at IS NULL AND f2.server_deleted_at IS NULL
                       AND a3.deleted_at IS NULL AND a3.server_deleted_at IS NULL), 0) as total_flight_hours
           FROM aircraft_models am
           WHERE am.deleted_at IS NULL AND am.server_deleted_at IS NULL
           ORDER BY am.created_at"""
    ).fetchall()
    return [dict(row) for row in rows]


def model_exists(conn, model_id: int) -> bool:
    return conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone() is not None


def get_model(conn, model_id: int):
    return conn.execute(
        "SELECT name, has_header, has_uav_send_id, extract_serial_from_path FROM aircraft_models WHERE id=?",
        (model_id,),
    ).fetchone()


def unique_model_name(conn, requested_name: str) -> str:
    name = requested_name
    base_name = name
    suffix = 1
    while conn.execute("SELECT id FROM aircraft_models WHERE name=?", (name,)).fetchone():
        name = f"{base_name} ({suffix})"
        suffix += 1
    return name


def insert_model(conn, name: str, *, has_header: bool = True, has_uav_send_id: bool = False, extract_serial_from_path: bool = False) -> int:
    conn.execute(
        """INSERT INTO aircraft_models (name, has_header, has_uav_send_id, extract_serial_from_path)
           VALUES (?, ?, ?, ?)""",
        (name, int(has_header), int(has_uav_send_id), int(extract_serial_from_path)),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def rename_model(conn, model_id: int, name: str) -> None:
    conn.execute(
        """UPDATE aircraft_models SET name=?,
           sync_state=CASE WHEN sync_state IN ('synced', 'server_cache') THEN 'dirty' ELSE sync_state END
           WHERE id=?""",
        (name, model_id),
    )


def get_model_columns(conn, model_id: int) -> list[dict] | None:
    if not model_exists(conn, model_id):
        return None
    rows = conn.execute(
        """SELECT dtr.data_type_key, dtr.display_label, dtr.table_name,
                  cr.column_name, cr.display_label as col_label, cr.unit,
                  cr.data_type, cr.ordinal, cr.scale_factor
           FROM data_table_registry dtr
           JOIN column_registry cr ON cr.model_id=dtr.model_id AND cr.data_type_key=dtr.data_type_key
           WHERE dtr.model_id=? ORDER BY dtr.data_type_key, cr.ordinal""",
        (model_id,),
    ).fetchall()
    groups = OrderedDict()
    for row in rows:
        key = row["data_type_key"]
        groups.setdefault(key, {"data_type_key": key, "table": row["table_name"], "label": row["display_label"], "columns": []})
        groups[key]["columns"].append({
            "column_name": row["column_name"], "display_label": row["col_label"],
            "unit": row["unit"] or "", "data_type": row["data_type"], "ordinal": row["ordinal"],
            "scale_factor": row["scale_factor"] if row["scale_factor"] is not None else 1.0,
        })
    return list(groups.values())


def update_data_type_label(conn, model_id: int, data_type_key: str, display_label: str) -> bool:
    row = conn.execute("SELECT id FROM data_table_registry WHERE model_id=? AND data_type_key=?", (model_id, data_type_key)).fetchone()
    if not row:
        return False
    conn.execute("UPDATE data_table_registry SET display_label=? WHERE model_id=? AND data_type_key=?", (display_label, model_id, data_type_key))
    conn.execute(
        """UPDATE aircraft_models
           SET sync_state=CASE WHEN sync_state IN ('synced', 'server_cache') THEN 'dirty' ELSE sync_state END
           WHERE id=?""",
        (model_id,),
    )
    return True


def list_aircraft(conn, model_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*,
          (SELECT COUNT(*) FROM flights f WHERE f.aircraft_id=a.id
           AND f.deleted_at IS NULL AND f.server_deleted_at IS NULL) as flight_count
          FROM aircraft a WHERE a.model_id=? AND a.deleted_at IS NULL
          AND a.server_deleted_at IS NULL ORDER BY a.name""",
        (model_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def aircraft_exists(conn, aircraft_id: int) -> bool:
    return conn.execute("SELECT id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone() is not None


def insert_aircraft(conn, model_id: int, name: str) -> int:
    conn.execute("INSERT INTO aircraft (model_id, name) VALUES (?, ?)", (model_id, name))
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def rename_aircraft(conn, aircraft_id: int, name: str) -> None:
    conn.execute(
        """UPDATE aircraft SET name=?,
           sync_state=CASE WHEN sync_state IN ('synced', 'server_cache') THEN 'dirty' ELSE sync_state END
           WHERE id=?""",
        (name, aircraft_id),
    )
