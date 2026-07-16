from __future__ import annotations

import hashlib
import json
import uuid
import zipfile

import pytest

from backend.sync import local_import as sync_import
from backend.sync import package as sync_package
from backend.sync import server as server_sync
from backend.sync import workflow as sync_workflow


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
    def fake_find_flight_by_client_uid(conn, client_uid):
        if client_uid == flight_uid:
            return {
                "id": 9001,
                "client_uid": flight_uid,
                "version": 1,
                "deleted_at": None,
                "name": base_flight["name"],
                "record_location": base_flight["record_location"],
                "record_note": base_flight["record_note"],
            }
        return None

    monkeypatch.setattr(server_sync, "_find_flight_by_client_uid", fake_find_flight_by_client_uid)
    monkeypatch.setattr(server_sync, "_find_flight_by_business", lambda *a, **k: None)
    monkeypatch.setattr(server_sync, "_server_flight_raw_hashes", lambda conn, flight_id: set())
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
