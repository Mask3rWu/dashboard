import { request } from './client';
import type { CurrentUser } from './auth';

export const listUsers = () => request<{ users: CurrentUser[] }>('/users');
export const createUser = (username: string, password: string, role: CurrentUser['role']) => request<CurrentUser>('/users', { method: 'POST', body: JSON.stringify({ username, password, role }) });
export const updateUser = (id: number, username: string) => request<CurrentUser>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify({ username }) });
export const resetUserPassword = (id: number) => request<CurrentUser>(`/users/${id}/reset-password`, { method: 'POST' });
export const deleteUser = (id: number) => request<{ ok: boolean }>(`/users/${id}`, { method: 'DELETE' });
