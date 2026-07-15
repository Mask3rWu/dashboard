import { request } from './client';
import type { ColumnGroup } from './analysis';
import type { DeleteScope } from './models';

export interface FlightRecordFields { record_total_duration_min?: number | null; record_location?: string; record_payload?: string; record_weather?: string; record_fuel_amount?: number | null; record_takeoff_weight?: number | null; record_altitude?: number | null; record_wind_speed?: number | null; record_wind_direction?: string; record_temperature?: number | null; record_note?: string; }
export interface Flight extends FlightRecordFields { id: number; client_uid?: string; server_id?: number | null; source_node_id?: string | null; sync_origin?: 'local' | 'server' | 'package' | string; sync_state?: 'local_only' | 'pending_upload' | 'syncing' | 'synced' | 'dirty' | 'upload_failed' | 'conflict' | 'server_cache' | 'server_deleted' | string; server_version?: number | null; last_sync_at?: string | null; sync_error_json?: string | null; name: string; aircraft_id: number; aircraft_name: string; model_id: number; model_name: string; source_path: string; session_key: string; flight_date: string; start_time: string; end_time: string; duration_sec: number; import_time: string; raw_file_count?: number; raw_import_warnings?: string; raw_warnings?: { file?: string; path?: string; error: string }[]; drone_id?: string; drone_model?: string; }
export interface RawFileItem { id: number; flight_id: number; original_name: string; original_rel_path: string; data_type_key?: string | null; source_mtime?: number | null; created_at: string; sha256: string; size_bytes: number; storage_rel_path: string; }
export interface RawFolderOpenResult { flight_id: number; file_count: number; path: string; warnings: { file?: string; storage_rel_path?: string; error: string; detail?: string }[]; }
export interface FlightListFilters { model_id?: number | null; aircraft_id?: number | null; date_from?: string; date_to?: string; location?: string; weather?: string; payload?: string; }
export interface FlightFilterField { key: string; label: string; type: 'text' | 'number'; unit?: string; }
export interface FlightFilterCondition { field: string; op: 'contains' | 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'between'; value: string | null; min_val: number | null; max_val: number | null; }
export interface FlightFilterSpec { logic: 'and' | 'or'; conditions: FlightFilterCondition[]; }
export const FLIGHT_FILTER_FIELDS: FlightFilterField[] = [
  { key: 'record_location', label: '地点', type: 'text' }, { key: 'record_weather', label: '天气', type: 'text' },
  { key: 'record_payload', label: '设备载荷', type: 'text' }, { key: 'record_wind_direction', label: '风向', type: 'text' },
  { key: 'record_total_duration_min', label: '总时长', type: 'number', unit: 'min' }, { key: 'record_fuel_amount', label: '燃油量', type: 'number', unit: 'kg' },
  { key: 'record_takeoff_weight', label: '起飞重量', type: 'number', unit: 'kg' }, { key: 'record_altitude', label: '海拔高度', type: 'number', unit: 'm' },
  { key: 'record_wind_speed', label: '风速', type: 'number', unit: 'm/s' }, { key: 'record_temperature', label: '温度', type: 'number', unit: '°C' },
];
function query(params: Record<string, string | number | null | undefined>) { const qs = new URLSearchParams(); Object.entries(params).forEach(([key, value]) => { if (value !== null && value !== undefined && String(value).trim() !== '') qs.set(key, String(value)); }); const text = qs.toString(); return text ? `?${text}` : ''; }
export const listFlights = (filters: FlightListFilters = {}) => request<{ flights: Flight[] }>(`/flights${query(filters as Record<string, string | number | null | undefined>)}`);
export const getFlight = (id: number) => request<Flight & { columns: ColumnGroup[] }>(`/flights/${id}`);
export const getRawFiles = (id: number) => request<{ flight_id: number; files: RawFileItem[]; warnings: { file?: string; path?: string; error: string }[] }>(`/flights/${id}/raw-files`);
export const openRawFolder = (id: number) => request<RawFolderOpenResult>(`/flights/${id}/raw-folder/open`, { method: 'POST' });
export const deleteFlight = (id: number, scope: DeleteScope = 'auto') => request('/flights/' + id, { method: 'DELETE', body: JSON.stringify({ scope }) });
export const updateFlight = (id: number, name: string) => request('/flights/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const updateFlightRecord = (id: number, record: FlightRecordFields) => request('/flights/' + id + '/record', { method: 'PATCH', body: JSON.stringify(record) });
