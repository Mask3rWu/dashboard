from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from backend.sync import local_import as sync_import
from backend.sync import package as sync_package
from backend.sync import server as server_sync


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
