from __future__ import annotations

import os


def test_database_schema_and_core_crud(isolated_data_dir):
    from backend.repositories import flights as flight_repository
    from backend.database import CORE_TABLES, CURRENT_SCHEMA_VERSION, DB_PATH, get_db, init_db

    assert os.path.commonpath([os.path.abspath(DB_PATH), str(isolated_data_dir.resolve())]) == str(
        isolated_data_dir.resolve()
    )
    init_db()

    conn = get_db()
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert CORE_TABLES <= tables
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION

        conn.execute("INSERT INTO aircraft_models (name) VALUES (?)", ("Smoke Model",))
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO aircraft (model_id, name) VALUES (?, ?)", (model_id, "Smoke Aircraft"))
        aircraft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        flight_id = flight_repository.insert_flight(
            conn,
            aircraft_id,
            "Smoke Flight",
            str(isolated_data_dir / "source"),
            "120000",
            "2026-07-15",
        )
        conn.commit()

        detail = flight_repository.get_flight_detail(conn, flight_id)
        assert detail["name"] == "Smoke Flight"
        assert detail["aircraft_name"] == "Smoke Aircraft"
        assert detail["model_name"] == "Smoke Model"

        flight_repository.delete_flight(conn, flight_id)
        conn.execute("DELETE FROM aircraft WHERE id=?", (aircraft_id,))
        conn.execute("DELETE FROM aircraft_models WHERE id=?", (model_id,))
        conn.commit()
        assert flight_repository.get_flight_detail(conn, flight_id) is None
    finally:
        conn.close()
