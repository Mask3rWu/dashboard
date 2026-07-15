import { request } from './client';
import type { Flight } from './flights';

export type DeleteScope = 'auto' | 'local_cache' | 'local_unsynced' | 'server';
const deleteBody = (scope: DeleteScope = 'auto') => ({ scope });
export interface AircraftModel { id: number; name: string; created_at: string; client_uid?: string | null; server_id?: number | null; sync_origin?: string | null; sync_state?: Flight['sync_state']; server_version?: number | null; server_deleted_at?: string | null; aircraft_count?: number; total_flights?: number; total_flight_hours?: number; }
export interface Aircraft { id: number; model_id: number; name: string; created_at: string; client_uid?: string | null; server_id?: number | null; sync_origin?: string | null; sync_state?: Flight['sync_state']; server_version?: number | null; server_deleted_at?: string | null; flight_count?: number; }
export interface ColumnDetail { column_name: string; display_label: string; unit: string; scale_factor: number; data_type: string; ordinal: number; }
export interface DataTypeGroup { data_type_key: string; table: string; label: string; columns: ColumnDetail[]; }

export const listModels = () => request<{ models: AircraftModel[] }>('/models');
export const createModel = (name: string) => request<AircraftModel>('/models', { method: 'POST', body: JSON.stringify({ name }) });
export const createModelFromScan = (name: string, sourcePath: string, selectedDataTypes?: string[]) => request<AircraftModel>('/models/from-scan', { method: 'POST', body: JSON.stringify({ name, source_path: sourcePath, selected_data_types: selectedDataTypes ?? null }) });
export const updateModel = (id: number, name: string) => request('/models/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const deleteModel = (id: number, scope: DeleteScope = 'auto') => request('/models/' + id, { method: 'DELETE', body: JSON.stringify(deleteBody(scope)) });
export const exportModel = (modelId: number) => request<{ ok: boolean; path: string; filename: string }>(`/models/${modelId}/export`);
export const importModel = (name: string, data: object) => request<AircraftModel>('/models/import', { method: 'POST', body: JSON.stringify({ name, data }) });
export const getModelColumns = (modelId: number) => request<{ data_types: DataTypeGroup[] }>(`/models/${modelId}/columns`);
export const updateModelColumn = (modelId: number, dataTypeKey: string, columnName: string, updates: { display_label?: string; unit?: string; scale_factor?: number }) => request<{ column_name: string; display_label: string; unit: string; scale_factor: number }>(`/models/${modelId}/columns?data_type_key=${encodeURIComponent(dataTypeKey)}&column_name=${encodeURIComponent(columnName)}`, { method: 'PATCH', body: JSON.stringify(updates) });
export const updateModelDataTypeLabel = (modelId: number, dataTypeKey: string, displayLabel: string) => request<{ ok: boolean; data_type_key: string; display_label: string }>(`/models/${modelId}/data-types/${encodeURIComponent(dataTypeKey)}`, { method: 'PATCH', body: JSON.stringify({ display_label: displayLabel }) });
export const listAircraft = (modelId: number) => request<{ aircraft: Aircraft[] }>(`/models/${modelId}/aircraft`);
export const createAircraft = (modelId: number, name: string) => request<Aircraft>(`/models/${modelId}/aircraft`, { method: 'POST', body: JSON.stringify({ name }) });
export const updateAircraft = (id: number, name: string) => request('/aircraft/' + id, { method: 'PATCH', body: JSON.stringify({ name }) });
export const deleteAircraft = (id: number, scope: DeleteScope = 'auto') => request('/aircraft/' + id, { method: 'DELETE', body: JSON.stringify(deleteBody(scope)) });
