import { request } from './client';
import type { FilterSpec } from './analysis';
import type { Flight, FlightFilterSpec } from './flights';
import type { Aircraft, AircraftModel, DataTypeGroup } from './models';

export interface RemoteFlightSearchQuery {
  model_id: number;
  aircraft_search?: string;
  time_from?: string;
  time_to?: string;
  record_filter?: FlightFilterSpec | null;
  data_filter?: FilterSpec | null;
  page: number;
  page_size: number;
}

export interface AircraftSearchSummary {
  aircraft_id: number;
  matched_count: number;
  matched_duration_sec: number;
}

export interface RemoteFlightSearchResult {
  flights: Flight[];
  page: number;
  page_size: number;
  total: number;
  summary: { flight_count: number; duration_sec: number };
  aircraft_summaries: AircraftSearchSummary[];
}

export interface RemoteDownloadResult {
  ok: boolean;
  status: string;
  report: {
    created: { models?: number; aircraft?: number; flights: number };
    updated: { models?: number; aircraft?: number; flights: number };
    already_downloaded?: { flights: number };
    conflicts: unknown[];
    warnings: unknown[];
  };
}

export interface RemoteModelSyncResult {
  ok: boolean;
  status: string;
  action: 'created' | 'linked' | 'updated';
  server_model_id: number;
  local_model_id: number;
  model: { id: number; name: string; server_id: number };
}

export const listRemoteModels = () =>
  request<{ models: AircraftModel[] }>('/remote-data/models');

export const listRemoteAircraft = (modelId: number) =>
  request<{ aircraft: Aircraft[] }>(`/remote-data/models/${modelId}/aircraft`);

export const getRemoteModelColumns = (modelId: number) =>
  request<{ data_types: DataTypeGroup[] }>(`/remote-data/models/${modelId}/columns`);

export const syncRemoteModel = (modelId: number) =>
  request<RemoteModelSyncResult>(`/remote-data/models/${modelId}/sync`, {
    method: 'POST',
  });

export const searchRemoteFlights = (query: RemoteFlightSearchQuery) =>
  request<RemoteFlightSearchResult>('/remote-data/flights/search', {
    method: 'POST',
    body: JSON.stringify(query),
  });

export const downloadRemoteFlights = (modelId: number, flightIds: number[], operationId?: string) =>
  request<RemoteDownloadResult>('/remote-data/flights/download', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId, flight_ids: flightIds, operation_id: operationId }),
  });
