import { request } from './client';

function buildQuery(params: Record<string, string | number | null | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && String(value).trim() !== '') query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : '';
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
