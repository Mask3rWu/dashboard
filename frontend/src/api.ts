const BASE = '/api';
const TOKEN_KEY = 'flight_analyzer_session_token';
const SERVER_TOKEN_KEY = 'flight_analyzer_server_token';

export function getSessionToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function setSessionToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getServerToken(): string {
  return localStorage.getItem(SERVER_TOKEN_KEY) || '';
}

export function setServerToken(token: string | null) {
  if (token) localStorage.setItem(SERVER_TOKEN_KEY, token);
  else localStorage.removeItem(SERVER_TOKEN_KEY);
}

function parseErrorBody(text: string, fallback: string): string {
  if (!text) return fallback;
  try {
    const body = JSON.parse(text);
    const rawDetail = body.detail || body.error || fallback;
    const detail = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail);
    const errorType = body.error_type ? ` (${body.error_type})` : '';
    return `${detail}${errorType}`;
  } catch {
    return text || fallback;
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let res: Response;
  const token = getSessionToken();
  const serverToken = getServerToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(serverToken ? { 'x-server-token': serverToken } : {}),
    ...(options?.headers ?? {}),
  };
  try {
    res = await fetch(`${BASE}${url}`, {
      ...options,
      headers,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(`无法连接到后端服务，请确认应用已正常启动。网络错误：${msg}`);
  }

  if (!res.ok) {
    const text = await res.text();
    const detail = parseErrorBody(text, res.statusText);
    throw new Error(`${detail || '请求失败'} (HTTP ${res.status})`);
  }
  return res.json();
}

export interface HealthStatus {
  status: string;
  version: string;
  data_dir: string;
  db_path: string;
  db_exists: boolean;
  frontend_dir_exists: boolean;
}

export type Capability =
  | 'manage_users'
  | 'change_own_password'
  | 'delete_models'
  | 'delete_aircraft'
  | 'delete_flights'
  | 'update_columns';

export interface CurrentUser {
  id: number;
  username: string;
  role: 'admin' | 'user';
  created_at?: string;
  password_changed_at?: string | null;
  disabled_at?: string | null;
}

export interface AppContext {
  environment: 'research' | 'field';
  node_id: string;
  user: CurrentUser | null;
  capabilities: Capability[];
}

export interface RuntimeContext {
  data_dir: string;
  server_base_url: string;
  server_reachable: boolean;
  server_status: 'online' | 'offline' | 'not_configured' | string;
  local_node_id: string;
  last_server_check_at: string;
  server_user: CurrentUser | null;
  server_capabilities: string[];
  sync_summary: {
    pending_upload: number;
    upload_failed: number;
    conflict: number;
    last_push_at?: string | null;
    last_pull_at?: string | null;
  };
}

export interface ServerAuthPayload {
  user: CurrentUser | null;
  capabilities: string[];
  token?: string;
}

export interface LoginPayload extends AppContext {
  token: string;
  server_token?: string;
  login_mode?: 'online' | 'offline';
}

export interface SyncQueueSummary {
  pending_upload: number;
  dirty: number;
  upload_failed: number;
  conflict: number;
  uploadable: number;
}

export interface SyncQueueItem {
  id: number;
  client_uid?: string | null;
  server_id?: number | null;
  source_node_id?: string | null;
  sync_origin?: string | null;
  sync_state: string;
  server_version?: number | null;
  last_sync_at?: string | null;
  sync_error_json?: string | null;
  sync_error?: unknown;
  name: string;
  session_key?: string | null;
  flight_date?: string | null;
  start_time?: string | null;
  duration_sec?: number | null;
  total_rows?: number | null;
  import_time?: string | null;
  updated_at?: string | null;
  record_location?: string | null;
  record_weather?: string | null;
  record_payload?: string | null;
  aircraft_id: number;
  aircraft_name: string;
  model_id: number;
  model_name: string;
  raw_file_count: number;
}

export interface SyncQueueResponse {
  summary: SyncQueueSummary;
  items: SyncQueueItem[];
  base_items?: SyncPreviewItem[];
}

export interface SyncOperationRequest {
  flight_ids?: number[] | null;
  since?: string | null;
  server_token?: string | null;
  operation_id?: string | null;
  package_path?: string | null;
  conflict_resolutions?: Record<string, string> | null;
  pull_package_path?: string | null;
  pull_conflict_resolutions?: Record<string, string> | null;
}

export interface SyncOperationResult {
  ok: boolean;
  status: string;
  run_id?: number;
  push?: SyncOperationResult;
  pull?: SyncOperationResult;
  steps?: { name: string; status: string; detail?: string }[];
  selected_flights?: { id: number; name: string; sync_state: string }[];
  skipped_dirty?: { id: number; name: string; sync_state: string }[];
  summary?: SyncQueueSummary;
  bundle?: unknown;
  preflight?: unknown;
  server_report?: unknown;
  writeback?: unknown;
  report?: unknown;
  abandoned?: number;
}

export interface SyncProgress {
  operation_id: string;
  status: 'running' | 'completed' | 'failed' | string;
  phase: string;
  message: string;
  detail?: string | null;
  percent: number;
  current?: number | null;
  total?: number | null;
  created_at: string;
  updated_at: string;
}

export interface SyncPreviewItem {
  entity_type?: 'model' | 'aircraft' | 'flight' | string;
  id?: number;
  server_id?: number | null;
  server_name?: string | null;
  server_version?: number | null;
  name: string;
  model_name?: string | null;
  aircraft_name?: string | null;
  sync_state?: string;
  action: string;
  reason?: string | null;
  matched_by?: string | null;
  transfer_kind?: 'metadata' | 'bundle' | string | null;
  session_key?: string | null;
  flight_date?: string | null;
  updated_at?: string | null;
  deleted_at?: string | null;
  local?: {
    id: number;
    name: string;
    sync_state: string;
    updated_at?: string | null;
    server_id?: number | null;
  } | null;
}

export interface SyncPreviewResult {
  mode: 'run' | 'push' | 'pull' | string;
  upload?: {
    ok: boolean;
    status: string;
    items: SyncPreviewItem[];
    models?: SyncPreviewItem[];
    aircraft?: SyncPreviewItem[];
    skipped_dirty?: SyncPreviewItem[];
    summary?: Record<string, number>;
    preflight?: unknown;
  } | null;
  pull?: {
    ok: boolean;
    package_path?: string | null;
    server_cursor?: string | number | null;
    items: SyncPreviewItem[];
    models?: SyncPreviewItem[];
    aircraft?: SyncPreviewItem[];
    conflicts: SyncPreviewItem[];
    summary?: Record<string, number>;
    warnings?: unknown[];
  } | null;
}

export interface AircraftModel {
  id: number;
  name: string;
  created_at: string;
  client_uid?: string | null;
  server_id?: number | null;
  sync_origin?: string | null;
  sync_state?: Flight['sync_state'];
  server_version?: number | null;
  server_deleted_at?: string | null;
  aircraft_count?: number;
  total_flights?: number;
  total_flight_hours?: number;
}

export interface Aircraft {
  id: number;
  model_id: number;
  name: string;
  created_at: string;
  client_uid?: string | null;
  server_id?: number | null;
  sync_origin?: string | null;
  sync_state?: Flight['sync_state'];
  server_version?: number | null;
  server_deleted_at?: string | null;
  flight_count?: number;
}

export interface FlightRecordFields {
  record_total_duration_min?: number | null;
  record_location?: string;
  record_payload?: string;
  record_weather?: string;
  record_fuel_amount?: number | null;
  record_takeoff_weight?: number | null;
  record_altitude?: number | null;
  record_wind_speed?: number | null;
  record_wind_direction?: string;
  record_temperature?: number | null;
  record_note?: string;
}

export interface Flight extends FlightRecordFields {
  id: number;
  client_uid?: string;
  server_id?: number | null;
  source_node_id?: string | null;
  sync_origin?: 'local' | 'server' | 'package' | string;
  sync_state?:
    | 'local_only'
    | 'pending_upload'
    | 'syncing'
    | 'synced'
    | 'dirty'
    | 'upload_failed'
    | 'conflict'
    | 'server_cache'
    | 'server_deleted'
    | string;
  server_version?: number | null;
  last_sync_at?: string | null;
  sync_error_json?: string | null;
  name: string;
  aircraft_id: number;
  aircraft_name: string;
  model_id: number;
  model_name: string;
  source_path: string;
  session_key: string;
  flight_date: string;
  start_time: string;
  end_time: string;
  duration_sec: number;
  import_time: string;
  raw_file_count?: number;
  raw_import_warnings?: string;
  raw_warnings?: { file?: string; path?: string; error: string }[];
  // Legacy fields (present in migrated data)
  drone_id?: string;
  drone_model?: string;
}

export interface RawFileItem {
  id: number;
  flight_id: number;
  original_name: string;
  original_rel_path: string;
  data_type_key?: string | null;
  source_mtime?: number | null;
  created_at: string;
  sha256: string;
  size_bytes: number;
  storage_rel_path: string;
}

export interface RawFolderOpenResult {
  flight_id: number;
  file_count: number;
  path: string;
  warnings: { file?: string; storage_rel_path?: string; error: string; detail?: string }[];
}

export interface SyncExportFlightNode {
  id: number;
  name: string;
  session_key?: string;
  flight_date?: string | null;
  start_time?: string | null;
  duration_sec?: number | null;
  record_location?: string;
  record_weather?: string;
}

export interface SyncExportAircraftNode {
  id: number;
  name: string;
  flights: SyncExportFlightNode[];
}

export interface SyncExportModelNode {
  id: number;
  name: string;
  aircraft: SyncExportAircraftNode[];
}

export interface SyncExportResult {
  ok: boolean;
  path: string;
  filename: string;
  flight_count: number;
  raw_file_count: number;
  parsed_sha256: string;
}

export interface SyncImportModelPlan {
  source_model_id: number;
  source_name: string;
  matched_model?: { id: number; name: string } | null;
  requires_confirmation: boolean;
  default_action: 'use_existing' | 'create';
  create_name: string;
}

export interface SyncImportAircraftPlan {
  source_aircraft_id: number;
  source_model_id: number;
  source_name: string;
  target_model_id?: number | null;
  matched_aircraft?: { id: number; name: string } | null;
  existing_aircraft: { id: number; name: string }[];
  requires_mapping: boolean;
  default_action: 'use_existing' | 'create';
  create_name: string;
}

export interface SyncImportPreview {
  package_path: string;
  summary: {
    source_node_id?: string;
    source_environment?: string;
    exported_at?: string;
    flight_count: number;
    aircraft_count: number;
    model_count: number;
    date_from?: string | null;
    date_to?: string | null;
    package_version: number;
    schema_version: number;
    compatible: boolean;
    import_path: 'parsed_sqlite' | 'raw_reparse_required';
  };
  model_plans: SyncImportModelPlan[];
  aircraft_plans: SyncImportAircraftPlan[];
  duplicates: {
    source_flight_id: number;
    source_name?: string;
    target_flight_id: number;
    target_name: string;
    target_aircraft_id: number;
  }[];
  warnings: string[];
}

export interface SyncImportRequest {
  package_path: string;
  model_actions: {
    source_model_id: number;
    action: 'use_existing' | 'create';
    target_model_id?: number | null;
    name?: string | null;
  }[];
  aircraft_mappings: {
    source_aircraft_id: number;
    action: 'use_existing' | 'create';
    target_aircraft_id?: number | null;
    name?: string | null;
  }[];
  conflict_policy: 'skip' | 'update_records';
}

export interface SyncImportReport {
  id: number;
  status: 'success' | 'partial' | 'failed' | string;
  imported_flights: unknown[];
  skipped_flights: unknown[];
  updated_flights: unknown[];
  created_models: unknown[];
  created_aircraft: unknown[];
  warnings: unknown[];
  failures: unknown[];
  raw_files?: { attached: number; warnings: number };
  parsed_rows?: number;
}

export interface SessionPreview {
  aircraft_serial: string;
  session_key: string;
  flight_date?: string | null;
  data_types: Record<string, number>;
  file_count: number;
  record_defaults?: FlightRecordFields;
  record_source?: string;
  record_defaults_error?: string;
  import_status: 'new' | 'imported';
  existing_flight_id?: number;
  existing_flight_name?: string;
  aircraft_id?: number;
  conflicting_aircraft?: { aircraft_serial: string; flight_id: number; flight_name: string }[];
}

export interface DiscoveredType {
  data_type_key: string;
  display_label: string;
  is_alert: boolean;
  is_raw: boolean;
  column_count: number;
}

export interface ScanResult {
  source_path: string;
  folder_name: string;
  format_detected?: boolean;
  model: {
    id: number;
    name: string;
    is_new: boolean;
    match_confidence: number | null;
  } | null;
  suggested_model_id?: number;
  suggested_model_name?: string;
  // Available both for unmatched folders and when the user overrides a
  // recommended high-similarity model to create a new one.
  suggested_name?: string;
  discovered_types?: DiscoveredType[];
  matching_models?: { id: number; name: string; score: number }[];
  sessions: SessionPreview[];
  error?: string;
}

export interface ImportSessionResult {
  flight_id: number;
  aircraft_id: number;
  session_key: string;
  name: string;
  rows: number;
  details: Record<string, number | string>;
  raw_files?: number;
  raw_warnings?: { file?: string; path?: string; error: string }[];
  error?: string;
}

export interface ColumnGroup {
  data_type_key?: string;
  table: string;
  label: string;
  row_count?: number;
  duration_sec?: number;
  columns: ColumnItem[];
}

export interface ColumnItem {
  key: string;
  label: string;
  unit: string;
  scale_factor: number;
}

export interface AlignedData {
  times: string[];
  ref_secs: number[];
  series: Record<string, {
    label: string;
    unit: string;
    scale_factor: number;
    is_numeric: boolean;
    table: string;
    values: (number | null)[];
    text_values?: (string | null)[];
  }>;
  mask?: boolean[];
  segments?: { start: number; end: number }[];
}

export interface FilterCondition {
  column: string;
  op: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'between';
  value: number | null;
  min_val: number | null;
  max_val: number | null;
}

export interface FilterSpec {
  logic: 'and' | 'or';
  conditions: FilterCondition[];
}

export interface FilterPreset {
  id: number;
  model_id: number;
  name: string;
  config: FilterSpec;
}

export interface FlightStats {
  duration_sec: number;
  start_time: string;
  end_time: string;
  drone_id: string;
  name: string;
}

export interface Preset {
  id: number;
  model_id: number;
  name: string;
  columns: string[];
}

export interface ColumnDetail {
  column_name: string;
  display_label: string;
  unit: string;
  scale_factor: number;
  data_type: string;
  ordinal: number;
}

export interface DataTypeGroup {
  data_type_key: string;
  table: string;
  label: string;
  columns: ColumnDetail[];
}

// Health
export const checkHealth = () => request<HealthStatus>('/health');

// App context / auth
export const getAppContext = () => request<AppContext>('/app/context');
export const updateAppContext = (updates: { environment?: 'research' | 'field'; node_id?: string }) =>
  request<AppContext>('/app/context', { method: 'PATCH', body: JSON.stringify(updates) });
export const getRuntimeContext = () => request<RuntimeContext>('/runtime/context');
export const updateRuntimeConfig = (updates: { data_dir?: string; server_base_url?: string }) =>
  request<RuntimeContext>('/runtime/config', { method: 'PATCH', body: JSON.stringify(updates) });
export const serverLogin = (username: string, password: string) =>
  request<ServerAuthPayload>('/server-auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
export const serverLogout = () => request<{ ok: boolean }>('/server-auth/logout', { method: 'POST' });
export const login = (username: string, password: string) =>
  request<LoginPayload>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
export const logout = () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' });
export const changePassword = (oldPassword: string, newPassword: string) =>
  request<{ ok: boolean }>('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });

// Server user management
export const listUsers = () => request<{ users: CurrentUser[] }>('/users');
export const createUser = (username: string, password: string, role: CurrentUser['role']) =>
  request<CurrentUser>('/users', {
    method: 'POST',
    body: JSON.stringify({ username, password, role }),
  });
export const updateUser = (id: number, username: string) =>
  request<CurrentUser>(`/users/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ username }),
  });
export const resetUserPassword = (id: number) =>
  request<CurrentUser>(`/users/${id}/reset-password`, { method: 'POST' });
export const deleteUser = (id: number) =>
  request<{ ok: boolean }>(`/users/${id}`, { method: 'DELETE' });

// Models
export const listModels = () => request<{ models: AircraftModel[] }>('/models');
export const createModel = (name: string) =>
  request<AircraftModel>('/models', { method: 'POST', body: JSON.stringify({ name }) });
export const createModelFromScan = (
  name: string,
  sourcePath: string,
  selectedDataTypes?: string[],
) =>
  request<AircraftModel>('/models/from-scan', {
    method: 'POST',
    body: JSON.stringify({
      name,
      source_path: sourcePath,
      selected_data_types: selectedDataTypes ?? null,
    }),
  });
export const updateModel = (id: number, name: string) =>
  request('/models/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export type DeleteScope = 'auto' | 'local_cache' | 'local_unsynced' | 'server';
const deleteBody = (scope: DeleteScope = 'auto') => ({ scope });
export const deleteModel = (id: number, scope: DeleteScope = 'auto') =>
  request('/models/' + id, { method: 'DELETE', body: JSON.stringify(deleteBody(scope)) });

export const exportModel = (modelId: number) =>
  request<{ ok: boolean; path: string; filename: string }>(`/models/${modelId}/export`);

export const importModel = (name: string, data: object) =>
  request<AircraftModel>('/models/import', {
    method: 'POST',
    body: JSON.stringify({ name, data }),
  });

// Model Columns
export const getModelColumns = (modelId: number) =>
  request<{ data_types: DataTypeGroup[] }>(`/models/${modelId}/columns`);

export const updateModelColumn = (
  modelId: number,
  dataTypeKey: string,
  columnName: string,
  updates: { display_label?: string; unit?: string; scale_factor?: number }
) =>
  request<{ column_name: string; display_label: string; unit: string; scale_factor: number }>(
    `/models/${modelId}/columns?data_type_key=${encodeURIComponent(dataTypeKey)}&column_name=${encodeURIComponent(columnName)}`,
    { method: 'PATCH', body: JSON.stringify(updates) }
  );

export const updateModelDataTypeLabel = (
  modelId: number,
  dataTypeKey: string,
  displayLabel: string
) =>
  request<{ ok: boolean; data_type_key: string; display_label: string }>(
    `/models/${modelId}/data-types/${encodeURIComponent(dataTypeKey)}`,
    { method: 'PATCH', body: JSON.stringify({ display_label: displayLabel }) }
  );

// Aircraft
export const listAircraft = (modelId: number) =>
  request<{ aircraft: Aircraft[] }>(`/models/${modelId}/aircraft`);
export const createAircraft = (modelId: number, name: string) =>
  request<Aircraft>(`/models/${modelId}/aircraft`, { method: 'POST', body: JSON.stringify({ name }) });
export const updateAircraft = (id: number, name: string) =>
  request('/aircraft/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const deleteAircraft = (id: number, scope: DeleteScope = 'auto') =>
  request('/aircraft/' + id, { method: 'DELETE', body: JSON.stringify(deleteBody(scope)) });

// Flights
export interface FlightListFilters {
  model_id?: number | null;
  aircraft_id?: number | null;
  date_from?: string;
  date_to?: string;
  location?: string;
  weather?: string;
  payload?: string;
}

function buildQuery(params: Record<string, string | number | null | undefined>) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      qs.set(key, String(value));
    }
  });
  const text = qs.toString();
  return text ? `?${text}` : '';
}

export const listFlights = (filters: FlightListFilters = {}) =>
  request<{ flights: Flight[] }>(`/flights${buildQuery(filters as Record<string, string | number | null | undefined>)}`);
export const getFlight = (id: number) => request<Flight & { columns: ColumnGroup[] }>(`/flights/${id}`);
export const getRawFiles = (id: number) =>
  request<{ flight_id: number; files: RawFileItem[]; warnings: { file?: string; path?: string; error: string }[] }>(`/flights/${id}/raw-files`);
export const openRawFolder = (id: number) =>
  request<RawFolderOpenResult>(`/flights/${id}/raw-folder/open`, { method: 'POST' });
export const getSyncExportTree = (q = '') =>
  request<{ tree: SyncExportModelNode[]; flight_count: number }>(`/sync/export-tree${buildQuery({ q })}`);
export const exportSyncPackage = (flightIds: number[]) =>
  request<SyncExportResult>('/sync/export', {
    method: 'POST',
    body: JSON.stringify({ flight_ids: flightIds }),
  });
export const previewSyncImport = (packagePath: string) =>
  request<SyncImportPreview>('/sync/import/preview', {
    method: 'POST',
    body: JSON.stringify({ package_path: packagePath }),
  });
export const importSyncPackage = (payload: SyncImportRequest) =>
  request<SyncImportReport>('/sync/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
export const getSyncQueue = () => request<SyncQueueResponse>('/sync/queue');
export const getSyncProgress = (operationId: string) =>
  request<SyncProgress>(`/sync/progress/${encodeURIComponent(operationId)}`);
export const previewSync = (payload: { mode: 'run' | 'push' | 'pull'; flight_ids?: number[] | null; since?: string | null }) =>
  request<SyncPreviewResult>('/sync/preview', { method: 'POST', body: JSON.stringify(payload) });
export const runSync = (payload: SyncOperationRequest = {}) =>
  request<SyncOperationResult>('/sync/run', { method: 'POST', body: JSON.stringify(payload) });
export const pushSync = (payload: SyncOperationRequest = {}) =>
  request<SyncOperationResult>('/sync/push', { method: 'POST', body: JSON.stringify(payload) });
export const pullSync = (payload: SyncOperationRequest = {}) =>
  request<SyncOperationResult>('/sync/pull', { method: 'POST', body: JSON.stringify(payload) });
export const retrySync = (payload: SyncOperationRequest = {}) =>
  request<SyncOperationResult>('/sync/retry', { method: 'POST', body: JSON.stringify(payload) });
export const abandonSync = (flightIds: number[]) =>
  request<SyncOperationResult>('/sync/abandon', {
    method: 'POST',
    body: JSON.stringify({ flight_ids: flightIds }),
  });
export const deleteFlight = (id: number, scope: DeleteScope = 'auto') =>
  request('/flights/' + id, { method: 'DELETE', body: JSON.stringify(deleteBody(scope)) });
export const updateFlight = (id: number, name: string) =>
  request('/flights/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const updateFlightRecord = (id: number, record: FlightRecordFields) =>
  request('/flights/' + id + '/record', { method: 'PATCH', body: JSON.stringify(record) });
export const scanFolder = (sourcePath: string) =>
  request<ScanResult>(
    '/flights/scan', { method: 'POST', body: JSON.stringify({ source_path: sourcePath }) }
  );
export const importSession = (
  sourcePath: string,
  aircraftId: number,
  sessionKey: string,
  record: FlightRecordFields & { flight_date?: string | null } = {},
) =>
  request<ImportSessionResult>(
    '/flights/import', {
      method: 'POST',
      body: JSON.stringify({ source_path: sourcePath, aircraft_id: aircraftId, session_key: sessionKey, ...record }),
    }
  );

// Folder browser
export const browseFolder = () =>
  request<{ path: string; cancelled?: boolean }>('/folders/browse');

export const listSubdirs = (path: string) =>
  request<{ path: string; subdirs: string[] }>(`/folders/subdirs?path=${encodeURIComponent(path)}`);

// Column Registry
export const getRegistryColumns = (modelId: number) =>
  request<{ columns: ColumnGroup[] }>(`/registry/columns?model_id=${modelId}`);

// Data
export const getColumns = (flightId: number) => request<{ columns: ColumnGroup[] }>(`/flights/${flightId}/columns`);
export const getAlignedData = (flightId: number, columnKeys: string[], filter?: FilterSpec) =>
  request<AlignedData>(`/flights/${flightId}/aligned`, {
    method: 'POST',
    body: JSON.stringify({ column_keys: columnKeys, filter: filter || undefined }),
  });
export const getStats = (flightId: number) => request<FlightStats>(`/flights/${flightId}/stats`);

// Analysis
export const getCorrelation = (flightId: number, columnKeys: string[]) =>
  request<{ columns: string[]; labels: string[]; matrix: number[][] }>(`/flights/${flightId}/correlation`, {
    method: 'POST', body: JSON.stringify({ column_keys: columnKeys }),
  });
export const getAnomaly = (flightId: number, columnKey: string, windowSize = 30, sigma = 3.0) =>
  request<{ times: string[]; values: number[]; anomaly_indices: number[]; upper_bound: number[]; lower_bound: number[]; label: string; unit: string }>(
    `/flights/${flightId}/anomaly`, {
      method: 'POST', body: JSON.stringify({ column_key: columnKey, window_size: windowSize, sigma }),
    }
  );
export const getCompare = (flightIds: number[], columnKey: string) =>
  request<{ series: { flight_id: number; name: string; times_sec: number[]; values: number[]; label: string; unit: string }[] }>(
    '/compare', { method: 'POST', body: JSON.stringify({ flight_ids: flightIds, column_key: columnKey }) }
  );

// Presets
export const listPresets = (modelId: number) =>
  request<{ presets: Preset[] }>(`/presets?model_id=${modelId}`);
export const createPreset = (modelId: number, name: string, columns: string[]) =>
  request<Preset>('/presets', { method: 'POST', body: JSON.stringify({ model_id: modelId, name, columns }) });
export const deletePreset = (id: number) => request('/presets/' + id, { method: 'DELETE' });

// Filter Presets
export const listFilterPresets = (modelId: number) =>
  request<{ presets: FilterPreset[] }>(`/filter-presets?model_id=${modelId}`);
export const createFilterPreset = (modelId: number, name: string, config: FilterSpec) =>
  request<FilterPreset>('/filter-presets', { method: 'POST', body: JSON.stringify({ model_id: modelId, name, config }) });
export const deleteFilterPreset = (id: number) => request('/filter-presets/' + id, { method: 'DELETE' });
