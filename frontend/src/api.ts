const BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || res.statusText);
  }
  return res.json();
}

export interface Flight {
  id: number;
  name: string;
  drone_id: string;
  drone_model: string;
  source_path: string;
  flight_date: string;
  start_time: string;
  end_time: string;
  duration_sec: number;
  import_time: string;
}

export interface ColumnGroup {
  table: string;
  label: string;
  columns: ColumnItem[];
}

export interface ColumnItem {
  key: string;
  label: string;
  unit: string;
}

export interface AlignedData {
  times: string[];
  ref_secs: number[];
  series: Record<string, {
    label: string;
    unit: string;
    table: string;
    values: (number | null)[];
  }>;
  alerts: AlertItem[];
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
  name: string;
  columns: string[];
}

// Flights
export const listFlights = () => request<{ flights: Flight[] }>('/flights');
export const getFlight = (id: number) => request<Flight & { columns: ColumnGroup[] }>(`/flights/${id}`);
export const deleteFlight = (id: number) => request('/flights/' + id, { method: 'DELETE' });
export const scanFolder = (sourcePath: string) =>
  request<{ files?: { drone_id: string; file_count: number; data_types: Record<string, number> }[]; error?: string }>(
    '/flights/scan', { method: 'POST', body: JSON.stringify({ source_path: sourcePath }) }
  );
export const importFolder = (sourcePath: string) =>
  request<{ imported?: { flight_id: number; drone_id: string; name: string; rows: number; details: Record<string, number> }[]; error?: string }>(
    '/flights/import', { method: 'POST', body: JSON.stringify({ source_path: sourcePath }) }
  );

// Data
export const getColumns = (flightId: number) => request<{ columns: ColumnGroup[] }>(`/flights/${flightId}/columns`);
export const getAlignedData = (flightId: number, columnKeys: string[], refTable = 'gps_data', tolerance = 0.5) =>
  request<AlignedData>(`/flights/${flightId}/aligned`, {
    method: 'POST',
    body: JSON.stringify({ column_keys: columnKeys, ref_table: refTable, tolerance }),
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
  request<{ series: { flight_id: number; name: string; times_pct: number[]; values: number[]; label: string; unit: string }[] }>(
    '/compare', { method: 'POST', body: JSON.stringify({ flight_ids: flightIds, column_key: columnKey }) }
  );

// Presets
export const listPresets = () => request<{ presets: Preset[] }>('/presets');
export const createPreset = (name: string, columns: string[]) =>
  request<Preset>('/presets', { method: 'POST', body: JSON.stringify({ name, columns }) });
export const deletePreset = (id: number) => request('/presets/' + id, { method: 'DELETE' });
