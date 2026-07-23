"""Desktop API request and response schemas.

Schemas are migrated here incrementally while router modules are extracted.
"""

from typing import Literal

from pydantic import BaseModel, Field


class RuntimeConfigUpdate(BaseModel):
    data_dir: str | None = None
    server_base_url: str | None = None


class ServerLoginRequest(BaseModel):
    username: str
    password: str


class AppContextUpdate(BaseModel):
    environment: str | None = None
    node_id: str | None = None


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


class SyncExportRequest(BaseModel):
    flight_ids: list[int]


class SyncPushBatchRequest(BaseModel):
    flight_ids: list[int] | None = None
    server_token: str | None = None
    operation_id: str | None = None
    progress_start: float = 0
    progress_end: float = 100
    progress_finalize: bool = True


class SyncPullRequest(BaseModel):
    since: str | None = None
    server_token: str | None = None
    operation_id: str | None = None
    progress_start: float = 0
    progress_end: float = 100
    progress_finalize: bool = True
    package_path: str | None = None
    conflict_resolutions: dict[str, str] | None = None
    exclude_source_node_id: str | None = None


class SyncRunRequest(BaseModel):
    flight_ids: list[int] | None = None
    since: str | None = None
    server_token: str | None = None
    operation_id: str | None = None
    pull_package_path: str | None = None
    pull_conflict_resolutions: dict[str, str] | None = None


class SyncPreviewRequest(BaseModel):
    mode: str = "run"
    flight_ids: list[int] | None = None
    since: str | None = None


class SyncAbandonRequest(BaseModel):
    flight_ids: list[int]


class DeleteEntityRequest(BaseModel):
    scope: str = "auto"
    reason: str | None = None
    server_token: str | None = None


class SyncImportPreviewRequest(BaseModel):
    package_path: str


class SyncModelAction(BaseModel):
    source_model_id: int
    action: str
    target_model_id: int | None = None
    name: str | None = None


class SyncAircraftMapping(BaseModel):
    source_aircraft_id: int
    action: str
    target_aircraft_id: int | None = None
    name: str | None = None


class SyncImportRequest(BaseModel):
    package_path: str
    model_actions: list[SyncModelAction] = []
    aircraft_mappings: list[SyncAircraftMapping] = []
    metadata_strategy: str | None = None
    conflict_policy: str | None = None


class ImportRequest(BaseModel):
    source_path: str


class ImportSessionRequest(BaseModel):
    source_path: str
    aircraft_id: int
    session_key: str = ""
    flight_date: str | None = None
    record_total_duration_min: float | None = None
    record_location: str | None = ""
    record_payload: str | None = ""
    record_weather: str | None = ""
    record_fuel_amount: float | None = None
    record_takeoff_weight: float | None = None
    record_altitude: float | None = None
    record_wind_speed: float | None = None
    record_wind_direction: str | None = ""
    record_temperature: float | None = None
    record_note: str | None = ""


class UpdateFlightRequest(BaseModel):
    name: str


class FlightRecordRequest(BaseModel):
    record_total_duration_min: float | None = None
    record_location: str | None = None
    record_payload: str | None = None
    record_weather: str | None = None
    record_fuel_amount: float | None = None
    record_takeoff_weight: float | None = None
    record_altitude: float | None = None
    record_wind_speed: float | None = None
    record_wind_direction: str | None = None
    record_temperature: float | None = None
    record_note: str | None = None


class AlignedRequest(BaseModel):
    column_keys: list[str]
    filter: dict | None = None


class DataFilterCondition(BaseModel):
    column: str
    op: Literal["gt", "gte", "lt", "lte", "eq", "between"]
    value: float | None = None
    min_val: float | None = None
    max_val: float | None = None


class DataFilterSpec(BaseModel):
    logic: Literal["and", "or"] = "and"
    conditions: list[DataFilterCondition] = Field(min_length=1, max_length=20)


class FlightDataMatchesRequest(BaseModel):
    model_id: int
    flight_ids: list[int] = Field(max_length=5000)
    filter: DataFilterSpec


class CorrelationRequest(BaseModel):
    column_keys: list[str]


class AnomalyRequest(BaseModel):
    column_key: str
    window_size: int = 30
    sigma: float = 3.0


class CompareRequest(BaseModel):
    flight_ids: list[int]
    column_key: str


class RemoteFlightSearchRequest(BaseModel):
    model_id: int
    aircraft_search: str = ""
    time_from: str | None = None
    time_to: str | None = None
    record_filter: dict | None = None
    data_filter: dict | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class RemoteFlightDownloadRequest(BaseModel):
    model_id: int
    flight_ids: list[int] = Field(min_length=1, max_length=100)
    operation_id: str | None = None


class PresetCreate(BaseModel):
    model_id: int
    name: str
    columns: list[str]


class FilterPresetCreate(BaseModel):
    model_id: int
    name: str
    config: dict


class CreateModelRequest(BaseModel):
    name: str


class CreateModelFromScanRequest(BaseModel):
    name: str
    source_path: str
    selected_data_types: list[str] | None = None


class UpdateModelRequest(BaseModel):
    name: str


class ImportModelRequest(BaseModel):
    name: str
    data: dict


class UpdateColumnRequest(BaseModel):
    display_label: str | None = None
    unit: str | None = None
    scale_factor: float | None = None


class UpdateDataTypeLabelRequest(BaseModel):
    display_label: str


class CreateAircraftRequest(BaseModel):
    name: str


class UpdateAircraftRequest(BaseModel):
    name: str
