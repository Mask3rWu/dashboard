import { request } from './client';

export interface HealthStatus {
  status: string; version: string; data_dir: string; db_path: string;
  db_exists: boolean; frontend_dir_exists: boolean;
}
export type Capability = 'manage_users' | 'change_own_password' | 'delete_models' | 'delete_aircraft' | 'delete_flights' | 'update_columns';
export interface CurrentUser {
  id: number; username: string; role: 'admin' | 'user'; created_at?: string;
  password_changed_at?: string | null; disabled_at?: string | null;
}
export interface AppContext {
  environment: 'research' | 'field'; node_id: string; user: CurrentUser | null; capabilities: Capability[];
}
export interface RuntimeContext {
  data_dir: string; server_base_url: string; server_reachable: boolean;
  server_status: 'online' | 'offline' | 'not_configured' | string;
  local_node_id: string; last_server_check_at: string; server_user: CurrentUser | null;
  server_capabilities: string[];
  sync_summary: { pending_upload: number; upload_failed: number; conflict: number; last_push_at?: string | null; last_pull_at?: string | null };
}
export interface ServerAuthPayload { user: CurrentUser | null; capabilities: string[]; token?: string; }
export interface LoginPayload extends AppContext { token: string; server_token?: string; login_mode?: 'online' | 'offline'; }

export const checkHealth = () => request<HealthStatus>('/health');
export const getAppContext = () => request<AppContext>('/app/context');
export const updateAppContext = (updates: { environment?: 'research' | 'field'; node_id?: string }) =>
  request<AppContext>('/app/context', { method: 'PATCH', body: JSON.stringify(updates) });
export const getRuntimeContext = () => request<RuntimeContext>('/runtime/context');
export const updateRuntimeConfig = (updates: { data_dir?: string; server_base_url?: string }) =>
  request<RuntimeContext>('/runtime/config', { method: 'PATCH', body: JSON.stringify(updates) });
export const serverLogin = (username: string, password: string) => request<ServerAuthPayload>('/server-auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
export const serverLogout = () => request<{ ok: boolean }>('/server-auth/logout', { method: 'POST' });
export const login = (username: string, password: string) => request<LoginPayload>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
export const logout = () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' });
export const changePassword = (oldPassword: string, newPassword: string) => request<{ ok: boolean }>('/auth/change-password', { method: 'POST', body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }) });
