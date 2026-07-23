from __future__ import annotations

from fastapi.testclient import TestClient


def test_data_filter_matches_whole_flights_at_the_same_aligned_time():
    import main
    from backend import analysis
    from backend.database import get_db, init_db
    from backend.repositories import flights as flight_repository

    init_db()
    conn = get_db()
    engine_table = "test_filter_engine_data"
    nav_table = "test_filter_nav_data"
    flight_ids: list[int] = []
    try:
        conn.execute(f"DROP TABLE IF EXISTS {engine_table}")
        conn.execute(f"DROP TABLE IF EXISTS {nav_table}")
        conn.execute("INSERT INTO aircraft_models (name) VALUES (?)", ("Data Filter Model",))
        model_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO aircraft (model_id, name) VALUES (?, ?)",
            (model_id, "Filter Aircraft"),
        )
        aircraft_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        for index in range(3):
            flight_ids.append(
                flight_repository.insert_flight(
                    conn,
                    aircraft_id,
                    f"Filter Flight {index + 1}",
                    "test-source",
                    f"filter-{index + 1}",
                    "2026-07-23",
                )
            )

        conn.execute(
            f"""CREATE TABLE {engine_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
                    time_str TEXT NOT NULL,
                    time_sec REAL NOT NULL,
                    rpm REAL
                )"""
        )
        conn.execute(
            f"""CREATE TABLE {nav_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
                    time_str TEXT NOT NULL,
                    time_sec REAL NOT NULL,
                    altitude REAL
                )"""
        )
        conn.executemany(
            """INSERT INTO data_table_registry
                   (model_id, data_type_key, table_name, display_label, is_alert)
               VALUES (?, ?, ?, ?, 0)""",
            [
                (model_id, "engine", engine_table, "Engine"),
                (model_id, "nav", nav_table, "Navigation"),
            ],
        )
        conn.executemany(
            """INSERT INTO column_registry
                   (model_id, data_type_key, table_name, column_name, display_label,
                    unit, data_type, ordinal, is_numeric, scale_factor)
               VALUES (?, ?, ?, ?, ?, ?, 'REAL', 0, 1, 1.0)""",
            [
                (model_id, "engine", engine_table, "rpm", "RPM", "rpm"),
                (model_id, "nav", nav_table, "altitude", "Altitude", "m"),
            ],
        )

        # Flight 1 satisfies both conditions at t=0. Flight 2 satisfies them
        # separately, which must not count as an AND match. Flight 3 has no nav data.
        conn.executemany(
            f"INSERT INTO {engine_table} (flight_id, time_str, time_sec, rpm) VALUES (?, ?, ?, ?)",
            [
                (flight_ids[0], "12:00:00", 43200, 3500),
                (flight_ids[0], "12:00:01", 43201, 1000),
                (flight_ids[1], "12:00:00", 43200, 3500),
                (flight_ids[1], "12:00:01", 43201, 1000),
                (flight_ids[2], "12:00:00", 43200, 3500),
            ],
        )
        conn.executemany(
            f"INSERT INTO {nav_table} (flight_id, time_str, time_sec, altitude) VALUES (?, ?, ?, ?)",
            [
                (flight_ids[0], "12:00:00", 43200, 200),
                (flight_ids[0], "12:00:01", 43201, 50),
                (flight_ids[1], "12:00:00", 43200, 50),
                (flight_ids[1], "12:00:01", 43201, 200),
            ],
        )
        conn.commit()

        filter_spec = {
            "logic": "and",
            "conditions": [
                {"column": "engine.rpm", "op": "gte", "value": 3000, "min_val": None, "max_val": None},
                {"column": "nav.altitude", "op": "gte", "value": 100, "min_val": None, "max_val": None},
            ],
        }
        assert analysis.match_flights_by_data(model_id, flight_ids, filter_spec) == [flight_ids[0]]

        filter_spec["logic"] = "or"
        assert analysis.match_flights_by_data(model_id, flight_ids, filter_spec) == flight_ids

        response = TestClient(main.app).post(
            "/api/flights/data-matches",
            json={"model_id": model_id, "flight_ids": flight_ids, "filter": filter_spec},
        )
        assert response.status_code == 200
        assert response.json() == {
            "flight_ids": flight_ids,
            "evaluated_count": 3,
            "matched_count": 3,
        }

        invalid_response = TestClient(main.app).post(
            "/api/flights/data-matches",
            json={
                "model_id": model_id,
                "flight_ids": flight_ids,
                "filter": {
                    "logic": "and",
                    "conditions": [{"column": "nav.unknown", "op": "gt", "value": 1}],
                },
            },
        )
        assert invalid_response.status_code == 400
    finally:
        if flight_ids:
            conn.execute("DELETE FROM aircraft_models WHERE name=?", ("Data Filter Model",))
        conn.execute(f"DROP TABLE IF EXISTS {engine_table}")
        conn.execute(f"DROP TABLE IF EXISTS {nav_table}")
        conn.commit()
        conn.close()
