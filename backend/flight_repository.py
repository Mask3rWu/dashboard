"""Repository helpers for flight metadata and management queries."""

from __future__ import annotations


RECORD_COLUMNS = (
    "record_daily_duration_min",
    "record_batch_name",
    "record_location",
    "record_payload",
    "record_weather",
    "record_fuel_amount",
    "record_takeoff_weight",
    "record_altitude",
    "record_wind_speed",
    "record_note",
)


def get_aircraft_with_model(conn, aircraft_id: int):
    return conn.execute(
        """SELECT a.id, a.name, am.id as model_id, am.name as model_name
           FROM aircraft a JOIN aircraft_models am ON am.id = a.model_id
           WHERE a.id=?""",
        (aircraft_id,),
    ).fetchone()


def find_duplicate_flight(conn, aircraft_id: int, flight_date: str | None, session_key: str):
    if flight_date is None:
        return conn.execute(
            "SELECT id FROM flights WHERE aircraft_id=? AND flight_date IS NULL AND session_key=?",
            (aircraft_id, session_key),
        ).fetchone()
    return conn.execute(
        "SELECT id FROM flights WHERE aircraft_id=? AND flight_date=? AND session_key=?",
        (aircraft_id, flight_date, session_key),
    ).fetchone()


def insert_flight(
    conn,
    aircraft_id: int,
    name: str,
    source_path: str,
    session_key: str,
    flight_date: str | None,
    record_fields: dict | None = None,
) -> int:
    record_fields = record_fields or {}
    columns = ["aircraft_id", "name", "source_path", "session_key", "flight_date"]
    values = [aircraft_id, name, source_path, session_key, flight_date]
    for column in RECORD_COLUMNS:
        if column in record_fields:
            columns.append(column)
            values.append(record_fields[column])
    conn.execute(
        f"INSERT INTO flights ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        values,
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def set_raw_import_warnings(conn, flight_id: int, warnings_json: str) -> None:
    conn.execute(
        "UPDATE flights SET raw_import_warnings=? WHERE id=?",
        (warnings_json, flight_id),
    )


def list_flights(conn, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    where = []
    params = []
    if filters.get("model_id") is not None:
        where.append("am.id = ?")
        params.append(filters["model_id"])
    if filters.get("aircraft_id") is not None:
        where.append("a.id = ?")
        params.append(filters["aircraft_id"])
    if filters.get("date_from"):
        where.append("f.flight_date >= ?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        where.append("f.flight_date <= ?")
        params.append(filters["date_to"])
    for column, key in (
        ("f.record_batch_name", "batch_name"),
        ("f.record_location", "location"),
        ("f.record_weather", "weather"),
        ("f.record_payload", "payload"),
    ):
        value = filters.get(key)
        if value and str(value).strip():
            where.append(f"{column} LIKE ?")
            params.append(f"%{str(value).strip()}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"""SELECT f.*, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name,
                  COALESCE(rf.raw_file_count, 0) as raw_file_count
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           LEFT JOIN (
               SELECT flight_id, COUNT(*) as raw_file_count
               FROM flight_raw_files
               GROUP BY flight_id
           ) rf ON rf.flight_id = f.id
           {where_sql}
           ORDER BY f.import_time DESC""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_flight_detail(conn, flight_id: int):
    return conn.execute(
        """SELECT f.*, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name,
                  COALESCE(rf.raw_file_count, 0) as raw_file_count
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           LEFT JOIN (
               SELECT flight_id, COUNT(*) as raw_file_count
               FROM flight_raw_files
               GROUP BY flight_id
           ) rf ON rf.flight_id = f.id
           WHERE f.id=?""",
        (flight_id,),
    ).fetchone()


def get_flight_raw_warning_row(conn, flight_id: int):
    return conn.execute(
        "SELECT id, raw_import_warnings FROM flights WHERE id=?",
        (flight_id,),
    ).fetchone()


def delete_flight(conn, flight_id: int) -> None:
    conn.execute("DELETE FROM flights WHERE id=?", (flight_id,))


def flight_exists(conn, flight_id: int) -> bool:
    return conn.execute("SELECT id FROM flights WHERE id=?", (flight_id,)).fetchone() is not None


def update_flight_name(conn, flight_id: int, name: str) -> None:
    conn.execute("UPDATE flights SET name=? WHERE id=?", (name, flight_id))


def update_flight_record(conn, flight_id: int, data: dict) -> None:
    invalid = [key for key in data if key not in RECORD_COLUMNS]
    if invalid:
        raise ValueError(f"Unsupported flight record columns: {invalid}")
    assignments = ", ".join([f"{key}=?" for key in data])
    conn.execute(
        f"UPDATE flights SET {assignments} WHERE id=?",
        [*data.values(), flight_id],
    )


def export_tree_rows(conn):
    return conn.execute(
        """SELECT f.id as flight_id, f.name as flight_name, f.session_key,
                  f.flight_date, f.start_time, f.duration_sec,
                  f.record_batch_name, f.record_location, f.record_weather,
                  a.id as aircraft_id, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           ORDER BY am.name, a.name, COALESCE(f.record_batch_name, ''), f.flight_date, f.session_key, f.id"""
    ).fetchall()
