"""Repository helpers for raw file object metadata."""

from __future__ import annotations


def get_file_object_by_sha(conn, sha256: str):
    return conn.execute(
        "SELECT id FROM file_objects WHERE sha256=?",
        (sha256,),
    ).fetchone()


def insert_file_object(conn, sha256: str, size_bytes: int, storage_rel_path: str) -> int:
    conn.execute(
        """INSERT INTO file_objects (sha256, size_bytes, storage_rel_path)
           VALUES (?, ?, ?)""",
        (sha256, size_bytes, storage_rel_path),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def attach_raw_file(
    conn,
    flight_id: int,
    file_object_id: int,
    original_name: str,
    original_rel_path: str,
    data_type_key: str | None,
    source_mtime: float | None,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO flight_raw_files
           (flight_id, file_object_id, original_name, original_rel_path,
            data_type_key, source_mtime)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            flight_id,
            file_object_id,
            original_name,
            original_rel_path,
            data_type_key,
            source_mtime,
        ),
    )


def get_raw_files_for_flight(conn, flight_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT frf.id, frf.flight_id, frf.original_name, frf.original_rel_path,
                  frf.data_type_key, frf.source_mtime, frf.created_at,
                  fo.id as file_object_id, fo.sha256, fo.size_bytes,
                  fo.storage_rel_path
           FROM flight_raw_files frf
           JOIN file_objects fo ON fo.id = frf.file_object_id
           WHERE frf.flight_id=?
           ORDER BY frf.original_rel_path, frf.id""",
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
