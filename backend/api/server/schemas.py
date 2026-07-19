"""Collaboration-server request schemas."""

from typing import Any

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
