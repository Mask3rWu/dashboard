from __future__ import annotations

import server_app

from backend.api.server import schemas
from tests.contract_helpers import api_route_contract, assert_schema, load_contract


def test_server_routes_match_reviewed_contract():
    assert api_route_contract(server_app.app) == load_contract("server_routes.json")


def test_server_key_request_schemas_are_stable():
    assert_schema(schemas.LoginRequest, ["username", "password"])
    assert_schema(
        schemas.CreateModelRequest,
        [
            "name",
            "client_uid",
            "source_node_id",
            "has_header",
            "has_uav_send_id",
            "extract_serial_from_path",
            "data_types",
        ],
        {
            "client_uid": None,
            "source_node_id": None,
            "has_header": True,
            "has_uav_send_id": False,
            "extract_serial_from_path": False,
        },
    )
    assert_schema(schemas.DeleteRequest, ["reason"], {"reason": None})
