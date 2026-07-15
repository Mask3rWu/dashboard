import { request } from './client';

export interface ColumnGroup { data_type_key?: string; table: string; label: string; row_count?: number; duration_sec?: number; columns: ColumnItem[]; }
export interface ColumnItem { key: string; label: string; unit: string; scale_factor: number; }
export interface AlignedData { times: string[]; ref_secs: number[]; series: Record<string, { label: string; unit: string; scale_factor: number; is_numeric: boolean; table: string; values: (number | null)[]; text_values?: (string | null)[] }>; mask?: boolean[]; segments?: { start: number; end: number }[]; }
export interface FilterCondition { column: string; op: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'between'; value: number | null; min_val: number | null; max_val: number | null; }
export interface FilterSpec { logic: 'and' | 'or'; conditions: FilterCondition[]; }
export interface FilterPreset { id: number; model_id: number; name: string; config: FilterSpec; }
export interface FlightStats { duration_sec: number; start_time: string; end_time: string; drone_id: string; name: string; }
export interface Preset { id: number; model_id: number; name: string; columns: string[]; }
export interface CorrelationData { columns: string[]; labels: string[]; matrix: number[][]; }
export interface AnomalyData { times: string[]; values: number[]; anomaly_indices: number[]; upper_bound: number[]; lower_bound: number[]; label: string; unit: string; }
export interface CompareSeries { flight_id: number; name: string; times_sec: number[]; values: number[]; label: string; unit: string; }
export const getRegistryColumns = (modelId: number) => request<{ columns: ColumnGroup[] }>(`/registry/columns?model_id=${modelId}`);
export const getColumns = (flightId: number) => request<{ columns: ColumnGroup[] }>(`/flights/${flightId}/columns`);
export const getAlignedData = (flightId: number, columnKeys: string[], filter?: FilterSpec) => request<AlignedData>(`/flights/${flightId}/aligned`, { method: 'POST', body: JSON.stringify({ column_keys: columnKeys, filter: filter || undefined }) });
export const getStats = (flightId: number) => request<FlightStats>(`/flights/${flightId}/stats`);
export const getCorrelation = (flightId: number, columnKeys: string[]) => request<CorrelationData>(`/flights/${flightId}/correlation`, { method: 'POST', body: JSON.stringify({ column_keys: columnKeys }) });
export const getAnomaly = (flightId: number, columnKey: string, windowSize = 30, sigma = 3.0) => request<AnomalyData>(`/flights/${flightId}/anomaly`, { method: 'POST', body: JSON.stringify({ column_key: columnKey, window_size: windowSize, sigma }) });
export const getCompare = (flightIds: number[], columnKey: string) => request<{ series: CompareSeries[] }>('/compare', { method: 'POST', body: JSON.stringify({ flight_ids: flightIds, column_key: columnKey }) });
export const listPresets = (modelId: number) => request<{ presets: Preset[] }>(`/presets?model_id=${modelId}`);
export const createPreset = (modelId: number, name: string, columns: string[]) => request<Preset>('/presets', { method: 'POST', body: JSON.stringify({ model_id: modelId, name, columns }) });
export const deletePreset = (id: number) => request('/presets/' + id, { method: 'DELETE' });
export const listFilterPresets = (modelId: number) => request<{ presets: FilterPreset[] }>(`/filter-presets?model_id=${modelId}`);
export const createFilterPreset = (modelId: number, name: string, config: FilterSpec) => request<FilterPreset>('/filter-presets', { method: 'POST', body: JSON.stringify({ model_id: modelId, name, config }) });
export const deleteFilterPreset = (id: number) => request('/filter-presets/' + id, { method: 'DELETE' });
