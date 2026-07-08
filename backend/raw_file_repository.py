"""Repository helpers for stored raw flight files."""

from __future__ import annotations


def attach_raw_file(
    conn,
    flight_id: int,
    original_name: str,
    original_rel_path: str,
    storage_rel_path: str,
    sha256: str,
    size_bytes: int,
    data_type_key: str | None,
    source_mtime: float | None,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO flight_raw_files
           (flight_id, original_name, original_rel_path, storage_rel_path,
            sha256, size_bytes, data_type_key, source_mtime)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            flight_id,
            original_name,
            original_rel_path,
            storage_rel_path,
            sha256,
            size_bytes,
            data_type_key,
            source_mtime,
        ),
    )


def get_raw_files_for_flight(conn, flight_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, flight_id, original_name, original_rel_path,
                  data_type_key, source_mtime, created_at,
                  sha256, size_bytes, storage_rel_path
           FROM flight_raw_files
           WHERE flight_id=?
           ORDER BY storage_rel_path, id""",
        (flight_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_flight_manifest_row(conn, flight_id: int):
    return conn.execute(
        """SELECT f.id, f.name, f.session_key, f.flight_date, f.raw_import_warnings,
                  a.id as aircraft_id, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           WHERE f.id=?""",
        (flight_id,),
    ).fetchone()


def get_raw_file_context_rows(conn, where_sql: str = "", params: tuple = ()) -> list[dict]:
    rows = conn.execute(
        f"""SELECT frf.*, f.name AS flight_name, f.flight_date,
                  a.id AS aircraft_id, a.name AS aircraft_name,
                  am.id AS model_id, am.name AS model_name
           FROM flight_raw_files frf
           JOIN flights f ON f.id = frf.flight_id
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           {where_sql}
           ORDER BY frf.flight_id, frf.storage_rel_path, frf.id""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]
