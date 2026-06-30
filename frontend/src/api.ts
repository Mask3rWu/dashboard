const BASE = '/api';

function parseErrorBody(text: string, fallback: string): string {
  if (!text) return fallback;
  try {
    const body = JSON.parse(text);
    const detail = body.detail || body.error || fallback;
    const errorType = body.error_type ? ` (${body.error_type})` : '';
    return `${detail}${errorType}`;
  } catch {
    return text || fallback;
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${url}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
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

export interface AircraftModel {
  id: number;
  name: string;
  format_category: string;
  created_at: string;
  aircraft_count?: number;
  total_flights?: number;
  total_flight_hours?: number;
}

export interface Aircraft {
  id: number;
  model_id: number;
  serial_number: string;
  name: string;
  created_at: string;
  flight_count?: number;
}

export interface Flight {
  id: number;
  name: string;
  aircraft_id: number;
  aircraft_serial: string;
  aircraft_name: string;
  model_id: number;
  model_name: string;
  format_category: string;
  source_path: string;
  session_key: string;
  flight_date: string;
  start_time: string;
  end_time: string;
  duration_sec: number;
  import_time: string;
  // Legacy fields (present in migrated data)
  drone_id?: string;
  drone_model?: string;
}

export interface SessionPreview {
  aircraft_serial: string;
  session_key: string;
  flight_date?: string | null;
  data_types: Record<string, number>;
  file_count: number;
  import_status: 'new' | 'imported';
  existing_flight_id?: number;
  existing_flight_name?: string;
  aircraft_id?: number;
  conflicting_aircraft?: { aircraft_serial: string; flight_id: number; flight_name: string }[];
}

export interface ScanResult {
  source_path: string;
  folder_name: string;
  format_category: string | null;
  format_detected?: boolean;
  model: {
    id: number;
    name: string;
    format_category: string;
    is_new: boolean;
    match_confidence: number | null;
  } | null;
  suggested_model_id?: number;
  suggested_model_name?: string;
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
  error?: string;
}

export interface ColumnGroup {
  data_type_key?: string;
  table: string;
  label: string;
  row_count?: number;
  sample_hz?: number | null;
  duration_sec?: number;
  columns: ColumnItem[];
}

export interface ColumnItem {
  key: string;
  label: string;
  unit: string;
  scale_factor: number;
}

export interface RefTableInfo {
  data_type_key: string;
  label: string;
  row_count: number;
  sample_hz: number | null;
  duration_sec: number;
  is_alert: boolean;
}

export interface AlignedData {
  ref_table?: string;
  ref_label?: string;
  ref_sample_hz?: number | null;
  tolerance?: number;
  ref_tables?: RefTableInfo[];
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
  alerts: AlertItem[];
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

export interface AlertItem {
  time_str: string;
  time_sec: number;
  desc: string;
  extra: string;
}

export interface FlightStats {
  duration_sec: number;
  start_time: string;
  end_time: string;
  drone_id: string;
  name: string;
  max_altitude: number;
  max_speed: number;
  avg_rpm: number;
  max_rpm: number;
  fuel_start: number;
  fuel_end: number;
  battery_start: number;
  battery_end: number;
  alert_count: number;
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

// Models
export const listModels = () => request<{ models: AircraftModel[] }>('/models');
export const createModel = (name: string, formatCategory: string) =>
  request<AircraftModel>('/models', { method: 'POST', body: JSON.stringify({ name, format_category: formatCategory }) });
export const createModelFromScan = (name: string, sourcePath: string, formatCategory: string) =>
  request<AircraftModel>('/models/from-scan', { method: 'POST', body: JSON.stringify({ name, source_path: sourcePath, format_category: formatCategory }) });
export const updateModel = (id: number, name: string) =>
  request('/models/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const deleteModel = (id: number) => request('/models/' + id, { method: 'DELETE' });

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
export const createAircraft = (modelId: number, serialNumber: string, name?: string) =>
  request<Aircraft>(`/models/${modelId}/aircraft`, { method: 'POST', body: JSON.stringify({ serial_number: serialNumber, name: name || '' }) });
export const updateAircraft = (id: number, serialNumber: string) =>
  request('/aircraft/' + id, { method: 'PATCH', body: JSON.stringify({ serial_number: serialNumber }) });
export const deleteAircraft = (id: number) => request('/aircraft/' + id, { method: 'DELETE' });

// Flights
export const listFlights = () => request<{ flights: Flight[] }>('/flights');
export const getFlight = (id: number) => request<Flight & { columns: ColumnGroup[] }>(`/flights/${id}`);
export const deleteFlight = (id: number) => request('/flights/' + id, { method: 'DELETE' });
export const updateFlight = (id: number, name: string) =>
  request('/flights/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const scanFolder = (sourcePath: string, formatCategory?: string) =>
  request<ScanResult>(
    '/flights/scan', { method: 'POST', body: JSON.stringify({ source_path: sourcePath, format_category: formatCategory || null }) }
  );
export const importSession = (sourcePath: string, aircraftId: number, sessionKey: string) =>
  request<ImportSessionResult>(
    '/flights/import', { method: 'POST', body: JSON.stringify({ source_path: sourcePath, aircraft_id: aircraftId, session_key: sessionKey }) }
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
export const getAlignedData = (flightId: number, columnKeys: string[], refTable?: string | null, tolerance?: number | null, filter?: FilterSpec) =>
  request<AlignedData>(`/flights/${flightId}/aligned`, {
    method: 'POST',
    body: JSON.stringify({ column_keys: columnKeys, ref_table: refTable || null, tolerance: tolerance ?? null, filter: filter || undefined }),
  });
export const getAlerts = (flightId: number) => request<{ alerts: AlertItem[] }>(`/flights/${flightId}/alerts`);
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
