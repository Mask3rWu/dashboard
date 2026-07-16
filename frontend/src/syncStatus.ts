import type { Flight } from './api/flights';

export type SyncStateFilter =
  | 'all'
  | 'server_cache'
  | 'synced'
  | 'local_unsynced'
  | 'dirty'
  | 'upload_failed'
  | 'conflict'
  | 'server_deleted';

export const SYNC_STATE_FILTERS: { key: SyncStateFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'server_cache', label: '服务器缓存' },
  { key: 'synced', label: '已同步' },
  { key: 'local_unsynced', label: '本地' },
  { key: 'dirty', label: '待更新' },
  { key: 'upload_failed', label: '上传失败' },
  { key: 'conflict', label: '冲突' },
  { key: 'server_deleted', label: '服务器已删除' },
];

export function syncStateLabel(state?: string | null) {
  const labels: Record<string, string> = {
    local_only: '本地',
    pending_upload: '本地',
    syncing: '同步中',
    synced: '已同步',
    dirty: '待更新',
    upload_failed: '上传失败',
    conflict: '冲突',
    server_cache: '服务器缓存',
    server_deleted: '服务器已删除',
  };
  return labels[state || ''] || state || '未标记';
}

export function syncStateClass(state?: string | null) {
  if (state === 'local_only' || state === 'pending_upload' || state === 'dirty') return 'bg-amber-50 text-amber-700 border-amber-200';
  if (state === 'upload_failed' || state === 'conflict') return 'bg-red-50 text-red-700 border-red-200';
  if (state === 'synced' || state === 'server_cache') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (state === 'server_deleted') return 'bg-gray-100 text-gray-500 border-gray-300 line-through';
  return 'bg-gray-50 text-gray-600 border-gray-200';
}

export function deleteScopeFor(item?: { sync_state?: string | null; server_id?: number | null }, serverOnline = true) {
  if (!item?.server_id) return 'local_unsynced';
  if (!serverOnline) return 'local_cache';
  if (item.sync_state === 'synced' || item.sync_state === 'server_cache' || item.sync_state === 'dirty') return 'server';
  return 'local_cache';
}

export function deleteActionLabel(item?: { sync_state?: string | null; server_id?: number | null }, serverOnline = true) {
  const scope = deleteScopeFor(item, serverOnline);
  if (scope === 'server') return '删除服务器数据';
  if (scope === 'local_cache') return '清理本地缓存';
  return '删除本地数据';
}

export function matchesSyncStateFilter(flight: Pick<Flight, 'sync_state'>, filter: SyncStateFilter) {
  const state = flight.sync_state || '';
  if (filter === 'all') return true;
  if (filter === 'local_unsynced') return state === 'local_only' || state === 'pending_upload';
  return state === filter;
}
