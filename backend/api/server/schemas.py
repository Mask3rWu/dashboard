"""Collaboration-server request schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    username: str


class ServerColumn(BaseModel):
    name: str
    label: str | None = None
    display_label: str | None = None
    unit: str = ""
    type: str | None = None
    data_type: str | None = None
    ordinal: int | None = None
    scale_factor: float = 1.0


class ServerDataType(BaseModel):
    display_label: str | None = None
    file_patterns: list[str] = Field(default_factory=list)
    is_alert: bool = False
    columns: list[ServerColumn] = Field(default_factory=list)


class CreateModelRequest(BaseModel):
    name: str
    client_uid: str | None = None
    source_node_id: str | None = None
    has_header: bool = True
    has_uav_send_id: bool = False
    extract_serial_from_path: bool = False
    data_types: dict[str, ServerDataType] = Field(default_factory=dict)


class DeleteRequest(BaseModel):
    reason: str | None = None


class MergeEntitiesRequest(BaseModel):
    entity_type: str
    source_id: int
    target_id: int


class CreateUploadSessionRequest(BaseModel):
    manifest: dict[str, Any]
    operation_id: str | None = None


class FlightRecordFilterCondition(BaseModel):
    field: str
    op: Literal["contains", "gt", "gte", "lt", "lte", "eq", "between"]
    value: str | None = None
    min_val: float | None = None
    max_val: float | None = None


class FlightRecordFilterSpec(BaseModel):
    logic: Literal["and", "or"] = "and"
    conditions: list[FlightRecordFilterCondition] = Field(default_factory=list, max_length=20)


class FlightDataFilterCondition(BaseModel):
    column: str
    op: Literal["gt", "gte", "lt", "lte", "eq", "between"]
    value: float | None = None
    min_val: float | None = None
    max_val: float | None = None


class FlightDataFilterSpec(BaseModel):
    logic: Literal["and", "or"] = "and"
    conditions: list[FlightDataFilterCondition] = Field(min_length=1, max_length=20)


class ServerFlightSearchRequest(BaseModel):
    model_id: int
    aircraft_search: str = ""
    time_from: str | None = None
    time_to: str | None = None
    record_filter: FlightRecordFilterSpec | None = None
    data_filter: FlightDataFilterSpec | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
