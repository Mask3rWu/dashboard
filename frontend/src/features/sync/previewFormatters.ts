import type { SyncOperationResult, SyncPreviewItem } from '../../api/sync';

export type SyncActionKind = 'run' | 'push' | 'pull' | 'retry';

export function formatTime(value?: string | null) {
  if (!value) return '-';
  return value.replace('T', ' ').slice(0, 19);
}

export function stringifyDetail(value: unknown) {
  if (value === null || value === undefined || value === '') return '无详情';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function operationMessage(result: SyncOperationResult) {
  const status = result.status || (result.ok ? 'success' : 'failed');
  if (result.steps?.length) {
    return result.steps.map((step) => `${step.name}: ${step.status}${step.detail ? ` - ${step.detail}` : ''}`).join('；');
  }
  return `操作完成：${status}`;
}

export function operationLabel(kind: SyncActionKind | null) {
  if (kind === 'run') return '同步一次';
  if (kind === 'push') return '只上传';
  if (kind === 'pull') return '从服务器拉取到本地';
  if (kind === 'retry') return '上传';
  return '同步';
}

export function previewActionLabel(item: SyncPreviewItem) {
  if (item.action === 'create') return '新增';
  if (item.action === 'existing') return '服务器已有';
  if (item.action === 'update_metadata') return '更新信息';
  if (item.action === 'update') return '更新本地';
  if (item.action === 'attach_existing') return '匹配本地';
  if (item.action === 'server_deleted') return '服务器已删除';
  if (item.action === 'conflict') return '冲突';
  return item.action || '-';
}

export function previewReasonLabel(reason?: string | null) {
  if (!reason) return '';
  const labels: Record<string, string> = {
    business_key_conflict: '业务键匹配到不同服务器记录',
    business_key_raw_hash_mismatch: '同架次原始文件不一致',
    local_unsynced_business_key_conflict: '本地未上传架次与服务器架次相同',
    dirty_flight: '本地和服务器均有改动',
    dirty_model: '本地机型也有改动',
    dirty_aircraft: '本地飞机号也有改动',
    server_deleted_dirty_local: '服务器已删除但本地有改动',
    server_changed_since_last_sync: '服务器已有更新，需先处理冲突',
    model_name_conflict: '机型名称已被占用',
    aircraft_name_conflict: '飞机号名称已被占用',
  };
  return labels[reason] || reason;
}

export function previewMatchedByLabel(value?: string | null) {
  if (value === 'client_uid') return '同一同步记录';
  if (value === 'business_key') return '同机型/飞机号/日期/架次';
  if (value === 'server_id') return '已关联服务器';
  return value || '';
}

export function entityTypeLabel(value?: string | null) {
  if (value === 'model') return '机型';
  if (value === 'aircraft') return '飞机号';
  return '架次';
}

export function baseObjectName(item: SyncPreviewItem) {
  if (item.entity_type === 'model') return item.model_name || item.name || '-';
  if (item.entity_type === 'aircraft') return item.aircraft_name || item.name || '-';
  return item.name || '-';
}

export function baseModelName(item: SyncPreviewItem) {
  if (item.entity_type === 'model') return item.model_name || item.name || '-';
  return item.model_name || '-';
}

export function uploadServerValue(item: SyncPreviewItem) {
  if (item.action === 'create') return '服务器无';
  return item.server_name || '已关联服务器';
}

export function baseChangeSummary(item: SyncPreviewItem, direction: 'upload' | 'pull') {
  if (item.action === 'conflict') return previewReasonLabel(item.reason) || '需要处理冲突';
  if (item.action === 'create') return direction === 'upload' ? '新增到服务器' : '新增到本地';
  if (item.action === 'update_metadata') return direction === 'upload' ? '用本地信息更新服务器' : '用服务器信息更新本地';
  if (item.action === 'existing') return '无需变更';
  return previewActionLabel(item);
}

export function flightChangeSummary(item: SyncPreviewItem, direction: 'upload' | 'pull') {
  if (item.action === 'conflict') return previewReasonLabel(item.reason) || '需要处理冲突';
  if (item.action === 'create') return direction === 'upload' ? '新增到服务器' : '新增到本地';
  if (item.action === 'update_metadata' || item.action === 'update') {
    return direction === 'upload' ? '用本地架次信息更新服务器' : '用服务器架次信息更新本地';
  }
  if (item.action === 'attach_existing') return '关联本地已有架次';
  if (item.action === 'existing') return '无需变更';
  if (item.action === 'server_deleted') return '标记服务器已删除';
  return previewActionLabel(item);
}

export function flightDisplayName(item: SyncPreviewItem) {
  return item.name || item.session_key || '-';
}

export function flightSubText(item: SyncPreviewItem) {
  const parts = [item.session_key, item.flight_date].filter(Boolean);
  return parts.length ? parts.join(' / ') : '-';
}

export function sortPreviewItems<T extends SyncPreviewItem>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const left = [a.model_name || '', a.aircraft_name || '', a.flight_date || '', a.session_key || '', a.name || ''].join('\u0000');
    const right = [b.model_name || '', b.aircraft_name || '', b.flight_date || '', b.session_key || '', b.name || ''].join('\u0000');
    return left.localeCompare(right, 'zh-Hans-CN');
  });
}
