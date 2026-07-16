from __future__ import annotations

import main

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
