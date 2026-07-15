"""Shared desktop API request helpers."""

from datetime import datetime

from fastapi import HTTPException, Request

from backend import runtime_context
from backend.permissions import require_capability
from backend.sync import client as sync_client


RECORD_TEXT_FIELDS = {
    "record_location",
    "record_payload",
    "record_weather",
    "record_wind_direction",
    "record_note",
}
RECORD_NUMERIC_FIELDS = {
    "record_total_duration_min",
    "record_fuel_amount",
    "record_takeoff_weight",
    "record_altitude",
    "record_wind_speed",
    "record_temperature",
}
RECORD_FIELDS = RECORD_TEXT_FIELDS | RECORD_NUMERIC_FIELDS


def model_dump(value, *, exclude_unset: bool = False) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_unset=exclude_unset)
    return value.dict(exclude_unset=exclude_unset)


def server_token(value, request: Request) -> str | None:
    body_token = getattr(value, "server_token", None)
    if body_token:
        return body_token
    token = request.headers.get("x-server-token")
    if token:
        return token.strip()
    authorization = request.headers.get("authorization")
    if authorization:
        return authorization.strip()
    return None


def server_header_token(request: Request) -> str | None:
    token = request.headers.get("x-server-token")
    return token.strip() if token else None


def require_capability_or_server(conn, request: Request, capability: str) -> None:
    try:
        require_capability(conn, request, capability)
        return
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        local_denied = exc
    token = server_header_token(request)
    if token:
        server_base_url = runtime_context.get_server_base_url(conn)
        if server_base_url:
            try:
                payload = sync_client.auth_me(server_base_url, token=token, timeout=2)
                capabilities = payload.get("capabilities")
                if isinstance(capabilities, list) and capability in capabilities:
                    return
            except sync_client.SyncClientError:
                pass
    raise local_denied


def require_local_delete_capability(conn, request: Request, entity_type: str) -> None:
    capability = {
        "model": "delete_models",
        "aircraft": "delete_aircraft",
        "flight": "delete_flights",
    }[entity_type]
    require_capability_or_server(conn, request, capability)


def normalize_record_fields(data: dict, *, include_unset: bool = True) -> dict:
    keys = RECORD_FIELDS if include_unset else {key for key in RECORD_FIELDS if key in data}
    normalized = {}
    for key in keys:
        value = data.get(key)
        if key in RECORD_TEXT_FIELDS:
            normalized[key] = (value or "").strip() if isinstance(value, str) else ""
        else:
            normalized[key] = value
    return normalized


def normalize_flight_date(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, "飞行日期格式必须为 YYYY-MM-DD") from exc
