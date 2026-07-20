from __future__ import annotations

import sqlite3
from pathlib import Path


def _storage_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE aircraft_models (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE aircraft (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE flights (
            id INTEGER PRIMARY KEY,
            aircraft_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            session_key TEXT,
            flight_date TEXT,
            raw_import_warnings TEXT
        );
        CREATE TABLE flight_raw_files (
            id INTEGER PRIMARY KEY,
            flight_id INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            original_rel_path TEXT NOT NULL,
            storage_rel_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            data_type_key TEXT,
            source_mtime REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(flight_id, storage_rel_path)
        );
        INSERT INTO aircraft_models (id, name) VALUES (7, 'Test Model');
        INSERT INTO aircraft (id, model_id, name) VALUES (11, 7, 'Tail 01');
        INSERT INTO flights (id, aircraft_id, name, flight_date)
        VALUES (13, 11, 'Test Flight', '2026-07-15');
        """
    )
    return conn


def test_store_raw_file_uses_model_and_aircraft_hierarchy(tmp_path, monkeypatch):
    from backend import raw_storage

    raw_root = tmp_path / "raw_files"
    monkeypatch.setattr(raw_storage, "RAW_ROOT", str(raw_root))
    source = tmp_path / "source" / "telemetry.txt"
    source.parent.mkdir()
    source.write_bytes(b"flight data")

    conn = _storage_connection()
    try:
        raw_storage.store_raw_file_for_flight(
            conn,
            13,
            str(source),
            source.name,
            source.name,
        )
        row = conn.execute(
            "SELECT storage_rel_path FROM flight_raw_files WHERE flight_id=13"
        ).fetchone()
        expected = (
            "机型_Test Model_7/飞机_Tail 01_11/"
            "20260715_telemetry.txt"
        )
        assert row["storage_rel_path"] == expected
        assert (raw_root / Path(expected)).read_bytes() == b"flight data"

        directory = raw_storage.get_raw_directory_for_flight(conn, 13)
        assert Path(directory["path"]) == (
            raw_root / "机型_Test Model_7" / "飞机_Tail 01_11"
        )
    finally:
        conn.close()


def test_raw_path_omits_repeated_aircraft_directory_but_keeps_nested_directory():
    from backend import raw_storage

    flight = {
        "model_id": 7,
        "model_name": "Test Model",
        "aircraft_id": 11,
        "aircraft_name": "Tail 01",
        "flight_date": "2026-07-15",
    }

    from_date_directory = raw_storage._raw_file_rel_path(
        flight,
        "telemetry.txt",
        "Tail 01/ParserData/telemetry.txt",
    )
    from_aircraft_directory = raw_storage._raw_file_rel_path(
        flight,
        "telemetry.txt",
        "ParserData/telemetry.txt",
    )

    expected = (
        "机型_Test Model_7/飞机_Tail 01_11/ParserData/"
        "20260715_telemetry.txt"
    )
    assert from_date_directory == expected
    assert from_aircraft_directory == expected


def test_attach_raw_files_is_independent_of_selected_import_directory(tmp_path, monkeypatch):
    from backend import raw_storage

    date_root = tmp_path / "20260715-flight"
    aircraft_root = date_root / "Tail 01"
    source = aircraft_root / "ParserData" / "telemetry.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"flight data")
    storage_paths = []

    for index, source_root in enumerate((date_root, aircraft_root)):
        raw_root = tmp_path / f"raw_files_{index}"
        monkeypatch.setattr(raw_storage, "RAW_ROOT", str(raw_root))
        conn = _storage_connection()
        try:
            result = raw_storage.attach_raw_files_to_flight(
                conn,
                13,
                str(source_root),
                [{
                    "filepath": str(source),
                    "filename": source.name,
                    "aircraft_serial": "Tail 01",
                }],
            )
            row = conn.execute(
                "SELECT original_rel_path, storage_rel_path "
                "FROM flight_raw_files WHERE flight_id=13"
            ).fetchone()
            assert result == {"attached": 1, "warnings": []}
            assert row["original_rel_path"] == "ParserData/telemetry.txt"
            storage_paths.append(row["storage_rel_path"])
        finally:
            conn.close()

    assert storage_paths == [
        "机型_Test Model_7/飞机_Tail 01_11/ParserData/20260715_telemetry.txt",
        "机型_Test Model_7/飞机_Tail 01_11/ParserData/20260715_telemetry.txt",
    ]


def test_refresh_removes_repeated_aircraft_directory_from_legacy_path(tmp_path, monkeypatch):
    from backend import raw_storage

    raw_root = tmp_path / "raw_files"
    monkeypatch.setattr(raw_storage, "RAW_ROOT", str(raw_root))

    def reject_copy_move(*args, **kwargs):
        raise AssertionError("raw storage refresh must use a metadata rename")

    monkeypatch.setattr(raw_storage.shutil, "move", reject_copy_move)
    legacy_rel = (
        "Test Model__model_7/Tail 01__aircraft_11/"
        "Tail 01/ParserData/20260715_legacy.txt"
    )
    legacy_file = raw_root / Path(legacy_rel)
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(b"legacy data")
    sha256, size_bytes = raw_storage.hash_file(str(legacy_file))

    conn = _storage_connection()
    try:
        conn.execute(
            """INSERT INTO flight_raw_files
               (flight_id, original_name, original_rel_path, storage_rel_path,
                sha256, size_bytes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                13,
                "legacy.txt",
                "Tail 01/ParserData/legacy.txt",
                legacy_rel,
                sha256,
                size_bytes,
            ),
        )

        warnings = raw_storage.refresh_raw_storage_paths(conn)

        expected = (
            "机型_Test Model_7/飞机_Tail 01_11/ParserData/"
            "20260715_legacy.txt"
        )
        row = conn.execute(
            "SELECT storage_rel_path FROM flight_raw_files WHERE flight_id=13"
        ).fetchone()
        assert warnings == []
        assert row["storage_rel_path"] == expected
        assert (raw_root / Path(expected)).read_bytes() == b"legacy data"
        assert not legacy_file.exists()
        assert not legacy_file.parent.exists()
    finally:
        conn.close()
