import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, Eye, HelpCircle, RefreshCw, RotateCcw, Server, Upload, XCircle } from 'lucide-react';
import {
  getSyncProgress,
  getSyncQueue,
  previewSync,
  pullSync,
  pushSync,
  retrySync,
  runSync,
  type RuntimeContext,
  type SyncOperationResult,
  type SyncPreviewItem,
  type SyncPreviewResult,
  type SyncProgress,
  type SyncQueueResponse,
} from '../api';
import { syncStateClass, syncStateLabel } from '../syncStatus';

type QueueFilter = 'all' | 'pending_upload' | 'dirty' | 'upload_failed' | 'conflict';
type ActionKind = 'run' | 'push' | 'pull' | 'retry';

interface Props {
  runtime: RuntimeContext | null;
  onRefreshContext: () => Promise<void>;
  onDataChanged: () => void | Promise<void>;
  onNavigateToFlight: (flightId: number) => void;
}

const QUEUE_FILTERS: { key: QueueFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending_upload', label: '本地' },
  { key: 'dirty', label: '待更新' },
  { key: 'upload_failed', label: '上传失败' },
  { key: 'conflict', label: '冲突' },
];

function formatTime(value?: string | null) {
  if (!value) return '-';
  return value.replace('T', ' ').slice(0, 19);
}

function stringifyDetail(value: unknown) {
  if (value === null || value === undefined || value === '') return '无详情';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function serverReady(runtime: RuntimeContext | null) {
  return !!runtime?.sync_enabled && !!runtime.server_base_url && !!runtime.server_reachable;
}

function operationMessage(result: SyncOperationResult) {
  const status = result.status || (result.ok ? 'success' : 'failed');
  if (result.steps?.length) {
    return result.steps.map((step) => `${step.name}: ${step.status}${step.detail ? ` - ${step.detail}` : ''}`).join('；');
  }
  return `操作完成：${status}`;
}

function createOperationId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `sync-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function operationLabel(kind: ActionKind | null) {
  if (kind === 'run') return '同步一次';
  if (kind === 'push') return '只上传';
  if (kind === 'pull') return '从服务器拉取到本地';
  if (kind === 'retry') return '上传';
  return '同步';
}

function previewActionLabel(item: SyncPreviewItem) {
  if (item.action === 'create') return '新增';
  if (item.action === 'existing') return '服务器已有';
  if (item.action === 'update_metadata') return '更新信息';
  if (item.action === 'update') return '更新本地';
  if (item.action === 'attach_existing') return '匹配本地';
  if (item.action === 'server_deleted') return '服务器已删除';
  if (item.action === 'conflict') return '冲突';
  return item.action || '-';
}

function previewReasonLabel(reason?: string | null) {
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

function previewMatchedByLabel(value?: string | null) {
  if (value === 'client_uid') return '同一同步记录';
  if (value === 'business_key') return '同机型/飞机号/日期/架次';
  if (value === 'server_id') return '已关联服务器';
  return value || '';
}

function entityTypeLabel(value?: string | null) {
  if (value === 'model') return '机型';
  if (value === 'aircraft') return '飞机号';
  return '架次';
}

function baseObjectName(item: SyncPreviewItem) {
  if (item.entity_type === 'model') return item.model_name || item.name || '-';
  if (item.entity_type === 'aircraft') return item.aircraft_name || item.name || '-';
  return item.name || '-';
}

function baseModelName(item: SyncPreviewItem) {
  if (item.entity_type === 'model') return item.model_name || item.name || '-';
  return item.model_name || '-';
}

function uploadServerValue(item: SyncPreviewItem) {
  if (item.action === 'create') return '服务器无';
  return item.server_name || '已关联服务器';
}

function baseChangeSummary(item: SyncPreviewItem, direction: 'upload' | 'pull') {
  if (item.action === 'conflict') return previewReasonLabel(item.reason) || '需要处理冲突';
  if (item.action === 'create') return direction === 'upload' ? '新增到服务器' : '新增到本地';
  if (item.action === 'update_metadata') return direction === 'upload' ? '用本地信息更新服务器' : '用服务器信息更新本地';
  if (item.action === 'existing') return '无需变更';
  return previewActionLabel(item);
}

function flightChangeSummary(item: SyncPreviewItem, direction: 'upload' | 'pull') {
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

function flightDisplayName(item: SyncPreviewItem) {
  return item.name || item.session_key || '-';
}

function flightSubText(item: SyncPreviewItem) {
  const parts = [item.session_key, item.flight_date].filter(Boolean);
  return parts.length ? parts.join(' / ') : '-';
}

function PreviewHelp() {
  return (
    <div className="relative group inline-flex">
      <HelpCircle className="w-4 h-4 text-gray-400" />
      <div className="pointer-events-none absolute left-0 top-6 z-10 hidden w-80 rounded border border-gray-200 bg-white p-3 text-xs leading-5 text-gray-600 shadow-lg group-hover:block">
        <div className="font-medium text-gray-800 mb-1">同步预览说明</div>
        <div>基础信息指机型和飞机号，只同步名称等元数据，不传输原始文件。</div>
        <div>架次数据包含架次名称、记录单、原始文件和解析数据；只有缺少数据文件或新增架次时才需要同步包。</div>
        <div>表格中的“本地当前”和“服务器内容”用于确认这次会保留哪一侧的信息。</div>
      </div>
    </div>
  );
}

function sortPreviewItems<T extends SyncPreviewItem>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const left = [
      a.model_name || '',
      a.aircraft_name || '',
      a.flight_date || '',
      a.session_key || '',
      a.name || '',
    ].join('\u0000');
    const right = [
      b.model_name || '',
      b.aircraft_name || '',
      b.flight_date || '',
      b.session_key || '',
      b.name || '',
    ].join('\u0000');
    return left.localeCompare(right, 'zh-Hans-CN');
  });
}

export default function SyncPage({ runtime, onRefreshContext, onDataChanged, onNavigateToFlight }: Props) {
  const [queue, setQueue] = useState<SyncQueueResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<ActionKind | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [progress, setProgress] = useState<SyncProgress | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [preview, setPreview] = useState<SyncPreviewResult | null>(null);
  const [pendingAction, setPendingAction] = useState<ActionKind | null>(null);
  const [pendingFlightIds, setPendingFlightIds] = useState<number[] | null>(null);
  const [pullResolutions, setPullResolutions] = useState<Record<string, 'local' | 'server'>>({});
  const [filter, setFilter] = useState<QueueFilter>('all');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getSyncQueue();
      setQueue(data);
      setSelectedIds((prev) => new Set([...prev].filter((id) => data.items.some((item) => item.id === id))));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(() => {
    if (!operationId || !busy) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await getSyncProgress(operationId);
        if (!cancelled) setProgress(data);
      } catch {
        // The first poll can race the backend before it initializes progress.
      }
    };

    poll();
    const timer = window.setInterval(poll, 500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [busy, operationId]);

  const filteredItems = useMemo(() => {
    const items = queue?.items ?? [];
    if (filter === 'all') return items;
    if (filter === 'pending_upload') {
      return items.filter((item) => item.sync_state === 'local_only' || item.sync_state === 'pending_upload');
    }
    return items.filter((item) => item.sync_state === filter);
  }, [filter, queue]);

  const allVisibleSelected = filteredItems.length > 0 && filteredItems.every((item) => selectedIds.has(item.id));
  const selectedUploadableIds = filteredItems
    .filter((item) => selectedIds.has(item.id) && item.sync_state !== 'conflict')
    .map((item) => item.id);
  const ready = serverReady(runtime);
  const serverDisabledReason = !runtime?.sync_enabled
    ? '同步未启用'
    : !runtime?.server_base_url
      ? '未配置服务器地址'
      : !runtime?.server_reachable
        ? '服务器不可达'
        : '';

  const refreshAll = async () => {
    await loadQueue();
    await onRefreshContext();
    await onDataChanged();
  };

  const openPreview = async (kind: ActionKind, flightIds: number[] | null = null) => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError('');
    setPreview(null);
    setPendingAction(kind);
    setPendingFlightIds(flightIds);
    setPullResolutions({});
    try {
      const mode = kind === 'pull' ? 'pull' : kind === 'run' ? 'run' : 'push';
      const data = await previewSync({ mode, flight_ids: flightIds });
      const defaults: Record<string, 'local' | 'server'> = {};
      (data.pull?.conflicts ?? []).forEach((item) => {
        if (item.server_id !== null && item.server_id !== undefined) {
          defaults[String(item.server_id)] = 'local';
        }
      });
      setPullResolutions(defaults);
      setPreview(data);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewLoading(false);
    }
  };

  const execute = async (kind: ActionKind, action: (operationId: string) => Promise<SyncOperationResult>) => {
    const nextOperationId = createOperationId();
    setBusy(kind);
    setOperationId(nextOperationId);
    setProgress({
      operation_id: nextOperationId,
      status: 'running',
      phase: '准备开始',
      message: `${operationLabel(kind)}正在启动`,
      percent: 0,
      created_at: '',
      updated_at: '',
    });
    setError('');
    setMessage('');
    try {
      const result = await action(nextOperationId);
      setMessage(operationMessage(result));
      setProgress((prev) => prev ? {
        ...prev,
        status: result.ok ? 'completed' : 'failed',
        phase: result.ok ? '操作完成' : '操作失败',
        message: operationMessage(result),
        percent: 100,
      } : prev);
      setSelectedIds(new Set());
      await refreshAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setProgress((prev) => prev ? {
        ...prev,
        status: 'failed',
        phase: `${operationLabel(kind)}失败`,
        message: e instanceof Error ? e.message : String(e),
        percent: 100,
      } : prev);
      await onRefreshContext();
      await loadQueue();
    } finally {
      setBusy(null);
    }
  };

  const confirmPreview = async () => {
    if (!pendingAction) return;
    const pullPackagePath = preview?.pull?.package_path ?? null;
    setPreviewOpen(false);
    if (pendingAction === 'run') {
      await execute('run', (operation_id) => runSync({
        operation_id,
        flight_ids: pendingFlightIds,
        pull_package_path: pullPackagePath,
        pull_conflict_resolutions: pullResolutions,
      }));
    } else if (pendingAction === 'pull') {
      await execute('pull', (operation_id) => pullSync({
        operation_id,
        package_path: pullPackagePath,
        conflict_resolutions: pullResolutions,
      }));
    } else if (pendingAction === 'push') {
      await execute('push', (operation_id) => pushSync({ operation_id, flight_ids: pendingFlightIds }));
    } else {
      await execute('retry', (operation_id) => retrySync({ operation_id, flight_ids: pendingFlightIds }));
    }
  };

  const toggleSelected = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        filteredItems.forEach((item) => next.delete(item.id));
      } else {
        filteredItems.forEach((item) => next.add(item.id));
      }
      return next;
    });
  };

  const summary = queue?.summary;
  const progressPercent = Math.max(0, Math.min(100, progress?.percent ?? 0));
  const showProgress = !!progress && (!!busy || progress.status === 'failed');
  const uploadBasePreview = [...(preview?.upload?.models ?? []), ...(preview?.upload?.aircraft ?? [])];
  const pullBasePreview = [...(preview?.pull?.models ?? []), ...(preview?.pull?.aircraft ?? [])]
    .filter((item) => item.action !== 'existing');
  const uploadHasConflict = [
    ...(preview?.upload?.models ?? []),
    ...(preview?.upload?.aircraft ?? []),
    ...(preview?.upload?.items ?? []),
  ].some((item) => item.action === 'conflict');
  const pullBaseHasConflict = pullBasePreview.some((item) => item.action === 'conflict');
  const canConfirmPreview = !!preview && !previewLoading && !previewError && !uploadHasConflict && !pullBaseHasConflict;
  const sortedUploadBasePreview = sortPreviewItems(uploadBasePreview);
  const sortedPullBasePreview = sortPreviewItems(pullBasePreview);
  const sortedUploadPreview = sortPreviewItems(preview?.upload?.items ?? []);
  const sortedPullPreview = sortPreviewItems(preview?.pull?.items ?? []);

  return (
    <div className="h-full overflow-auto p-6 space-y-5 bg-white">
      <section className="border border-gray-200 rounded-lg bg-gray-50 px-4 py-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
              <Server className="w-4 h-4 text-blue-600" />
              <span>同步状态</span>
              <span className={`text-[10px] px-2 py-0.5 rounded border ${ready ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-red-50 text-red-700 border-red-200'}`}>
                {runtime?.server_status || 'unknown'}
              </span>
            </div>
            <div className="mt-2 grid grid-cols-2 xl:grid-cols-4 gap-x-6 gap-y-1 text-xs text-gray-500">
              <div className="truncate">服务器：<span className="text-gray-800">{runtime?.server_base_url || '未配置'}</span></div>
              <div>同步：<span className="text-gray-800">{runtime?.sync_enabled ? '启用' : '关闭'}</span></div>
              <div>登录用户：<span className="text-gray-800">{runtime?.server_user?.username || '未登录服务器'}</span></div>
              <div>本机节点：<span className="text-gray-800 font-mono">{runtime?.local_node_id || '-'}</span></div>
              <div>待上传：<span className="text-amber-700 font-medium">{runtime?.sync_summary.pending_upload ?? 0}</span></div>
              <div>上传失败：<span className="text-red-700 font-medium">{runtime?.sync_summary.upload_failed ?? 0}</span></div>
              <div>冲突：<span className="text-red-700 font-medium">{runtime?.sync_summary.conflict ?? 0}</span></div>
              <div>检查时间：<span className="text-gray-800">{formatTime(runtime?.last_server_check_at)}</span></div>
              <div>上次上传：<span className="text-gray-800">{formatTime(runtime?.sync_summary.last_push_at)}</span></div>
              <div>上次拉取：<span className="text-gray-800">{formatTime(runtime?.sync_summary.last_pull_at)}</span></div>
            </div>
          </div>
          <div className="flex flex-wrap justify-end gap-2 shrink-0">
            <button
              type="button"
              disabled={!ready || !runtime?.server_user || !!busy}
              title={!runtime?.server_user ? '请先登录服务器' : serverDisabledReason || '执行 push 后 pull'}
              onClick={() => openPreview('run')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${busy === 'run' ? 'animate-spin' : ''}`} />
              {busy === 'run' ? '同步中...' : '同步一次'}
            </button>
            <button
              type="button"
              disabled={!ready || !runtime?.server_user || !!busy}
              title={!runtime?.server_user ? '请先登录服务器' : serverDisabledReason || '仅上传本地待同步数据'}
              onClick={() => openPreview('push')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              <Upload className="w-3.5 h-3.5" />
              {busy === 'push' ? '上传中...' : '只上传'}
            </button>
            <button
              type="button"
              disabled={!ready || !runtime?.server_user || !!busy}
              title={!runtime?.server_user ? '请先登录服务器' : serverDisabledReason || '把服务器数据导入当前本地缓存'}
              onClick={() => openPreview('pull')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-slate-700 text-white hover:bg-slate-600 disabled:opacity-50"
            >
              <Download className="w-3.5 h-3.5" />
              {busy === 'pull' ? '拉取中...' : '从服务器拉取到本地'}
            </button>
          </div>
        </div>
        {showProgress && (
          <div className={`mt-3 rounded border px-3 py-3 text-xs ${progress.status === 'failed' ? 'bg-red-50 border-red-200 text-red-700' : 'bg-blue-50 border-blue-200 text-blue-800'}`}>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-medium">
                  <RefreshCw className={`w-3.5 h-3.5 shrink-0 ${busy ? 'animate-spin' : ''}`} />
                  <span>{operationLabel(busy)}：{progress.phase}</span>
                </div>
                <div className="mt-1 truncate text-gray-600">{progress.message}</div>
              </div>
              <div className="shrink-0 font-mono text-sm">{progressPercent}%</div>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white border border-blue-100">
              <div
                className={`h-full transition-all duration-300 ${progress.status === 'failed' ? 'bg-red-500' : 'bg-blue-600'}`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            {typeof progress.current === 'number' && typeof progress.total === 'number' && progress.total <= 10000 && (
              <div className="mt-1 text-[11px] text-gray-500">
                当前进度：{progress.current} / {progress.total}
              </div>
            )}
          </div>
        )}
        {!ready && (
          <div className="mt-3 flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
            <XCircle className="w-3.5 h-3.5" />
            {serverDisabledReason}，登录和服务器同步操作不可用。本地导入、查看和分析不受影响。
          </div>
        )}
      </section>

      {(message || error) && (
        <div className={`flex items-start gap-2 rounded border px-3 py-2 text-xs ${error ? 'bg-red-50 border-red-200 text-red-700' : 'bg-emerald-50 border-emerald-200 text-emerald-700'}`}>
          {error ? <AlertTriangle className="w-4 h-4 shrink-0" /> : <CheckCircle2 className="w-4 h-4 shrink-0" />}
          <span>{error || message}</span>
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">上传队列与冲突</h2>
            <div className="text-xs text-gray-500 mt-1">
              本地 {summary?.pending_upload ?? 0} / dirty {summary?.dirty ?? 0} / failed {summary?.upload_failed ?? 0} / conflict {summary?.conflict ?? 0}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as QueueFilter)}
              className="bg-white border border-gray-300 rounded px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
            >
              {QUEUE_FILTERS.map((item) => (
                <option key={item.key} value={item.key}>{item.label}</option>
              ))}
            </select>
            <button
              type="button"
              disabled={loading || !!busy}
              onClick={loadQueue}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              刷新
            </button>
            <button
              type="button"
              disabled={!ready || !runtime?.server_user || !!busy || selectedUploadableIds.length === 0}
              onClick={() => openPreview('retry', selectedUploadableIds)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-amber-600 text-white hover:bg-amber-500 disabled:opacity-50"
            >
              <RotateCcw className={`w-3.5 h-3.5 ${busy === 'retry' ? 'animate-spin' : ''}`} />
              {busy === 'retry' ? '上传中...' : '上传'}
            </button>
          </div>
        </div>

        <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
          <div className="grid grid-cols-[36px_1.2fr_120px_120px_110px_150px_120px] gap-3 px-3 py-2 bg-gray-50 border-b border-gray-200 text-[11px] font-medium text-gray-500">
            <button type="button" onClick={toggleAllVisible} className="text-left text-gray-500 hover:text-gray-800">
              {allVisibleSelected ? '清' : '选'}
            </button>
            <span>架次</span>
            <span>状态</span>
            <span>飞机</span>
            <span>原始文件</span>
            <span>更新时间</span>
            <span className="text-right">操作</span>
          </div>
          {loading ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">加载中...</div>
          ) : filteredItems.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">当前筛选下没有待处理项</div>
          ) : (
            <div className="divide-y divide-gray-100">
              {filteredItems.map((item) => (
                <div key={item.id}>
                  <div className="grid grid-cols-[36px_1.2fr_120px_120px_110px_150px_120px] gap-3 px-3 py-3 items-center text-sm">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(item.id)}
                      onChange={() => toggleSelected(item.id)}
                      className="w-4 h-4"
                    />
                    <div className="min-w-0">
                      <button
                        type="button"
                        onClick={() => onNavigateToFlight(item.id)}
                        className="text-left font-medium text-gray-900 hover:text-blue-600 truncate block max-w-full"
                      >
                        {item.name}
                      </button>
                      <div className="text-xs text-gray-400 truncate">
                        {item.model_name} / {item.session_key || '-'} / {item.flight_date || '-'}
                      </div>
                    </div>
                    <span className={`justify-self-start text-[10px] px-2 py-0.5 rounded border ${syncStateClass(item.sync_state)}`}>
                      {syncStateLabel(item.sync_state)}
                    </span>
                    <span className="text-xs text-gray-600 truncate">{item.aircraft_name}</span>
                    <span className="text-xs text-gray-500">{item.raw_file_count ?? 0}</span>
                    <span className="text-xs text-gray-500">{formatTime(item.updated_at || item.import_time)}</span>
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
                        className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600"
                      >
                        <Eye className="w-3.5 h-3.5" />
                        详情
                      </button>
                      {item.sync_state === 'upload_failed' && (
                        <button
                          type="button"
                          disabled={!ready || !runtime?.server_user || !!busy}
                          onClick={() => openPreview('retry', [item.id])}
                          className="text-xs text-amber-700 hover:text-amber-600 disabled:opacity-50"
                        >
                          上传
                        </button>
                      )}
                      {item.sync_state === 'conflict' && (
                        <span className="text-xs text-red-500" title="冲突必须人工处理后才能继续上传">需人工处理</span>
                      )}
                    </div>
                  </div>
                  {expandedId === item.id && (
                    <div className="mx-3 mb-3 rounded border border-gray-200 bg-gray-50 p-3">
                      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs text-gray-600 mb-3">
                        <div>服务器状态：<span className="text-gray-800">{item.server_id ? '已关联服务器' : '尚未上传'}</span></div>
                        <div>同步时间：<span className="text-gray-800">{formatTime(item.last_sync_at)}</span></div>
                        <div>同步状态：<span className="text-gray-800">{syncStateLabel(item.sync_state)}</span></div>
                        <div>原始文件：<span className="text-gray-800">{item.raw_file_count ?? 0} 个</span></div>
                        <div>地点：<span className="text-gray-800">{item.record_location || '-'}</span></div>
                        <div>天气：<span className="text-gray-800">{item.record_weather || '-'}</span></div>
                        <div>载荷：<span className="text-gray-800">{item.record_payload || '-'}</span></div>
                      </div>
                      <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded bg-white border border-gray-200 p-3 text-xs text-gray-700">
                        {stringifyDetail(item.sync_error ?? item.sync_error_json)}
                      </pre>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
      {previewOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-5xl max-h-[86vh] overflow-hidden rounded-lg bg-white shadow-xl border border-gray-200 flex flex-col">
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-200">
              <div>
                <div className="flex items-center gap-2">
                  <div className="text-base font-semibold text-gray-900">{operationLabel(pendingAction)}预览</div>
                  <PreviewHelp />
                </div>
                <div className="text-xs text-gray-500 mt-0.5">确认后才会开始写入本地或服务器</div>
              </div>
              <button
                type="button"
                onClick={() => setPreviewOpen(false)}
                className="px-3 py-1.5 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
              >
                关闭
              </button>
            </div>

            <div className="overflow-auto p-4 space-y-4">
              {previewLoading && (
                <div className="flex items-center gap-2 text-sm text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-3">
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  正在检查本地与服务器的同步清单...
                </div>
              )}
              {previewError && (
                <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-3">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>{previewError}</span>
                </div>
              )}

              {preview?.upload && (
                <section className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-semibold text-gray-900">待上传到服务器</div>
                    <div className="text-xs text-gray-500">
                      {sortedUploadBasePreview.length + sortedUploadPreview.length} 项
                      {typeof preview.upload.summary?.conflict === 'number' && preview.upload.summary.conflict > 0
                        ? ` / 冲突 ${preview.upload.summary.conflict}`
                        : ''}
                    </div>
                  </div>
                  {sortedUploadBasePreview.length > 0 && (
                    <div className="space-y-1">
                      <div className="flex items-center justify-between border-l-4 border-blue-500 pl-2">
                        <div className="text-xs font-semibold text-gray-800">基础信息：机型 / 飞机号</div>
                        <div className="text-[11px] text-gray-400">{sortedUploadBasePreview.length} 项</div>
                      </div>
                      <div className="border border-gray-200 rounded overflow-hidden">
                        <div className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 bg-blue-50 text-[11px] font-medium text-gray-600">
                          <span>对象</span>
                          <span>所属机型</span>
                          <span>本地将上传</span>
                          <span>服务器当前</span>
                          <span>将执行</span>
                        </div>
                        {sortedUploadBasePreview.map((item) => (
                          <div key={`up-base-${item.entity_type}-${item.id}`} className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                            <span className="text-gray-600">{entityTypeLabel(item.entity_type)}</span>
                            <span className="text-gray-700 truncate">{baseModelName(item)}</span>
                            <span className="font-medium text-gray-900 truncate">{baseObjectName(item)}</span>
                            <span className="text-gray-500 truncate">{uploadServerValue(item)}</span>
                            <span className={item.action === 'conflict' ? 'text-red-700 font-medium truncate' : 'text-gray-700 truncate'}>
                              {baseChangeSummary(item, 'upload')}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="space-y-1">
                    <div className="flex items-center justify-between border-l-4 border-slate-400 pl-2">
                      <div className="text-xs font-semibold text-gray-800">架次数据</div>
                      <div className="text-[11px] text-gray-400">{sortedUploadPreview.length} 项</div>
                    </div>
                    <div className="border border-gray-200 rounded overflow-hidden">
                      <div className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 bg-gray-50 text-[11px] font-medium text-gray-500">
                        <span>机型</span>
                        <span>飞机号</span>
                        <span>本地将上传</span>
                        <span>服务器当前</span>
                        <span>将执行</span>
                      </div>
                      {sortedUploadPreview.length === 0 ? (
                        <div className="px-3 py-6 text-center text-sm text-gray-400">没有待上传架次</div>
                      ) : (
                        sortedUploadPreview.map((item) => (
                          <div key={`up-${item.id}`} className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                            <span className="text-gray-600 truncate">{item.model_name || '-'}</span>
                            <span className="text-gray-600 truncate">{item.aircraft_name || '-'}</span>
                            <div className="min-w-0">
                              <div className="font-medium text-gray-900 truncate">{flightDisplayName(item)}</div>
                              <div className="text-gray-400 truncate">{flightSubText(item)}</div>
                            </div>
                            <span className="text-gray-500 truncate">
                              {item.action === 'create' ? '服务器无' : previewMatchedByLabel(item.matched_by) || '已关联服务器'}
                            </span>
                            <span className={item.action === 'conflict' ? 'text-red-700 font-medium truncate' : 'text-gray-700 truncate'}>
                              {flightChangeSummary(item, 'upload')}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </section>
              )}

              {preview?.pull && (
                <section className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-semibold text-gray-900">待下载到本地</div>
                    <div className="text-xs text-gray-500">
                      {sortedPullBasePreview.length + sortedPullPreview.length} 项 / 冲突 {preview.pull.conflicts.length}
                    </div>
                  </div>
                  {sortedPullBasePreview.length > 0 && (
                    <div className="space-y-1">
                      <div className="flex items-center justify-between border-l-4 border-blue-500 pl-2">
                        <div className="text-xs font-semibold text-gray-800">基础信息：机型 / 飞机号</div>
                        <div className="text-[11px] text-gray-400">{sortedPullBasePreview.length} 项</div>
                      </div>
                      <div className="border border-gray-200 rounded overflow-hidden">
                        <div className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 bg-blue-50 text-[11px] font-medium text-gray-600">
                          <span>对象</span>
                          <span>所属机型</span>
                          <span>本地当前</span>
                          <span>服务器内容</span>
                          <span>将执行</span>
                        </div>
                        {sortedPullBasePreview.map((item) => {
                          const conflict = item.action === 'conflict' && item.server_id !== null && item.server_id !== undefined;
                          return (
                            <div key={`pull-base-${item.entity_type}-${item.server_id}`} className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                              <span className="text-gray-600">{entityTypeLabel(item.entity_type)}</span>
                              <span className="text-gray-700 truncate">{baseModelName(item)}</span>
                              <span className="text-gray-500 truncate">{item.local?.name || '本地无'}</span>
                              <span className="font-medium text-gray-900 truncate">{baseObjectName(item)}</span>
                              <span className={conflict ? 'text-red-700 font-medium truncate' : 'text-gray-700 truncate'}>
                                {baseChangeSummary(item, 'pull')}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  <div className="space-y-1">
                    <div className="flex items-center justify-between border-l-4 border-slate-400 pl-2">
                      <div className="text-xs font-semibold text-gray-800">架次数据</div>
                      <div className="text-[11px] text-gray-400">{sortedPullPreview.length} 项</div>
                    </div>
                    <div className="border border-gray-200 rounded overflow-hidden">
                      <div className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 bg-gray-50 text-[11px] font-medium text-gray-500">
                        <span>机型</span>
                        <span>飞机号</span>
                        <span>本地当前</span>
                        <span>服务器内容</span>
                        <span>将执行</span>
                      </div>
                      {sortedPullPreview.length === 0 ? (
                        <div className="px-3 py-6 text-center text-sm text-gray-400">没有待下载架次</div>
                      ) : (
                        sortedPullPreview.map((item) => {
                          const conflict = item.action === 'conflict' && item.server_id !== null && item.server_id !== undefined;
                          const sid = String(item.server_id ?? '');
                          return (
                            <div key={`pull-${item.server_id}`} className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                              <span className="text-gray-600 truncate">{item.model_name || '-'}</span>
                              <span className="text-gray-600 truncate">{item.aircraft_name || '-'}</span>
                              <div className="min-w-0 text-gray-500">
                                {item.local ? (
                                  <>
                                    <div className="truncate">{item.local.name}</div>
                                    <div className="text-[11px] text-gray-400">{syncStateLabel(item.local.sync_state)}</div>
                                  </>
                                ) : '本地无'}
                              </div>
                              <div className="min-w-0">
                                <div className="font-medium text-gray-900 truncate">{flightDisplayName(item)}</div>
                                <div className="text-gray-400 truncate">{flightSubText(item)}</div>
                              </div>
                              {conflict ? (
                                <div className="flex flex-wrap items-center gap-2">
                                  <label className="inline-flex items-center gap-1">
                                    <input
                                      type="radio"
                                      checked={pullResolutions[sid] !== 'server'}
                                      onChange={() => setPullResolutions((prev) => ({ ...prev, [sid]: 'local' }))}
                                    />
                                    保留本地
                                  </label>
                                  <label className="inline-flex items-center gap-1">
                                    <input
                                      type="radio"
                                      checked={pullResolutions[sid] === 'server'}
                                      onChange={() => setPullResolutions((prev) => ({ ...prev, [sid]: 'server' }))}
                                    />
                                    使用服务器
                                  </label>
                                </div>
                              ) : (
                                <span className="text-gray-700 truncate">{flightChangeSummary(item, 'pull')}</span>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>
                  </div>
                </section>
              )}

              {uploadHasConflict && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
                  上传预检存在冲突，当前服务器协议不支持在此处直接覆盖服务器，请先处理冲突后再同步。
                </div>
              )}
              {pullBaseHasConflict && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
                  基础信息存在冲突，请先处理本地机型或飞机号改动后再拉取。
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-200 bg-gray-50">
              <button
                type="button"
                onClick={() => setPreviewOpen(false)}
                className="px-4 py-1.5 text-sm rounded border border-gray-300 text-gray-700 hover:bg-white"
              >
                取消
              </button>
              <button
                type="button"
                disabled={!canConfirmPreview}
                onClick={confirmPreview}
                className="px-4 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
              >
                确认执行
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
