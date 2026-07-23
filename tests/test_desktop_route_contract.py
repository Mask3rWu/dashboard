from __future__ import annotations

import mimetypes

from fastapi.testclient import TestClient

import main

from backend.api.desktop.app import create_app
from backend.api.desktop import schemas
from tests.contract_helpers import api_route_contract, assert_schema, load_contract


def test_desktop_routes_match_reviewed_contract():
    assert api_route_contract(main.app) == load_contract("desktop_routes.json")


def test_desktop_key_request_schemas_are_stable():
    assert_schema(schemas.LoginRequest, ["username", "password"])
    assert_schema(schemas.CreateModelRequest, ["name"])
    assert_schema(
        schemas.ImportSessionRequest,
        [
            "source_path",
            "aircraft_id",
            "session_key",
            "flight_date",
            "record_total_duration_min",
            "record_location",
            "record_payload",
            "record_weather",
            "record_fuel_amount",
            "record_takeoff_weight",
            "record_altitude",
            "record_wind_speed",
            "record_wind_direction",
            "record_temperature",
            "record_note",
        ],
        {"session_key": "", "flight_date": None},
    )
    assert_schema(schemas.AlignedRequest, ["column_keys", "filter"], {"filter": None})
    assert_schema(schemas.FlightDataMatchesRequest, ["model_id", "flight_ids", "filter"])
    assert_schema(
        schemas.SyncPushBatchRequest,
        [
            "flight_ids",
            "server_token",
            "operation_id",
            "progress_start",
            "progress_end",
            "progress_finalize",
        ],
        {
            "flight_ids": None,
            "server_token": None,
            "operation_id": None,
            "progress_start": 0,
            "progress_end": 100,
            "progress_finalize": True,
        },
    )
    assert_schema(
        schemas.SyncPullRequest,
        [
            "since",
            "server_token",
            "operation_id",
            "progress_start",
            "progress_end",
            "progress_finalize",
            "package_path",
            "conflict_resolutions",
            "exclude_source_node_id",
        ],
        {"since": None, "package_path": None, "conflict_resolutions": None},
    )


def test_frontend_modules_ignore_windows_js_file_associations(tmp_path, monkeypatch):
    """Managed Windows images can incorrectly map JavaScript to text/plain."""
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export {};", encoding="utf-8")

    response = TestClient(create_app(str(tmp_path))).get("/assets/app.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
