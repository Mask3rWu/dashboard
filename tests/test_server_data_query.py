from __future__ import annotations

from sqlalchemy import create_engine, text

from backend import server_data_query


def _connection():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    conn = engine.connect()
    for ddl in (
        """CREATE TABLE aircraft_models (
               id INTEGER PRIMARY KEY, name TEXT, client_uid TEXT, source_node_id TEXT,
               version INTEGER, has_header INTEGER, has_uav_send_id INTEGER,
               extract_serial_from_path INTEGER, created_at TEXT, updated_at TEXT,
               deleted_at TEXT
           )""",
        "CREATE TABLE aircraft (id INTEGER PRIMARY KEY, model_id INTEGER, name TEXT, client_uid TEXT, version INTEGER, created_at TEXT, deleted_at TEXT)",
        """CREATE TABLE flights (
               id INTEGER PRIMARY KEY, aircraft_id INTEGER, name TEXT, source_path TEXT,
               session_key TEXT, flight_date TEXT, start_time TEXT, end_time TEXT,
               duration_sec REAL, total_rows INTEGER, record_total_duration_min REAL,
               record_location TEXT, record_payload TEXT, record_weather TEXT,
               record_fuel_amount REAL, record_takeoff_weight REAL, record_altitude REAL,
               record_wind_speed REAL, record_wind_direction TEXT, record_temperature REAL,
               record_note TEXT, client_uid TEXT, version INTEGER, created_at TEXT,
               updated_at TEXT, deleted_at TEXT
           )""",
        "CREATE TABLE flight_raw_files (id INTEGER PRIMARY KEY, flight_id INTEGER)",
        "CREATE TABLE data_table_registry (id INTEGER PRIMARY KEY, model_id INTEGER, data_type_key TEXT, table_name TEXT, display_label TEXT, file_patterns TEXT, is_alert INTEGER)",
        "CREATE TABLE column_registry (id INTEGER PRIMARY KEY, model_id INTEGER, data_type_key TEXT, table_name TEXT, column_name TEXT, display_label TEXT, unit TEXT, scale_factor REAL, data_type TEXT, ordinal INTEGER, is_numeric INTEGER)",
        "CREATE TABLE server_data_m1_engine (id INTEGER PRIMARY KEY, flight_id INTEGER, time_str TEXT, time_sec REAL, rpm REAL)",
        "CREATE TABLE server_data_m1_nav (id INTEGER PRIMARY KEY, flight_id INTEGER, time_str TEXT, time_sec REAL, altitude REAL)",
    ):
        conn.execute(text(ddl))
    conn.execute(text("INSERT INTO aircraft_models VALUES (1, 'Model', 'm1', 'node-1', 1, 1, 0, 0, '2026-07-23 00:00:00', '2026-07-23 00:00:00', NULL)"))
    conn.execute(text("INSERT INTO aircraft VALUES (1, 1, 'AC-01', 'a1', 1, '2026-07-23 00:00:00', NULL)"))
    flight_sql = text(
        """INSERT INTO flights
           (id, aircraft_id, name, source_path, session_key, flight_date,
            start_time, end_time, duration_sec, total_rows,
            record_total_duration_min, record_location, record_payload,
            record_weather, record_fuel_amount, record_takeoff_weight,
            record_altitude, record_wind_speed, record_wind_direction,
            record_temperature, record_note, client_uid, version, created_at,
            updated_at, deleted_at)
           VALUES
           (:id, 1, :name, '', :session, '2026-07-23',
            '2026-07-23 12:00:00', '2026-07-23 12:00:01', 1, 2,
            1, :location, '', 'clear', NULL, NULL, NULL, NULL, '', NULL, '',
            :uid, 1, :created, :created, NULL)"""
    )
    conn.execute(flight_sql, {"id": 1, "name": "Flight 1", "session": "120000", "location": "Site A", "uid": "f1", "created": "2026-07-23 12:00:00"})
    conn.execute(flight_sql, {"id": 2, "name": "Flight 2", "session": "120100", "location": "Site B", "uid": "f2", "created": "2026-07-23 12:01:00"})
    conn.execute(text("INSERT INTO data_table_registry VALUES (1, 1, 'engine', 'server_data_m1_engine', 'Engine', '[\"engine*.csv\"]', 0), (2, 1, 'nav', 'server_data_m1_nav', 'Nav', '[\"nav*.csv\"]', 0)"))
    conn.execute(text("INSERT INTO column_registry VALUES (1, 1, 'engine', 'server_data_m1_engine', 'rpm', 'RPM', 'rpm', 1, 'REAL', 1, 1), (2, 1, 'nav', 'server_data_m1_nav', 'altitude', 'Altitude', 'm', 1, 'REAL', 1, 1)"))
    conn.execute(text("INSERT INTO server_data_m1_engine VALUES (1, 1, '12:00:00', 43200, 3500), (2, 2, '12:00:00', 43200, 3500)"))
    conn.execute(text("INSERT INTO server_data_m1_nav VALUES (1, 1, '12:00:00', 43200, 200), (2, 2, '12:00:00', 43200, 50)"))
    conn.commit()
    return conn


def test_server_search_applies_record_filters_and_paginates():
    conn = _connection()
    try:
        result = server_data_query.search_flights(conn, {
            "model_id": 1,
            "record_filter": {
                "logic": "and",
                "conditions": [{"field": "record_location", "op": "contains", "value": "site"}],
            },
            "page": 1,
            "page_size": 1,
        })
        assert result["total"] == 2
        assert len(result["flights"]) == 1
        assert result["summary"]["flight_count"] == 2
    finally:
        conn.close()


def test_server_search_reuses_same_aligned_data_filter_semantics():
    conn = _connection()
    try:
        result = server_data_query.search_flights(conn, {
            "model_id": 1,
            "data_filter": {
                "logic": "and",
                "conditions": [
                    {"column": "engine.rpm", "op": "gte", "value": 3000},
                    {"column": "nav.altitude", "op": "gte", "value": 100},
                ],
            },
            "page": 1,
            "page_size": 50,
        })
        assert [flight["id"] for flight in result["flights"]] == [1]
    finally:
        conn.close()


def test_model_definition_contains_full_import_configuration():
    conn = _connection()
    try:
        result = server_data_query.get_model_definition(conn, 1)
        model = result["model"]
        assert model["id"] == 1
        assert model["source_node_id"] == "node-1"
        assert model["config"]["has_header"] is True
        engine = model["config"]["data_types"]["engine"]
        assert engine["file_patterns"] == ["engine*.csv"]
        assert engine["columns"][0]["name"] == "rpm"
        assert engine["columns"][0]["unit"] == "rpm"
    finally:
        conn.close()
