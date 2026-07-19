from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
import zipfile

import pytest
from fastapi import HTTPException

from backend.api.server.routers import sync as server_sync_router
from backend.sync import local_import as sync_import
from backend.sync import package as sync_package
from backend.sync import server as server_sync
from backend.sync import workflow as sync_workflow
from backend.sync import cleanup as sync_cleanup
from backend.sync import progress as sync_progress
from backend.sync import repository as sync_repository
from backend.sync import upload_sessions
from backend.sync.protocol import model_structure_signature
from backend import permissions, server_database
from backend.import_pipeline.format_configs import update_column_metadata
from backend.repositories import models as model_repository


@pytest.mark.parametrize("module", [sync_package, sync_import])
def test_safe_zip_paths_and_sha256(module, tmp_path):
    assert module._safe_zip_path("data/parsed.sqlite") == "data/parsed.sqlite"
    for path in ("../escape", "data/../escape", "/absolute", "C:/absolute"):
        with pytest.raises(ValueError, match="Unsafe zip path"):
            module._safe_zip_path(path)

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"flight-analyzer")
    assert module._sha256_file(str(payload)) == hashlib.sha256(b"flight-analyzer").hexdigest()


def test_zip_member_traversal_is_rejected(tmp_path):
    package = tmp_path / "unsafe.fapkg"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("../manifest.json", "{}")
    with pytest.raises(ValueError, match="Unsafe zip path"):
        sync_import._load_manifest(str(package))


def test_manifest_required_fields_and_versions():
    manifest = {
        "package_version": sync_package.PACKAGE_VERSION,
        "schema_version": sync_package.CURRENT_SCHEMA_VERSION,
        "models": [],
        "aircraft": [],
        "flights": [],
        "raw_files": [],
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }
    sync_import._validate_manifest(manifest, require_compatible=True)

    missing = dict(manifest)
    missing.pop("raw_files")
    with pytest.raises(ValueError, match="manifest 缺少字段: raw_files"):
        sync_import._validate_manifest(missing, require_compatible=True)

    incompatible = dict(manifest, package_version=sync_package.PACKAGE_VERSION + 1)
    with pytest.raises(ValueError, match="当前版本暂不支持"):
        sync_import._validate_manifest(incompatible, require_compatible=True)


def test_minimal_export_package_contract(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    try:
        conn.execute("INSERT INTO aircraft_models (name) VALUES (?)", ("Package Model",))
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        result = sync_package.export_package(conn, [], model_ids=[model_id])

        with zipfile.ZipFile(result["path"], "r") as zf:
            names = sorted(zf.namelist())
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

        assert names == ["data/parsed.sqlite", "manifest.json", f"models/model_{model_id}.json"]
        assert manifest["package_version"] == 2
        assert manifest["sync_protocol_version"] == 1
        assert manifest["schema_version"] == 4
        assert manifest["bundle_kind"] == "manual_export"
        assert manifest["parsed_data"]["path"] == "data/parsed.sqlite"
        assert manifest["parsed_data"]["sha256"] == result["parsed_sha256"]
        assert manifest["models"][0]["config_signature"] == model_structure_signature(
            {
                "has_header": True,
                "has_uav_send_id": False,
                "extract_serial_from_path": False,
                "data_types": {},
            }
        )
        assert manifest["models"][0]["config"]["data_types"] == {}
        assert set(manifest) == {
            "package_version",
            "sync_protocol_version",
            "package_id",
            "bundle_kind",
            "app_version",
            "schema_version",
            "source_node_id",
            "source_environment",
            "exported_at",
            "base_server_cursor",
            "models",
            "aircraft",
            "flights",
            "raw_files",
            "parsed_data",
        }
    finally:
        conn.close()


def test_server_pull_bundle_uses_current_local_schema_version(monkeypatch, tmp_path):
    from backend.database import CURRENT_SCHEMA_VERSION

    monkeypatch.setattr(
        server_sync,
        "_changed_entity_ids",
        lambda *args, **kwargs: {"models": set(), "aircraft": set(), "flights": set()},
    )
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(server_sync, "_select_rows_by_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_sync, "_server_raw_manifest_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_sync, "_entity_redirects_since", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_sync.db, "SERVER_DATA_DIR", str(tmp_path))

    def write_parsed_sqlite(conn, ids, path):
        with open(path, "wb") as file:
            file.write(b"empty parsed sqlite fixture")
        return 0

    monkeypatch.setattr(server_sync, "_write_server_parsed_sqlite", write_parsed_sqlite)

    result = server_sync.build_pull_bundle(object(), since=0)
    with zipfile.ZipFile(result["path"], "r") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest["schema_version"] == CURRENT_SCHEMA_VERSION


def test_server_pull_bundle_writes_shared_raw_object_once(monkeypatch, tmp_path):
    raw_payload = b"shared raw object"
    raw_sha = hashlib.sha256(raw_payload).hexdigest()
    monkeypatch.setattr(server_sync.db, "SERVER_DATA_DIR", str(tmp_path))
    raw_path = upload_sessions.raw_object_abs_path(raw_sha)
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    with open(raw_path, "wb") as file:
        file.write(raw_payload)

    monkeypatch.setattr(
        server_sync,
        "_changed_entity_ids",
        lambda *args, **kwargs: {"models": set(), "aircraft": set(), "flights": set()},
    )
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(server_sync, "_select_rows_by_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_sync, "_entity_redirects_since", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        server_sync,
        "_server_raw_manifest_rows",
        lambda *args, **kwargs: [
            {
                "id": 1,
                "flight_id": 10,
                "original_name": "first.bin",
                "original_rel_path": "first.bin",
                "storage_rel_path": "aircraft/first.bin",
                "package_path": f"raw_objects/{raw_sha}",
                "sha256": raw_sha,
                "size_bytes": len(raw_payload),
            },
            {
                "id": 2,
                "flight_id": 11,
                "original_name": "second.bin",
                "original_rel_path": "second.bin",
                "storage_rel_path": "aircraft/second.bin",
                "package_path": f"raw_objects/{raw_sha}",
                "sha256": raw_sha,
                "size_bytes": len(raw_payload),
            },
        ],
    )

    def write_parsed_sqlite(conn, ids, path):
        with open(path, "wb") as file:
            file.write(b"empty parsed sqlite fixture")
        return 0

    monkeypatch.setattr(server_sync, "_write_server_parsed_sqlite", write_parsed_sqlite)

    result = server_sync.build_pull_bundle(object(), since=0)
    with zipfile.ZipFile(result["path"], "r") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        object_names = [name for name in archive.namelist() if name == f"raw_objects/{raw_sha}"]

    assert len(manifest["raw_files"]) == 2
    assert object_names == [f"raw_objects/{raw_sha}"]


def test_pull_preview_and_metadata_attach_records_by_client_uid(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    suffix = uuid.uuid4().hex
    model_uid = f"model-{suffix}"
    aircraft_uid = f"aircraft-{suffix}"
    flight_uid = f"flight-{suffix}"
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO aircraft_models (client_uid, name) VALUES (?, ?)",
            (model_uid, f"Model {suffix}"),
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO aircraft (client_uid, model_id, name) VALUES (?, ?, ?)",
            (aircraft_uid, model_id, f"Aircraft {suffix}"),
        )
        aircraft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO flights
               (client_uid, aircraft_id, name, source_path, session_key, flight_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (flight_uid, aircraft_id, f"Flight {suffix}", "local://flight", "115610", "2026-07-13"),
        )
        flight_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        manifest = {
            "source_node_id": "server",
            "server_cursor": "42",
            "models": [{"id": 101, "client_uid": model_uid, "name": f"Model {suffix}", "version": 1}],
            "aircraft": [{"id": 201, "client_uid": aircraft_uid, "model_id": 101, "name": f"Aircraft {suffix}", "version": 1}],
            "flights": [{
                "id": 301,
                "client_uid": flight_uid,
                "aircraft_id": 201,
                "name": f"Flight {suffix}",
                "source_path": "sync://server/301",
                "session_key": "115610",
                "flight_date": "2026-07-13",
                "version": 1,
            }],
            "raw_files": [],
        }

        preview = sync_import.preview_pull_manifest(conn, manifest)
        assert preview["models"][0]["local"]["id"] == model_id
        assert preview["models"][0]["matched_by"] == "client_uid"
        assert preview["aircraft"][0]["local"]["id"] == aircraft_id
        assert preview["aircraft"][0]["matched_by"] == "client_uid"
        assert preview["items"][0]["local"]["id"] == flight_id
        assert preview["items"][0]["matched_by"] == "client_uid"
        assert preview["items"][0]["action"] == "attach_existing"

        report = sync_import.apply_pull_manifest_metadata(conn, manifest)
        assert report["status"] == "success"
        assert conn.execute("SELECT server_id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()[0] == 101
        assert conn.execute("SELECT server_id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()[0] == 201
        assert conn.execute("SELECT server_id FROM flights WHERE id=?", (flight_id,)).fetchone()[0] == 301
    finally:
        conn.close()


def test_pull_preview_matches_aircraft_by_name_when_client_uid_differs(isolated_data_dir):
    """An aircraft created independently on another node (different client_uid,
    no server_id) must be recognised on pull by (model, name), mirroring the
    upload-side _find_aircraft fallback -- otherwise the download table reports
    'create' while the upload table reports 'existing' for the same aircraft."""
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        # Local model already linked to the server (matched by server_id).
        conn.execute(
            "INSERT INTO aircraft_models (client_uid, server_id, name, sync_state, sync_origin) "
            "VALUES (?, ?, ?, 'synced', 'server')",
            (f"model-local-{suffix}", 101, f"Model {suffix}"),
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Local aircraft created on this node: own client_uid, no server_id, pending upload.
        conn.execute(
            "INSERT INTO aircraft (client_uid, model_id, name, sync_state) VALUES (?, ?, ?, 'pending_upload')",
            (f"aircraft-local-{suffix}", model_id, "Aircraft001"),
        )
        aircraft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        manifest = {
            "source_node_id": "server",
            "server_cursor": "42",
            "models": [{"id": 101, "client_uid": f"model-local-{suffix}", "name": f"Model {suffix}", "version": 1}],
            "aircraft": [{
                "id": 201,
                "client_uid": f"aircraft-server-{suffix}",
                "model_id": 101,
                "name": "Aircraft001",
                "version": 1,
            }],
            "flights": [],
            "raw_files": [],
        }

        preview = sync_import.preview_pull_manifest(conn, manifest)
        item = preview["aircraft"][0]
        assert item["matched_by"] == "name"
        assert item["action"] == "existing"
        assert item["local"]["id"] == aircraft_id

        # Metadata apply links the existing local row instead of creating a duplicate.
        report = sync_import.apply_pull_manifest_metadata(conn, manifest)
        assert report["status"] == "success"
        row = conn.execute(
            "SELECT server_id, COUNT(*) AS n FROM aircraft WHERE model_id=?", (model_id,)
        ).fetchone()
        assert row["n"] == 1
        assert row["server_id"] == 201
    finally:
        conn.close()


def test_pull_preview_matches_model_by_name_when_client_uid_differs(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO aircraft_models (client_uid, name, sync_state) VALUES (?, ?, 'pending_upload')",
            (f"model-local-{suffix}", f"Model {suffix}"),
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        manifest = {
            "source_node_id": "server",
            "server_cursor": "42",
            "models": [{"id": 101, "client_uid": f"model-server-{suffix}", "name": f"Model {suffix}", "version": 1}],
            "aircraft": [],
            "flights": [],
            "raw_files": [],
        }

        preview = sync_import.preview_pull_manifest(conn, manifest)
        item = preview["models"][0]
        assert item["matched_by"] == "name"
        assert item["action"] == "existing"
        assert item["local"]["id"] == model_id
    finally:
        conn.close()


def test_preflight_flight_distinguishes_existing_from_update_metadata(monkeypatch):
    """An identical flight should preflight as 'existing'; only a real metadata
    change should be 'update_metadata'. Mirrors the model/aircraft behaviour and
    the import-side _flight_metadata_changed check."""
    suffix = uuid.uuid4().hex
    flight_uid = f"flight-{suffix}"
    base_flight = {
        "id": 301,
        "client_uid": flight_uid,
        "aircraft_id": 201,
        "name": f"Flight {suffix}",
        "source_path": "local://flight",
        "session_key": "115610",
        "flight_date": "2026-07-13",
        "record_location": "Site A",
        "record_note": "baseline",
    }

    # Server already holds this flight (matched by client_uid) with the same
    # metadata as the upload baseline.
    server_version = [1]

    def fake_find_flight_by_client_uid(conn, client_uid):
        if client_uid == flight_uid:
            return {
                "id": 9001,
                "client_uid": flight_uid,
                "version": server_version[0],
                "deleted_at": None,
                "name": base_flight["name"],
                "record_location": base_flight["record_location"],
                "record_note": base_flight["record_note"],
            }
        return None

    monkeypatch.setattr(server_sync, "_find_flight_by_client_uid", fake_find_flight_by_client_uid)
    monkeypatch.setattr(server_sync, "_find_entity_by_mapping", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_find_flight_by_business", lambda *a, **k: None)
    server_identity = [()]
    monkeypatch.setattr(
        server_sync,
        "_server_flight_content_identity",
        lambda conn, flight_id: server_identity[0],
    )
    monkeypatch.setattr(server_sync, "existing_import_report", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)

    def manifest_for(flight):
        return {
            "package_version": sync_package.PACKAGE_VERSION,
            "sync_protocol_version": sync_package.SYNC_PROTOCOL_VERSION,
            "package_id": f"pkg-{suffix}",
            "source_node_id": "node-A",
            "bundle_kind": "push_batch",
            "models": [],
            "aircraft": [],
            "flights": [flight],
            "raw_files": [],
            "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
        }

    identical = server_sync.build_preflight_plan(object(), manifest_for(base_flight))
    assert identical["flights"][0]["action"] == "existing"

    changed = server_sync.build_preflight_plan(
        object(), manifest_for(dict(base_flight, record_note="revised note"))
    )
    assert changed["flights"][0]["action"] == "update_metadata"

    server_version[0] = 5
    newer_server = server_sync.build_preflight_plan(
        object(), manifest_for(dict(base_flight, version=1, record_note="local wins"))
    )
    assert newer_server["status"] == "ready"
    assert newer_server["flights"][0]["action"] == "update_metadata"
    assert newer_server["flights"][0]["server_newer"] is True

    server_identity[0] = (("telemetry", "a" * 64),)
    mismatched_empty_set = server_sync.build_preflight_plan(
        object(), manifest_for(base_flight)
    )
    assert mismatched_empty_set["status"] == "conflict"
    assert mismatched_empty_set["flights"][0]["reason"] == "flight_raw_hash_mismatch"


def test_server_schema_has_node_scoped_entity_mapping_table():
    ddl = "\n".join(server_database.SCHEMA_DDL)
    assert "CREATE TABLE IF NOT EXISTS sync_entity_mappings" in ddl
    assert "(client_node_id, entity_type, client_entity_uid)" in ddl
    assert "INDEX idx_sync_entity_target (entity_type, server_entity_id)" in ddl
    assert "matched_by VARCHAR(32) NOT NULL" in ddl
    assert "CREATE TABLE IF NOT EXISTS sync_entity_redirects" in ddl
    assert "(entity_type, source_server_entity_id)" in ddl


def test_manifest_flight_identity_preserves_data_type_and_duplicate_entries():
    sha = "a" * 64
    manifest = {
        "raw_files": [
            {"flight_id": 7, "data_type_key": "telemetry", "sha256": sha},
            {"flight_id": 7, "data_type_key": "telemetry", "sha256": sha},
            {"flight_id": 7, "data_type_key": "alerts", "sha256": sha},
        ]
    }
    expected = (("alerts", sha), ("telemetry", sha), ("telemetry", sha))
    assert server_sync._manifest_content_identity_by_flight(manifest)[7] == expected
    assert sync_import._manifest_content_identity_by_flight(manifest)[7] == expected


def test_known_server_id_is_checked_before_cross_node_client_uid(monkeypatch):
    expected = {"id": 42, "config_signature": "a" * 64}
    monkeypatch.setattr(server_sync, "_find_entity_by_mapping", lambda *a, **k: None)
    monkeypatch.setattr(
        server_sync,
        "_entity_by_known_server_id",
        lambda conn, entity_type, server_id: expected if server_id == 42 else None,
    )
    row, matched_by = server_sync._find_model_match(
        object(), {"server_id": 42, "client_uid": "same-on-another-node"}, "node-A"
    )
    assert row == expected
    assert matched_by == "server_id"


def test_preflight_blocks_mapping_and_known_server_id_mismatch(monkeypatch):
    mapped = {"id": 42, "name": "Mapped", "version": 1, "config_signature": "a" * 64}
    monkeypatch.setattr(server_sync, "existing_import_report", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(server_sync, "_find_entity_by_mapping", lambda *a, **k: mapped)
    monkeypatch.setattr(
        server_sync,
        "_entity_by_known_server_id",
        lambda conn, entity_type, server_id: {"id": 43},
    )
    manifest = {
        "package_version": sync_package.PACKAGE_VERSION,
        "sync_protocol_version": sync_package.SYNC_PROTOCOL_VERSION,
        "package_id": "pkg-mapping-server-id-conflict",
        "source_node_id": "node-A",
        "bundle_kind": "push_batch",
        "models": [{
            "id": 1,
            "server_id": 43,
            "client_uid": "model-A",
            "name": "Mapped",
            "config_signature": "a" * 64,
        }],
        "aircraft": [],
        "flights": [],
        "raw_files": [],
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }
    result = server_sync.build_preflight_plan(object(), manifest)
    assert result["status"] == "conflict"
    assert result["models"][0]["reason"] == "mapping_server_id_mismatch"


def test_model_structure_signature_excludes_editable_metadata():
    base = _offline_model_config()
    edited = json.loads(json.dumps(base))
    edited["data_types"]["telemetry"]["display_label"] = "Edited type alias"
    edited["data_types"]["telemetry"]["columns"][0].update(
        {"label": "Edited column alias", "unit": "kn", "scale_factor": 3.6}
    )
    assert model_structure_signature(edited) == model_structure_signature(base)

    changed_column = json.loads(json.dumps(base))
    changed_column["data_types"]["telemetry"]["columns"][0]["name"] = "other_value"
    assert model_structure_signature(changed_column) != model_structure_signature(base)

    changed_type = json.loads(json.dumps(base))
    changed_type["data_types"]["telemetry"]["columns"][0]["type"] = "TEXT"
    assert model_structure_signature(changed_type) != model_structure_signature(base)

    changed_rule = json.loads(json.dumps(base))
    changed_rule["data_types"]["telemetry"]["file_patterns"] = ["*.csv"]
    assert model_structure_signature(changed_rule) != model_structure_signature(base)


def test_model_match_uses_structure_signature_before_name(monkeypatch):
    signature = "a" * 64
    matched = {
        "id": 71,
        "client_uid": "server-model-uid",
        "name": "Server Model Name",
        "config_signature": signature,
    }

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def first(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class Row:
        def __init__(self, values):
            self._mapping = values

    class Connection:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "sync_entity_mappings" in sql or "client_uid=:client_uid" in sql:
                return Result([])
            if "config_signature=:config_signature" in sql:
                return Result([Row(matched)])
            raise AssertionError(f"unexpected query: {sql}")

    row, matched_by = server_sync._find_model_match(
        Connection(),
        {"client_uid": "other-node-uid", "name": "Different Name", "config_signature": signature},
        "node-B",
    )
    assert row["id"] == 71
    assert matched_by == "structure_signature"


def test_preflight_maps_aircraft_and_flight_from_overlapping_raw_set(monkeypatch):
    raw_hash = "b" * 64
    server_model = {
        "id": 101,
        "client_uid": "server-model",
        "name": "Server Model",
        "config_signature": "c" * 64,
        "version": 1,
    }
    server_aircraft = {
        "id": 201,
        "client_uid": "server-aircraft",
        "model_id": 101,
        "name": "Server Aircraft",
        "version": 1,
    }
    server_flight = {
        "id": 301,
        "client_uid": "server-flight",
        "aircraft_id": 201,
        "name": "Server Flight",
        "session_key": "SERVER-SESSION",
        "flight_date": "2026-07-17",
        "version": 1,
        "deleted_at": None,
    }
    monkeypatch.setattr(server_sync, "existing_import_report", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(server_sync, "_find_model_match", lambda *a, **k: (server_model, "structure_signature"))
    monkeypatch.setattr(server_sync, "_find_model_by_name", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_find_aircraft_identity_match", lambda *a, **k: (None, None))
    monkeypatch.setattr(server_sync, "_overlap_aircraft_ids", lambda *a, **k: {201})
    monkeypatch.setattr(server_sync, "_find_aircraft_by_id", lambda *a, **k: server_aircraft)
    monkeypatch.setattr(server_sync, "_find_aircraft_by_name", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_find_flight_identity", lambda *a, **k: (None, None))
    monkeypatch.setattr(server_sync, "_find_flights_by_content_identity", lambda *a, **k: [server_flight])
    monkeypatch.setattr(
        server_sync, "_server_flight_content_identity", lambda *a, **k: (("", raw_hash),)
    )
    monkeypatch.setattr(server_sync.upload_sessions, "missing_raw_objects", lambda *a, **k: [])

    manifest = {
        "package_version": sync_package.PACKAGE_VERSION,
        "sync_protocol_version": sync_package.SYNC_PROTOCOL_VERSION,
        "package_id": "pkg-overlap",
        "source_node_id": "node-B",
        "bundle_kind": "push_batch",
        "models": [{
            "id": 1,
            "client_uid": "node-b-model",
            "name": "Local Model",
            "config_signature": "c" * 64,
        }],
        "aircraft": [{
            "id": 2,
            "client_uid": "node-b-aircraft",
            "model_id": 1,
            "name": "Local Aircraft",
        }],
        "flights": [{
            "id": 3,
            "client_uid": "node-b-flight",
            "aircraft_id": 2,
            "name": "Local Flight",
            "session_key": "LOCAL-SESSION",
            "flight_date": "2026-07-17",
        }],
        "raw_files": [{"flight_id": 3, "sha256": raw_hash, "size_bytes": 1}],
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }
    result = server_sync.build_preflight_plan(object(), manifest)
    assert result["status"] == "ready"
    assert result["aircraft"][0]["server_id"] == 201
    assert result["aircraft"][0]["matched_by"] == "overlapping_flight"
    assert result["flights"][0]["server_id"] == 301
    assert result["flights"][0]["matched_by"] == "raw_file_set"
    assert result["flights"][0]["action"] == "update_metadata"


def test_preflight_blocks_aircraft_overlap_across_multiple_targets(monkeypatch):
    monkeypatch.setattr(server_sync, "existing_import_report", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(
        server_sync,
        "_find_model_match",
        lambda *a, **k: ({"id": 101, "name": "Model", "version": 1}, "structure_signature"),
    )
    monkeypatch.setattr(server_sync, "_find_model_by_name", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_find_aircraft_identity_match", lambda *a, **k: (None, None))
    monkeypatch.setattr(server_sync, "_overlap_aircraft_ids", lambda *a, **k: {201, 202})
    manifest = {
        "package_version": sync_package.PACKAGE_VERSION,
        "sync_protocol_version": sync_package.SYNC_PROTOCOL_VERSION,
        "package_id": "pkg-overlap-conflict",
        "source_node_id": "node-B",
        "bundle_kind": "push_batch",
        "models": [{"id": 1, "name": "Model"}],
        "aircraft": [{"id": 2, "model_id": 1, "name": "Aircraft"}],
        "flights": [],
        "raw_files": [],
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }
    result = server_sync.build_preflight_plan(object(), manifest)
    assert result["status"] == "conflict"
    assert result["aircraft"][0]["reason"] == "aircraft_overlap_multiple_targets"


def test_upload_preflight_plans_safe_aircraft_merge_from_new_overlap(monkeypatch):
    server_model = {"id": 100, "name": "Model", "version": 1}
    source_aircraft = {
        "id": 201,
        "model_id": 100,
        "name": "Source Aircraft",
        "version": 1,
    }
    target_aircraft = {
        "id": 202,
        "model_id": 100,
        "name": "Target Aircraft",
        "version": 1,
    }
    merge_plan = {
        "ok": True,
        "status": "ready",
        "entity_type": "aircraft",
        "source_id": 201,
        "target_id": 202,
        "source": source_aircraft,
        "target": target_aircraft,
        "move_flight_ids": [],
        "duplicate_flights": [],
        "conflicts": [],
    }
    monkeypatch.setattr(server_sync, "existing_import_report", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(server_sync, "_find_model_match", lambda *a, **k: (server_model, "structure_signature"))
    monkeypatch.setattr(server_sync, "_find_model_by_name", lambda *a, **k: server_model)
    monkeypatch.setattr(
        server_sync,
        "_find_aircraft_identity_match",
        lambda *a, **k: (source_aircraft, "entity_mapping"),
    )
    monkeypatch.setattr(server_sync, "_overlap_aircraft_ids", lambda *a, **k: {202})
    monkeypatch.setattr(server_sync, "preflight_entity_merge", lambda *a, **k: merge_plan)
    monkeypatch.setattr(
        server_sync,
        "_find_aircraft_by_id",
        lambda conn, entity_id: target_aircraft if entity_id == 202 else source_aircraft,
    )
    monkeypatch.setattr(server_sync, "_find_aircraft_by_name", lambda *a, **k: None)
    manifest = {
        "package_version": sync_package.PACKAGE_VERSION,
        "sync_protocol_version": sync_package.SYNC_PROTOCOL_VERSION,
        "package_id": "pkg-safe-auto-merge",
        "source_node_id": "node-B",
        "bundle_kind": "push_batch",
        "models": [{"id": 1, "name": "Model"}],
        "aircraft": [{"id": 2, "model_id": 1, "name": "Source Aircraft"}],
        "flights": [],
        "raw_files": [],
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }
    result = server_sync.build_preflight_plan(object(), manifest)
    assert result["status"] == "ready"
    assert result["aircraft"][0]["action"] == "merge"
    assert result["aircraft"][0]["server_id"] == 202
    assert result["aircraft"][0]["merge_plan"]["source_id"] == 201


def test_aircraft_merge_preflight_moves_unique_and_merges_identical_flights(monkeypatch):
    source_aircraft = {"id": 10, "model_id": 1, "name": "Source", "deleted_at": None}
    target_aircraft = {"id": 20, "model_id": 1, "name": "Target", "deleted_at": None}
    source_flights = [
        {"id": 101, "flight_date": "2026-07-17", "session_key": "A"},
        {"id": 102, "flight_date": "2026-07-18", "session_key": "B"},
    ]
    target_duplicate = {"id": 201, "flight_date": "2026-07-17", "session_key": "A", "deleted_at": None}
    monkeypatch.setattr(
        server_sync,
        "_find_aircraft_by_id",
        lambda conn, entity_id: source_aircraft if entity_id == 10 else target_aircraft,
    )
    monkeypatch.setattr(server_sync, "_active_aircraft_flights", lambda *a, **k: source_flights)
    monkeypatch.setattr(
        server_sync,
        "_find_flight_by_business",
        lambda conn, aircraft_id, flight_date, session_key: (
            target_duplicate if session_key == "A" else None
        ),
    )
    monkeypatch.setattr(
        server_sync,
        "_server_flight_content_identity",
        lambda *a, **k: (("telemetry", "a" * 64),),
    )

    plan = server_sync._aircraft_merge_plan(
        object(), 10, 20, allow_different_models=False
    )
    assert plan["ok"] is True
    assert plan["move_flight_ids"] == [102]
    assert plan["duplicate_flights"] == [
        {"source_flight_id": 101, "target_flight_id": 201}
    ]


def test_aircraft_merge_preflight_blocks_same_business_key_with_different_files(monkeypatch):
    monkeypatch.setattr(
        server_sync,
        "_find_aircraft_by_id",
        lambda conn, entity_id: {
            "id": entity_id,
            "model_id": 1,
            "name": str(entity_id),
            "deleted_at": None,
        },
    )
    monkeypatch.setattr(
        server_sync,
        "_active_aircraft_flights",
        lambda *a, **k: [{"id": 101, "flight_date": "2026-07-17", "session_key": "A"}],
    )
    monkeypatch.setattr(
        server_sync,
        "_find_flight_by_business",
        lambda *a, **k: {"id": 201, "deleted_at": None},
    )
    monkeypatch.setattr(
        server_sync,
        "_server_flight_content_identity",
        lambda conn, flight_id: (("telemetry", ("a" if flight_id == 101 else "b") * 64),),
    )
    plan = server_sync._aircraft_merge_plan(
        object(), 10, 20, allow_different_models=False
    )
    assert plan["ok"] is False
    assert plan["conflicts"][0]["reason"] == "flight_business_key_raw_mismatch"


def test_preflight_prefers_node_scoped_mapping_and_validates_model_structure(monkeypatch):
    mapped_model = {
        "id": 9001,
        "client_uid": "canonical-server-uid",
        "name": "Mapped Model",
        "config_signature": "server-signature",
        "version": 1,
        "deleted_at": None,
    }
    monkeypatch.setattr(server_sync, "existing_import_report", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_max_cursor", lambda conn: 0)
    monkeypatch.setattr(
        server_sync,
        "_find_entity_by_mapping",
        lambda conn, node_id, entity_type, client_uid: (
            mapped_model
            if (node_id, entity_type, client_uid) == ("node-B", "model", "node-b-model-uid")
            else None
        ),
    )
    monkeypatch.setattr(server_sync, "_find_model_by_name", lambda conn, name: mapped_model)

    manifest = {
        "package_version": sync_package.PACKAGE_VERSION,
        "sync_protocol_version": sync_package.SYNC_PROTOCOL_VERSION,
        "package_id": "pkg-node-scoped-model",
        "source_node_id": "node-B",
        "bundle_kind": "push_batch",
        "models": [{
            "id": 1,
            "client_uid": "node-b-model-uid",
            "name": "Mapped Model",
            "config_signature": "server-signature",
        }],
        "aircraft": [],
        "flights": [],
        "raw_files": [],
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }

    ready = server_sync.build_preflight_plan(object(), manifest)
    assert ready["models"][0]["matched_by"] == "entity_mapping"
    assert ready["models"][0]["server_id"] == 9001
    assert ready["models"][0]["action"] == "existing"

    conflicting = server_sync.build_preflight_plan(
        object(),
        {
            **manifest,
            "package_id": "pkg-node-scoped-model-conflict",
            "models": [{**manifest["models"][0], "config_signature": "different-signature"}],
        },
    )
    assert conflicting["status"] == "conflict"
    assert conflicting["models"][0]["reason"] == "model_config_mismatch"


def _offline_model_config(column_name: str = "value") -> dict:
    return {
        "has_header": True,
        "has_uav_send_id": False,
        "extract_serial_from_path": False,
        "data_types": {
            "telemetry": {
                "display_label": "Telemetry",
                "file_patterns": ["*.txt"],
                "is_alert": False,
                "columns": [
                    {
                        "name": column_name,
                        "label": column_name,
                        "unit": "",
                        "type": "REAL",
                        "ordinal": 1,
                    }
                ],
            }
        },
    }


def _write_offline_model_package(path, model_id: int, config: dict) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"models/model_{model_id}.json",
            json.dumps(config, ensure_ascii=False),
        )


def _offline_import_report() -> dict:
    return {
        "created_models": [],
        "created_aircraft": [],
        "imported_flights": [],
        "skipped_flights": [],
        "updated_flights": [],
    }


def test_offline_created_entities_inherit_package_client_uids(isolated_data_dir, tmp_path):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    package_path = tmp_path / "offline-uids.fapkg"
    suffix = uuid.uuid4().hex
    model_name = f"Package Model {suffix}"
    aircraft_name = f"Package Aircraft {suffix}"
    config = _offline_model_config()
    _write_offline_model_package(package_path, 11, config)
    manifest = {
        "source_node_id": "field-node",
        "models": [{"id": 11, "client_uid": f"package-model-{suffix}", "name": model_name}],
        "aircraft": [{
            "id": 22,
            "client_uid": f"package-aircraft-{suffix}",
            "model_id": 11,
            "name": aircraft_name,
        }],
        "flights": [{
            "id": 33,
            "client_uid": f"package-flight-{suffix}",
            "aircraft_id": 22,
            "name": f"Package Flight {suffix}",
            "session_key": "S1",
            "flight_date": "2026-07-17",
        }],
        "raw_files": [],
    }
    options = {
        "model_actions": [{"source_model_id": 11, "action": "create"}],
        "aircraft_mappings": [{"source_aircraft_id": 22, "action": "create"}],
    }
    report = _offline_import_report()
    try:
        model_map = sync_import._resolve_models(
            conn, str(package_path), manifest, options, report
        )
        aircraft_map = sync_import._resolve_aircraft(
            conn, manifest, model_map, options, report
        )
        flight_map, _ = sync_import._import_flights(
            conn, manifest, aircraft_map, options, report
        )

        assert conn.execute(
            "SELECT client_uid FROM aircraft_models WHERE id=?", (model_map[11],)
        ).fetchone()[0] == f"package-model-{suffix}"
        assert conn.execute(
            "SELECT client_uid FROM aircraft WHERE id=?", (aircraft_map[22],)
        ).fetchone()[0] == f"package-aircraft-{suffix}"
        assert conn.execute(
            "SELECT client_uid FROM flights WHERE id=?", (flight_map[33],)
        ).fetchone()[0] == f"package-flight-{suffix}"
    finally:
        conn.close()


def test_offline_matching_existing_entities_preserves_target_uids(isolated_data_dir, tmp_path):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    package_path = tmp_path / "offline-existing-uids.fapkg"
    config = _offline_model_config()
    _write_offline_model_package(package_path, 11, config)
    try:
        model_id = sync_import._create_model_from_config(
            conn, "Existing Model", config, "target-model-uid"
        )
        aircraft_id = sync_import._create_aircraft(
            conn, model_id, "Existing Aircraft", "target-aircraft-uid"
        )
        conn.execute(
            """INSERT INTO flights
               (client_uid, aircraft_id, name, source_path, session_key, flight_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "target-flight-uid",
                aircraft_id,
                "Existing Flight",
                "local://existing",
                "S1",
                "2026-07-17",
            ),
        )
        flight_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        manifest = {
            "source_node_id": "field-node",
            "models": [{"id": 11, "client_uid": "package-model-uid", "name": "Renamed Model"}],
            "aircraft": [{
                "id": 22,
                "client_uid": "package-aircraft-uid",
                "model_id": 11,
                "name": "Package Aircraft Name",
            }],
            "flights": [{
                "id": 33,
                "client_uid": "package-flight-uid",
                "aircraft_id": 22,
                "name": "Package Flight Name",
                "session_key": "S1",
                "flight_date": "2026-07-17",
                "record_note": "package note",
            }],
            "raw_files": [],
        }
        options = {
            "metadata_strategy": "target_wins",
            "aircraft_mappings": [{
                "source_aircraft_id": 22,
                "action": "use_existing",
                "target_aircraft_id": aircraft_id,
            }],
        }
        report = _offline_import_report()
        model_map = sync_import._resolve_models(
            conn, str(package_path), manifest, options, report
        )
        aircraft_map = sync_import._resolve_aircraft(
            conn, manifest, model_map, options, report
        )
        flight_map, _ = sync_import._import_flights(
            conn, manifest, aircraft_map, options, report
        )

        assert model_map[11] == model_id
        assert aircraft_map[22] == aircraft_id
        assert flight_map[33] == flight_id
        assert conn.execute(
            "SELECT client_uid FROM aircraft_models WHERE id=?", (model_id,)
        ).fetchone()[0] == "target-model-uid"
        assert conn.execute(
            "SELECT client_uid FROM aircraft WHERE id=?", (aircraft_id,)
        ).fetchone()[0] == "target-aircraft-uid"
        assert conn.execute(
            "SELECT client_uid FROM flights WHERE id=?", (flight_id,)
        ).fetchone()[0] == "target-flight-uid"
        assert conn.execute(
            "SELECT name FROM aircraft_models WHERE id=?", (model_id,)
        ).fetchone()[0] == "Existing Model"
        assert conn.execute(
            "SELECT name FROM aircraft WHERE id=?", (aircraft_id,)
        ).fetchone()[0] == "Existing Aircraft"
        flight = conn.execute(
            "SELECT name, record_note FROM flights WHERE id=?", (flight_id,)
        ).fetchone()
        assert flight["name"] == "Existing Flight"
        assert flight["record_note"] == ""
    finally:
        conn.close()


def test_offline_package_wins_overwrites_all_mutable_metadata(isolated_data_dir, tmp_path):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    package_path = tmp_path / "offline-package-wins.fapkg"
    suffix = uuid.uuid4().hex
    target_model_name = f"Target Model {suffix}"
    target_aircraft_name = f"Target Aircraft {suffix}"
    package_model_name = f"Package Model {suffix}"
    package_aircraft_name = f"Package Aircraft {suffix}"
    target_config = _offline_model_config()
    package_config = _offline_model_config()
    package_config["data_types"]["telemetry"]["display_label"] = "Package Telemetry"
    package_config["data_types"]["telemetry"]["columns"][0].update(
        {"label": "Package Value", "unit": "kn", "scale_factor": 3.6}
    )
    _write_offline_model_package(package_path, 11, package_config)
    try:
        model_id = sync_import._create_model_from_config(
            conn, target_model_name, target_config, f"target-model-{suffix}"
        )
        aircraft_id = sync_import._create_aircraft(
            conn, model_id, target_aircraft_name, f"target-aircraft-{suffix}"
        )
        conn.execute(
            """INSERT INTO flights
               (client_uid, aircraft_id, name, source_path, session_key, flight_date, record_note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"target-flight-{suffix}",
                aircraft_id,
                "Target Flight",
                "local://target",
                "S1",
                "2026-07-17",
                "target note",
            ),
        )
        flight_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute("UPDATE aircraft_models SET sync_state='synced' WHERE id=?", (model_id,))
        conn.execute("UPDATE aircraft SET sync_state='synced' WHERE id=?", (aircraft_id,))
        conn.execute("UPDATE flights SET sync_state='synced' WHERE id=?", (flight_id,))
        manifest = {
            "source_node_id": "field-node",
            "models": [{"id": 11, "client_uid": f"package-model-{suffix}", "name": package_model_name}],
            "aircraft": [{
                "id": 22,
                "client_uid": f"package-aircraft-{suffix}",
                "model_id": 11,
                "name": package_aircraft_name,
            }],
            "flights": [{
                "id": 33,
                "client_uid": f"package-flight-{suffix}",
                "aircraft_id": 22,
                "name": "Package Flight",
                "session_key": "S1",
                "flight_date": "2026-07-17",
                "record_note": "package note",
            }],
            "raw_files": [],
        }
        options = {
            "metadata_strategy": "package_wins",
            "aircraft_mappings": [{
                "source_aircraft_id": 22,
                "action": "use_existing",
                "target_aircraft_id": aircraft_id,
            }],
        }
        report = _offline_import_report()
        model_map = sync_import._resolve_models(
            conn, str(package_path), manifest, options, report
        )
        aircraft_map = sync_import._resolve_aircraft(
            conn, manifest, model_map, options, report
        )
        flight_map, _ = sync_import._import_flights(
            conn, manifest, aircraft_map, options, report
        )

        model = conn.execute(
            "SELECT name, client_uid, sync_state FROM aircraft_models WHERE id=?", (model_id,)
        ).fetchone()
        assert dict(model) == {
            "name": package_model_name,
            "client_uid": f"target-model-{suffix}",
            "sync_state": "dirty",
        }
        data_type = conn.execute(
            "SELECT display_label FROM data_table_registry WHERE model_id=? AND data_type_key='telemetry'",
            (model_id,),
        ).fetchone()
        column = conn.execute(
            """SELECT display_label, unit, scale_factor FROM column_registry
               WHERE model_id=? AND data_type_key='telemetry' AND column_name='value'""",
            (model_id,),
        ).fetchone()
        assert data_type["display_label"] == "Package Telemetry"
        assert dict(column) == {
            "display_label": "Package Value",
            "unit": "kn",
            "scale_factor": 3.6,
        }
        aircraft = conn.execute(
            "SELECT name, client_uid, sync_state FROM aircraft WHERE id=?", (aircraft_id,)
        ).fetchone()
        assert dict(aircraft) == {
            "name": package_aircraft_name,
            "client_uid": f"target-aircraft-{suffix}",
            "sync_state": "dirty",
        }
        flight = conn.execute(
            "SELECT name, record_note, client_uid, sync_state FROM flights WHERE id=?", (flight_id,)
        ).fetchone()
        assert dict(flight) == {
            "name": "Package Flight",
            "record_note": "package note",
            "client_uid": f"target-flight-{suffix}",
            "sync_state": "dirty",
        }
        assert model_map[11] == model_id
        assert aircraft_map[22] == aircraft_id
        assert flight_map[33] == flight_id
    finally:
        conn.close()


def test_offline_independent_copy_generates_new_uids(isolated_data_dir, tmp_path):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    package_path = tmp_path / "offline-independent.fapkg"
    suffix = uuid.uuid4().hex
    _write_offline_model_package(package_path, 11, _offline_model_config())
    manifest = {
        "models": [{"id": 11, "client_uid": f"package-model-{suffix}", "name": f"Package Model {suffix}"}],
        "aircraft": [{
            "id": 22,
            "client_uid": f"package-aircraft-{suffix}",
            "model_id": 11,
            "name": f"Package Aircraft {suffix}",
        }],
    }
    options = {
        "model_actions": [{
            "source_model_id": 11,
            "action": "create_independent",
            "name": f"Independent Model {suffix}",
        }],
        "aircraft_mappings": [{
            "source_aircraft_id": 22,
            "action": "create_independent",
            "name": f"Independent Aircraft {suffix}",
        }],
    }
    try:
        report = _offline_import_report()
        model_map = sync_import._resolve_models(
            conn, str(package_path), manifest, options, report
        )
        aircraft_map = sync_import._resolve_aircraft(
            conn, manifest, model_map, options, report
        )
        model_uid = conn.execute(
            "SELECT client_uid FROM aircraft_models WHERE id=?", (model_map[11],)
        ).fetchone()[0]
        aircraft_uid = conn.execute(
            "SELECT client_uid FROM aircraft WHERE id=?", (aircraft_map[22],)
        ).fetchone()[0]
        assert model_uid != f"package-model-{suffix}" and len(model_uid) == 32
        assert aircraft_uid != f"package-aircraft-{suffix}" and len(aircraft_uid) == 32
    finally:
        conn.close()


def test_offline_new_entity_name_collision_is_blocked(isolated_data_dir, tmp_path):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    name = f"Occupied Model {suffix}"
    package_path = tmp_path / "offline-name-conflict.fapkg"
    _write_offline_model_package(package_path, 11, _offline_model_config())
    try:
        sync_import._create_model_from_config(
            conn, name, _offline_model_config(), f"existing-{suffix}"
        )
        manifest = {
            "models": [{"id": 11, "client_uid": f"package-{suffix}", "name": name}],
        }
        options = {
            "model_actions": [{
                "source_model_id": 11,
                "action": "create_independent",
                "name": name,
            }],
        }
        with pytest.raises(ValueError, match="名称已被另一个实体占用"):
            sync_import._resolve_models(
                conn, str(package_path), manifest, options, _offline_import_report()
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM aircraft_models WHERE name=?", (name,)
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_offline_same_model_uid_with_different_structure_is_blocked(isolated_data_dir, tmp_path):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    package_path = tmp_path / "offline-model-conflict.fapkg"
    _write_offline_model_package(package_path, 11, _offline_model_config("different_value"))
    try:
        sync_import._create_model_from_config(
            conn, "Existing Model", _offline_model_config(), "shared-model-uid"
        )
        manifest = {
            "models": [{"id": 11, "client_uid": "shared-model-uid", "name": "Existing Model"}],
        }
        with pytest.raises(ValueError, match="机型不可变结构冲突"):
            sync_import._resolve_models(
                conn, str(package_path), manifest, {}, _offline_import_report()
            )
    finally:
        conn.close()


def test_model_alias_and_column_metadata_edits_enter_sync_queue(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    try:
        model_id = sync_import._create_model_from_config(
            conn, "Synced Model", _offline_model_config(), "synced-model-uid"
        )
        conn.execute(
            "UPDATE aircraft_models SET sync_state='synced' WHERE id=?",
            (model_id,),
        )
        assert model_repository.update_data_type_label(
            conn, model_id, "telemetry", "Edited Telemetry"
        )
        assert conn.execute(
            "SELECT sync_state FROM aircraft_models WHERE id=?", (model_id,)
        ).fetchone()[0] == "dirty"

        conn.execute(
            "UPDATE aircraft_models SET sync_state='synced' WHERE id=?",
            (model_id,),
        )
        update_column_metadata(
            conn,
            model_id,
            "telemetry",
            "value",
            display_label="Edited Value",
            unit="kn",
            scale_factor=3.6,
        )
        assert conn.execute(
            "SELECT sync_state FROM aircraft_models WHERE id=?", (model_id,)
        ).fetchone()[0] == "dirty"
    finally:
        conn.close()


def test_pull_redirect_relinks_single_local_entity_and_skips_source_tombstone(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        conn.execute(
            """INSERT INTO aircraft_models
               (client_uid, server_id, name, sync_origin, sync_state)
               VALUES (?, ?, ?, 'server', 'server_cache')""",
            (f"source-{suffix}", 101, f"Source Model {suffix}"),
        )
        local_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        manifest = {
            "source_node_id": "server",
            "server_cursor": 9,
            "models": [
                {
                    "id": 101,
                    "client_uid": f"source-{suffix}",
                    "name": f"Merged Source {suffix}",
                    "version": 2,
                    "deleted_at": "2026-07-17T00:00:00",
                },
                {
                    "id": 102,
                    "client_uid": f"target-{suffix}",
                    "name": f"Target Model {suffix}",
                    "version": 3,
                },
            ],
            "aircraft": [],
            "flights": [],
            "raw_files": [],
            "entity_redirects": [{
                "entity_type": "model",
                "source_server_entity_id": 101,
                "target_server_entity_id": 102,
            }],
        }
        preview = sync_import.preview_pull_manifest(conn, manifest)
        assert len(preview["models"]) == 1
        assert preview["models"][0]["server_id"] == 102
        assert preview["models"][0]["matched_by"] == "entity_redirect"
        assert preview["entity_redirects"][0]["action"] == "relink"
        assert preview["summary"]["bundle_required"] == 0

        report = sync_import.apply_pull_manifest_metadata(conn, manifest)
        assert report["status"] == "success"
        assert report["redirects_applied"] == 1
        row = conn.execute(
            "SELECT id, server_id, name, server_deleted_at FROM aircraft_models WHERE id=?",
            (local_id,),
        ).fetchone()
        assert row["server_id"] == 102
        assert row["name"] == f"Target Model {suffix}"
        assert row["server_deleted_at"] is None
    finally:
        conn.close()


def test_pull_redirect_merges_clean_local_aircraft_caches(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        model_id = sync_import._create_model_from_config(
            conn, f"Redirect Model {suffix}", _offline_model_config(), f"model-{suffix}"
        )
        source_aircraft_id = sync_import._create_aircraft(
            conn, model_id, f"Source Aircraft {suffix}", f"source-aircraft-{suffix}"
        )
        target_aircraft_id = sync_import._create_aircraft(
            conn, model_id, f"Target Aircraft {suffix}", f"target-aircraft-{suffix}"
        )
        conn.execute(
            "UPDATE aircraft SET server_id=201, sync_state='server_cache' WHERE id=?",
            (source_aircraft_id,),
        )
        conn.execute(
            "UPDATE aircraft SET server_id=202, sync_state='server_cache' WHERE id=?",
            (target_aircraft_id,),
        )
        for aircraft_id, uid in (
            (source_aircraft_id, f"source-flight-{suffix}"),
            (target_aircraft_id, f"target-flight-{suffix}"),
        ):
            conn.execute(
                """INSERT INTO flights
                   (client_uid, aircraft_id, name, source_path, session_key, flight_date,
                    server_id, sync_origin, sync_state)
                   VALUES (?, ?, ?, ?, 'A', '2026-07-17', ?, 'server', 'server_cache')""",
                (
                    uid,
                    aircraft_id,
                    f"Flight {suffix}",
                    f"sync://{uid}",
                    301 if aircraft_id == source_aircraft_id else 302,
                ),
            )
        report = {"conflicts": [], "redirects_applied": 0}
        manifest = {
            "entity_redirects": [
                {
                    "entity_type": "flight",
                    "source_server_entity_id": 301,
                    "target_server_entity_id": 302,
                },
                {
                    "entity_type": "aircraft",
                    "source_server_entity_id": 201,
                    "target_server_entity_id": 202,
                },
            ]
        }
        assert sync_import._apply_local_redirects(conn, manifest, report) is True
        assert report["redirects_applied"] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM aircraft WHERE id IN (?, ?)",
            (source_aircraft_id, target_aircraft_id),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM flights WHERE aircraft_id=?", (target_aircraft_id,)
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_pull_redirect_blocks_unsynced_local_changes(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        conn.execute(
            """INSERT INTO aircraft_models
               (client_uid, server_id, name, sync_state)
               VALUES (?, 401, ?, 'dirty')""",
            (f"dirty-{suffix}", f"Dirty Model {suffix}"),
        )
        local_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        report = {"conflicts": [], "redirects_applied": 0}
        manifest = {
            "entity_redirects": [{
                "entity_type": "model",
                "source_server_entity_id": 401,
                "target_server_entity_id": 402,
            }]
        }
        assert sync_import._apply_local_redirects(conn, manifest, report) is False
        assert report["conflicts"][0]["reason"] == "local_changes_block_redirect"
        assert conn.execute(
            "SELECT server_id FROM aircraft_models WHERE id=?", (local_id,)
        ).fetchone()[0] == 401
    finally:
        conn.close()


def test_pull_redirect_blocks_unsynced_descendant_changes(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        model_id = sync_import._create_model_from_config(
            conn, f"Parent {suffix}", _offline_model_config(), f"model-{suffix}"
        )
        aircraft_id = sync_import._create_aircraft(
            conn, model_id, f"Aircraft {suffix}", f"aircraft-{suffix}"
        )
        conn.execute(
            """INSERT INTO flights
               (client_uid, aircraft_id, name, source_path, session_key,
                server_id, sync_origin, sync_state)
               VALUES (?, ?, ?, 'local://dirty-child', 'A', 603, 'server', 'dirty')""",
            (f"flight-{suffix}", aircraft_id, f"Flight {suffix}"),
        )
        conn.execute(
            "UPDATE aircraft_models SET server_id=601, sync_state='server_cache' WHERE id=?",
            (model_id,),
        )
        conn.execute(
            "UPDATE aircraft SET server_id=602, sync_state='server_cache' WHERE id=?",
            (aircraft_id,),
        )
        report = {"conflicts": [], "redirects_applied": 0}
        manifest = {
            "entity_redirects": [{
                "entity_type": "model",
                "source_server_entity_id": 601,
                "target_server_entity_id": 611,
            }]
        }
        assert sync_import._apply_local_redirects(conn, manifest, report) is False
        assert report["conflicts"][0]["reason"] == "local_changes_block_redirect"
        assert conn.execute(
            "SELECT server_id FROM aircraft_models WHERE id=?", (model_id,)
        ).fetchone()[0] == 601
    finally:
        conn.close()


def test_pull_redirects_converge_multiple_local_sources_on_one_target(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        source_ids = []
        for index, server_id in enumerate((701, 702), start=1):
            model_id = sync_import._create_model_from_config(
                conn,
                f"Source {index} {suffix}",
                _offline_model_config(),
                f"model-{index}-{suffix}",
            )
            conn.execute(
                "UPDATE aircraft_models SET server_id=?, sync_state='server_cache' WHERE id=?",
                (server_id, model_id),
            )
            source_ids.append(model_id)
        report = {"conflicts": [], "redirects_applied": 0}
        manifest = {
            "entity_redirects": [
                {
                    "entity_type": "model",
                    "source_server_entity_id": server_id,
                    "target_server_entity_id": 703,
                }
                for server_id in (701, 702)
            ]
        }
        assert sync_import._apply_local_redirects(conn, manifest, report) is True
        rows = conn.execute(
            "SELECT id, server_id FROM aircraft_models WHERE id IN (?, ?)", source_ids
        ).fetchall()
        assert [(row["server_id"]) for row in rows] == [703]
        assert report["redirects_applied"] == 2
    finally:
        conn.close()


def test_node_id_cannot_change_after_sync_starts(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    try:
        permissions.set_app_context(conn, {"node_id": "node-before-sync"})
        conn.execute(
            """INSERT INTO app_settings(key, value) VALUES ('last_pull_cursor', '1')
               ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
        )
        with pytest.raises(ValueError, match="cannot be changed"):
            permissions.set_app_context(conn, {"node_id": "node-after-sync"})
        assert permissions.get_app_context(conn)["node_id"] == "node-before-sync"
    finally:
        conn.close()


def test_pull_direction_overwrites_dirty_metadata_from_server(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    local_config = _offline_model_config()
    server_config = _offline_model_config()
    server_config["data_types"]["telemetry"]["display_label"] = "Server Telemetry"
    server_config["data_types"]["telemetry"]["columns"][0].update(
        {"label": "Server Value", "unit": "m/s", "scale_factor": 2.0}
    )
    try:
        model_id = sync_import._create_model_from_config(
            conn, f"Local Model {suffix}", local_config, f"model-{suffix}"
        )
        aircraft_id = sync_import._create_aircraft(
            conn, model_id, f"Local Aircraft {suffix}", f"aircraft-{suffix}"
        )
        conn.execute(
            """INSERT INTO flights
               (client_uid, aircraft_id, name, source_path, session_key, flight_date,
                record_note, server_id, sync_origin, sync_state)
               VALUES (?, ?, ?, 'local://dirty', 'A', '2026-07-17', 'local note',
                       303, 'server', 'dirty')""",
            (f"flight-{suffix}", aircraft_id, f"Local Flight {suffix}"),
        )
        conn.execute(
            "UPDATE aircraft_models SET server_id=101, sync_origin='server', sync_state='dirty' WHERE id=?",
            (model_id,),
        )
        conn.execute(
            "UPDATE aircraft SET server_id=202, sync_origin='server', sync_state='dirty' WHERE id=?",
            (aircraft_id,),
        )
        manifest = {
            "source_node_id": "server",
            "server_cursor": 12,
            "models": [{
                "id": 101,
                "client_uid": f"server-model-{suffix}",
                "name": f"Server Model {suffix}",
                "version": 4,
                "config": server_config,
            }],
            "aircraft": [{
                "id": 202,
                "client_uid": f"server-aircraft-{suffix}",
                "model_id": 101,
                "name": f"Server Aircraft {suffix}",
                "version": 5,
            }],
            "flights": [{
                "id": 303,
                "client_uid": f"server-flight-{suffix}",
                "aircraft_id": 202,
                "name": f"Server Flight {suffix}",
                "session_key": "A",
                "flight_date": "2026-07-17",
                "record_note": "server note",
                "version": 6,
            }],
            "raw_files": [],
            "entity_redirects": [],
        }
        preview = sync_import.preview_pull_manifest(conn, manifest)
        assert preview["ok"] is True
        assert preview["models"][0]["action"] == "update_metadata"
        assert preview["aircraft"][0]["action"] == "update_metadata"
        assert preview["items"][0]["action"] == "update"

        report = sync_import.apply_pull_manifest_metadata(conn, manifest)
        assert report["status"] == "success"
        model = conn.execute(
            "SELECT name, sync_state, server_version FROM aircraft_models WHERE id=?",
            (model_id,),
        ).fetchone()
        assert dict(model) == {
            "name": f"Server Model {suffix}",
            "sync_state": "server_cache",
            "server_version": 4,
        }
        aircraft = conn.execute(
            "SELECT name, sync_state, server_version FROM aircraft WHERE id=?",
            (aircraft_id,),
        ).fetchone()
        assert dict(aircraft) == {
            "name": f"Server Aircraft {suffix}",
            "sync_state": "server_cache",
            "server_version": 5,
        }
        flight = conn.execute(
            "SELECT name, record_note, sync_state, server_version FROM flights WHERE server_id=303"
        ).fetchone()
        assert dict(flight) == {
            "name": f"Server Flight {suffix}",
            "record_note": "server note",
            "sync_state": "server_cache",
            "server_version": 6,
        }
        column = conn.execute(
            """SELECT display_label, unit, scale_factor FROM column_registry
               WHERE model_id=? AND data_type_key='telemetry' AND column_name='value'""",
            (model_id,),
        ).fetchone()
        assert dict(column) == {
            "display_label": "Server Value",
            "unit": "m/s",
            "scale_factor": 2.0,
        }
    finally:
        conn.close()


def test_pull_preview_hides_identical_flight_metadata(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    config = _offline_model_config()
    raw_sha = "a" * 64
    try:
        model_name = f"Same Model {suffix}"
        aircraft_name = f"Same Aircraft {suffix}"
        flight_name = f"Same Flight {suffix}"
        model_id = sync_import._create_model_from_config(
            conn, model_name, config, f"local-model-{suffix}"
        )
        aircraft_id = sync_import._create_aircraft(
            conn, model_id, aircraft_name, f"local-aircraft-{suffix}"
        )
        conn.execute(
            "UPDATE aircraft_models SET server_id=101, sync_state='synced' WHERE id=?",
            (model_id,),
        )
        conn.execute(
            "UPDATE aircraft SET server_id=202, sync_state='synced' WHERE id=?",
            (aircraft_id,),
        )
        conn.execute(
            """INSERT INTO flights
               (client_uid, server_id, source_node_id, aircraft_id, name, source_path, session_key,
                flight_date, record_location, record_note, sync_origin, sync_state,
                server_version, updated_at)
               VALUES (?, 303, 'node-local', ?, ?, 'local://same', 'A', '2026-07-19',
                       'Hong Kong', '', 'server', 'synced', 1, '2026-07-19 10:00:00')""",
            (f"local-flight-{suffix}", aircraft_id, flight_name),
        )
        flight_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """INSERT INTO flight_raw_files
               (flight_id, original_name, original_rel_path, storage_rel_path,
                sha256, size_bytes, data_type_key, sync_state)
               VALUES (?, 'a.csv', 'a.csv', 'same/a.csv', ?, 10, 'telemetry', 'synced')""",
            (flight_id, raw_sha),
        )
        manifest = {
            "source_node_id": "server",
            "server_cursor": 20,
            "models": [{
                "id": 101,
                "client_uid": f"server-model-{suffix}",
                "name": model_name,
                "version": 2,
                "config": config,
            }],
            "aircraft": [{
                "id": 202,
                "client_uid": f"server-aircraft-{suffix}",
                "model_id": 101,
                "name": aircraft_name,
                "version": 2,
            }],
            "flights": [{
                "id": 303,
                "client_uid": f"different-server-flight-{suffix}",
                "aircraft_id": 202,
                "name": flight_name,
                "session_key": "A",
                "flight_date": "2026-07-19",
                "record_location": "Hong Kong",
                "record_note": None,
                "version": 9,
                "updated_at": "2026-07-19 11:00:00",
            }],
            "raw_files": [{
                "flight_id": 303,
                "sha256": raw_sha,
                "data_type_key": "telemetry",
            }],
            "entity_redirects": [],
        }

        preview = sync_import.preview_pull_manifest(conn, manifest)
        assert preview["items"][0]["action"] == "existing"
        assert preview["summary"]["metadata_only"] == 3
        assert preview["summary"]["bundle_required"] == 0

        changed_manifest = {
            **manifest,
            "flights": [{**manifest["flights"][0], "record_note": "server changed"}],
        }
        changed_preview = sync_import.preview_pull_manifest(conn, changed_manifest)
        assert changed_preview["items"][0]["action"] == "update"

        report = sync_import.apply_pull_manifest_metadata(conn, manifest)
        flight = conn.execute(
            "SELECT record_location, record_note, server_version, updated_at FROM flights WHERE id=?",
            (flight_id,),
        ).fetchone()
        assert report["updated"]["flights"] == 0
        assert dict(flight) == {
            "record_location": "Hong Kong",
            "record_note": "",
            "server_version": 1,
            "updated_at": "2026-07-19 10:00:00",
        }
    finally:
        conn.close()


def test_pull_blocks_same_model_identity_with_different_structure(isolated_data_dir):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        model_id = sync_import._create_model_from_config(
            conn, f"Structure Model {suffix}", _offline_model_config(), f"shared-{suffix}"
        )
        conn.execute(
            "UPDATE aircraft_models SET server_id=501, sync_state='server_cache' WHERE id=?",
            (model_id,),
        )
        manifest = {
            "models": [{
                "id": 501,
                "client_uid": f"shared-{suffix}",
                "name": f"Structure Model {suffix}",
                "config": _offline_model_config("different_column"),
            }],
            "aircraft": [],
            "flights": [],
            "raw_files": [],
        }
        preview = sync_import.preview_pull_manifest(conn, manifest)
        assert preview["ok"] is False
        assert preview["models"][0]["reason"] == "model_config_mismatch"
    finally:
        conn.close()


def test_run_preview_excludes_server_rows_planned_for_upload_update():
    manifest = {
        "models": [{"id": 101}, {"id": 102}],
        "aircraft": [{"id": 201}, {"id": 202}],
        "flights": [{"id": 301}, {"id": 302}],
        "raw_files": [{"flight_id": 301}, {"flight_id": 302}],
    }
    preflight = {
        "models": [{"action": "update_metadata", "server_id": 101}],
        "aircraft": [{"action": "existing", "server_id": 201}],
        "flights": [{"action": "update_metadata", "server_id": 301}],
    }

    filtered = sync_workflow._exclude_planned_upload_changes(manifest, preflight)

    assert [row["id"] for row in filtered["models"]] == [102]
    assert [row["id"] for row in filtered["aircraft"]] == [201, 202]
    assert [row["id"] for row in filtered["flights"]] == [302]
    assert [row["flight_id"] for row in filtered["raw_files"]] == [302]


def test_run_preview_excludes_current_node_changes_after_planned_upload(monkeypatch):
    captured = {}

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(sync_workflow, "get_db", lambda: Connection())
    monkeypatch.setattr(
        sync_workflow,
        "_preview_upload",
        lambda conn, flight_ids, token: {
            "ok": True,
            "preflight": {"status": "ready", "conflicts": []},
        },
    )
    monkeypatch.setattr(
        sync_workflow.runtime_context,
        "get_local_node_id",
        lambda conn: "node-current",
    )

    def fake_preview_pull(
        conn,
        since,
        token,
        planned_upload=None,
        exclude_source_node_id=None,
    ):
        captured["exclude_source_node_id"] = exclude_source_node_id
        return {"ok": True}

    monkeypatch.setattr(sync_workflow, "_preview_pull", fake_preview_pull)

    result = sync_workflow.preview(
        "run",
        flight_ids=[1],
        since="12",
        token="token",
    )

    assert result["pull"] == {"ok": True}
    assert captured["exclude_source_node_id"] == "node-current"


def test_upload_preview_uses_lightweight_manifest_without_exporting_bundle(
    isolated_data_dir, monkeypatch
):
    from backend.database import get_db, init_db

    init_db()
    conn = get_db()
    suffix = uuid.uuid4().hex
    try:
        model_id = sync_import._create_model_from_config(
            conn, f"Preview Model {suffix}", _offline_model_config(), f"model-{suffix}"
        )
        aircraft_id = sync_import._create_aircraft(
            conn, model_id, f"Preview Aircraft {suffix}", f"aircraft-{suffix}"
        )
        conn.execute(
            """INSERT INTO flights
               (client_uid, aircraft_id, name, source_path, session_key, sync_state)
               VALUES (?, ?, ?, 'local://preview', 'A', 'pending_upload')""",
            (f"flight-{suffix}", aircraft_id, f"Preview Flight {suffix}"),
        )
        flight_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        captured = {}
        monkeypatch.setattr(
            sync_workflow.runtime_context,
            "get_server_base_url",
            lambda local_conn: "http://sync.example/api",
        )
        def fake_preflight(base_url, manifest, token=None):
            captured["manifest"] = manifest
            return {
                "status": "ready",
                "models": [],
                "aircraft": [],
                "flights": [{"source_id": flight_id, "action": "create"}],
                "conflicts": [],
                "summary": {},
            }

        monkeypatch.setattr(sync_workflow.client, "preflight", fake_preflight)
        monkeypatch.setattr(
            sync_workflow,
            "export_package",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("preview must not export a bundle")
            ),
        )

        result = sync_workflow._preview_upload(conn, [flight_id], token="token")

        assert result["ok"] is True
        assert result["bundle"]["lightweight"] is True
        assert result["bundle"]["path"] is None
        assert captured["manifest"]["preview_only"] is True
        assert captured["manifest"]["parsed_data"].get("sha256") is None
    finally:
        conn.close()


def test_large_id_queries_are_split_into_bounded_batches():
    calls = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, sql, params):
            calls.append(list(params))
            return Result([{"id": value, "name": str(value)} for value in params])

    rows = sync_repository.rows_by_ids(
        Connection(), lambda value: value, "flights", set(range(1, 1202))
    )
    assert len(rows) == 1201
    assert [len(batch) for batch in calls] == [500, 500, 201]
    assert [row["id"] for row in rows[:2]] == [1, 2]
    assert rows[-1]["id"] == 1201


def test_online_cache_cleanup_respects_age_and_suffix(tmp_path):
    old_bundle = tmp_path / "old.sqlite"
    fresh_bundle = tmp_path / "fresh.sqlite"
    unrelated = tmp_path / "old.txt"
    for path in (old_bundle, fresh_bundle, unrelated):
        path.write_bytes(b"payload")
    old_timestamp = time.time() - 10_000
    os.utime(old_bundle, (old_timestamp, old_timestamp))
    os.utime(unrelated, (old_timestamp, old_timestamp))

    result = sync_cleanup.cleanup_files(
        str(tmp_path),
        max_age_seconds=3600,
        suffixes=(".sqlite",),
    )

    assert result == {"removed_files": 1, "removed_bytes": 7}
    assert not old_bundle.exists()
    assert fresh_bundle.exists()
    assert unrelated.exists()


def test_sync_progress_derives_current_phase_percent_from_counts():
    operation_id = f"progress-{uuid.uuid4().hex}"

    sync_progress.update(
        operation_id,
        phase="导出解析数据",
        message="正在写入",
        percent=20,
        current=250,
        total=1000,
        unit="rows",
    )

    item = sync_progress.get(operation_id)
    assert item is not None
    assert item["percent"] == 20
    assert item["phase_percent"] == 25


def test_sync_progress_clears_phase_percent_for_indeterminate_new_phase():
    operation_id = f"progress-{uuid.uuid4().hex}"
    sync_progress.update(
        operation_id,
        phase="上传内容对象",
        message="正在上传",
        current=5,
        total=10,
    )

    sync_progress.update(
        operation_id,
        phase="服务器提交",
        message="等待服务器提交",
        percent=90,
    )

    item = sync_progress.get(operation_id)
    assert item is not None
    assert item["phase"] == "服务器提交"
    assert item["phase_percent"] is None


def test_upload_resume_key_ignores_volatile_manifest_fields():
    base = {
        "package_id": "pkg-a",
        "exported_at": "2026-07-18T00:00:00",
        "base_server_cursor": "1",
        "source_node_id": "node-a",
        "models": [{"id": 1, "client_uid": "model-a", "version": 2}],
        "aircraft": [],
        "flights": [],
        "raw_files": [{"sha256": "a" * 64, "size_bytes": 12}],
        "parsed_data": {"sha256": "b" * 64, "size_bytes": 34},
    }
    retry = {
        **base,
        "package_id": "pkg-b",
        "exported_at": "2026-07-18T01:00:00",
        "base_server_cursor": "9",
    }
    assert upload_sessions.manifest_resume_key(base) == upload_sessions.manifest_resume_key(retry)
    assert upload_sessions.manifest_sha256(base) != upload_sessions.manifest_sha256(retry)


def test_content_addressed_raw_object_path_is_deterministic(monkeypatch, tmp_path):
    monkeypatch.setattr(upload_sessions.db, "SERVER_DATA_DIR", str(tmp_path))
    sha = "abcdef" + "1" * 58
    assert upload_sessions.raw_object_rel_path(sha) == f"objects/ab/cd/{sha}"
    assert upload_sessions.raw_object_abs_path(sha) == os.path.join(
        str(tmp_path), "raw_files", "objects", "ab", "cd", sha
    )


def test_upload_manifest_accepts_zero_byte_objects():
    empty_sha = hashlib.sha256(b"").hexdigest()

    objects = upload_sessions._manifest_objects(
        {
            "raw_files": [{"sha256": empty_sha, "size_bytes": 0}],
            "parsed_data": {"sha256": empty_sha, "size_bytes": 0},
        }
    )

    assert objects == [
        {"kind": "raw", "sha256": empty_sha, "size_bytes": 0},
        {"kind": "parsed", "sha256": empty_sha, "size_bytes": 0},
    ]


def test_client_resumes_only_missing_upload_chunks(monkeypatch, tmp_path):
    payload_path = tmp_path / "object.bin"
    payload_path.write_bytes(b"abcdefghij")
    sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    uploaded = []

    def fake_upload(base_url, session_id, kind, object_sha, index, offset, payload, **kwargs):
        uploaded.append((index, offset, payload))
        return {"session_id": session_id, "objects": []}

    monkeypatch.setattr(sync_workflow.client, "upload_session_chunk", fake_upload)
    state = {
        "session_id": "1" * 32,
        "objects": [{
            "object_kind": "raw",
            "sha256": sha,
            "size_bytes": 10,
            "chunk_size": 4,
            "total_chunks": 3,
            "received_bytes": 4,
            "status": "uploading",
            "received_chunks": [{"chunk_index": 1}],
        }],
    }
    progress = []
    sync_workflow.client.upload_session_objects(
        "http://sync.example/api",
        state,
        [{"kind": "raw", "sha256": sha, "size_bytes": 10, "path": str(payload_path)}],
        progress_callback=lambda current, total: progress.append((current, total)),
    )
    assert uploaded == [(0, 0, b"abcd"), (2, 8, b"ij")]
    assert progress[-1] == (6, 6)


def test_client_retries_chunk_after_lost_response(monkeypatch, tmp_path):
    payload_path = tmp_path / "object.bin"
    payload_path.write_bytes(b"payload")
    sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    attempts = []

    def fake_upload(*args, **kwargs):
        attempts.append(args[6])
        if len(attempts) == 1:
            raise sync_workflow.client.SyncClientError("response was lost")
        return {"session_id": args[1], "objects": []}

    monkeypatch.setattr(sync_workflow.client, "upload_session_chunk", fake_upload)
    monkeypatch.setattr(sync_workflow.client.time, "sleep", lambda _seconds: None)
    state = {
        "session_id": "2" * 32,
        "objects": [{
            "object_kind": "parsed",
            "sha256": sha,
            "size_bytes": 7,
            "chunk_size": 8,
            "total_chunks": 1,
            "received_bytes": 0,
            "status": "pending",
            "received_chunks": [],
        }],
    }

    sync_workflow.client.upload_session_objects(
        "http://sync.example/api",
        state,
        [{"kind": "parsed", "sha256": sha, "size_bytes": 7, "path": str(payload_path)}],
    )

    assert attempts == [b"payload", b"payload"]


def test_client_does_not_retry_conflicting_chunk(monkeypatch, tmp_path):
    payload_path = tmp_path / "object.bin"
    payload_path.write_bytes(b"payload")
    sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    attempts = 0

    def fake_upload(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise sync_workflow.client.SyncClientError("conflict", status_code=409)

    monkeypatch.setattr(sync_workflow.client, "upload_session_chunk", fake_upload)
    state = {
        "session_id": "3" * 32,
        "objects": [{
            "object_kind": "raw",
            "sha256": sha,
            "size_bytes": 7,
            "chunk_size": 8,
            "total_chunks": 1,
            "received_bytes": 0,
            "status": "pending",
            "received_chunks": [],
        }],
    }

    with pytest.raises(sync_workflow.client.SyncClientError, match="conflict"):
        sync_workflow.client.upload_session_objects(
            "http://sync.example/api",
            state,
            [{"kind": "raw", "sha256": sha, "size_bytes": 7, "path": str(payload_path)}],
        )

    assert attempts == 1


def test_client_uploads_zero_byte_object(monkeypatch, tmp_path):
    payload_path = tmp_path / "empty.sqlite"
    payload_path.write_bytes(b"")
    sha = hashlib.sha256(b"").hexdigest()
    uploaded = []

    def fake_upload(*args, **kwargs):
        uploaded.append((args[4], args[5], args[6]))
        return {"session_id": args[1], "objects": []}

    monkeypatch.setattr(sync_workflow.client, "upload_session_chunk", fake_upload)
    state = {
        "session_id": "4" * 32,
        "objects": [{
            "object_kind": "parsed",
            "sha256": sha,
            "size_bytes": 0,
            "chunk_size": upload_sessions.CHUNK_SIZE,
            "total_chunks": 1,
            "received_bytes": 0,
            "status": "pending",
            "received_chunks": [],
        }],
    }

    sync_workflow.client.upload_session_objects(
        "http://sync.example/api",
        state,
        [{"kind": "parsed", "sha256": sha, "size_bytes": 0, "path": str(payload_path)}],
    )

    assert uploaded == [(0, 0, b"")]


def test_session_import_failure_rolls_back_before_marking_failed(monkeypatch):
    events = []

    class FakeConnection:
        def rollback(self):
            events.append("rollback")

        def commit(self):
            events.append("commit")

    monkeypatch.setattr(
        server_sync_router.upload_sessions,
        "describe_session",
        lambda *args, **kwargs: {"operation_id": "operation-a"},
    )

    def fail_import(*args, **kwargs):
        events.append("import")
        raise ValueError("import failed")

    monkeypatch.setattr(server_sync_router.server_sync, "import_push_session", fail_import)
    monkeypatch.setattr(
        server_sync_router,
        "_persist_upload_session_failure",
        lambda *args, **kwargs: events.append("persist_failed"),
    )
    monkeypatch.setattr(server_sync_router.server_operations, "update", lambda *args, **kwargs: None)
    monkeypatch.setattr(server_sync_router.server_operations, "finish", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as raised:
        server_sync_router.commit_sync_session(
            "5" * 32,
            user={"id": 1, "role": "admin"},
            conn=FakeConnection(),
        )

    assert raised.value.status_code == 409
    assert events == ["import", "rollback", "persist_failed"]


def test_server_schema_has_resumable_upload_and_raw_object_tables():
    ddl = "\n".join(server_database.SCHEMA_DDL)
    assert "CREATE TABLE IF NOT EXISTS raw_objects" in ddl
    assert "CREATE TABLE IF NOT EXISTS sync_upload_sessions" in ddl
    assert "CREATE TABLE IF NOT EXISTS sync_upload_objects" in ddl
    assert "CREATE TABLE IF NOT EXISTS sync_upload_chunks" in ddl


def test_mysql_dynamic_insert_batch_scales_down_for_wide_tables():
    assert server_sync._dynamic_insert_batch_size(3) == 1000
    assert server_sync._dynamic_insert_batch_size(11) == 909
    assert server_sync._dynamic_insert_batch_size(95) == 105
    assert server_sync._dynamic_insert_batch_size(200) == 100
