import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, Eye, RefreshCw, RotateCcw, Server, Upload, XCircle } from 'lucide-react';
import {
  getSyncQueue,
  previewSync,
  pullSync,
  pushSync,
  retrySync,
  runSync,
  type SyncPreviewResult,
  type SyncQueueResponse,
} from '../api/sync';
import type { RuntimeContext } from '../api/auth';
import { syncStateClass, syncStateLabel } from '../syncStatus';
import SyncPreviewDialog from '../features/sync/SyncPreviewDialog';
import SyncProgress from '../features/sync/SyncProgress';
import { formatTime, stringifyDetail, type SyncActionKind } from '../features/sync/previewFormatters';
import { useSyncOperation } from '../features/sync/useSyncOperation';

type QueueFilter = 'all' | 'pending_upload' | 'dirty' | 'upload_failed' | 'conflict';

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

function serverReady(runtime: RuntimeContext | null) {
  return !!runtime?.server_base_url && !!runtime.server_reachable;
}

export default function SyncPage({ runtime, onRefreshContext, onDataChanged, onNavigateToFlight }: Props) {
  const [queue, setQueue] = useState<SyncQueueResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [preview, setPreview] = useState<SyncPreviewResult | null>(null);
  const [pendingAction, setPendingAction] = useState<SyncActionKind | null>(null);
  const [pendingFlightIds, setPendingFlightIds] = useState<number[] | null>(null);
  const [pullResolutions, setPullResolutions] = useState<Record<string, 'local' | 'server'>>({});
  const [filter, setFilter] = useState<QueueFilter>('all');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { busy, progress, message, error, setError, execute } = useSyncOperation();

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
  }, [setError]);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled) return loadQueue();
    });
    return () => { cancelled = true; };
  }, [loadQueue]);

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
  const serverUnavailableReason = !runtime?.server_base_url
    ? '未配置服务器地址'
    : !runtime?.server_reachable
      ? '服务器不可达'
      : '';

  const refreshAll = async () => {
    await loadQueue();
    await onRefreshContext();
    await onDataChanged();
  };

  const operationCallbacks = {
    onSuccess: async () => {
      setSelectedIds(new Set());
      await refreshAll();
    },
    onFailure: async () => {
      await onRefreshContext();
      await loadQueue();
    },
  };

  const openPreview = async (kind: SyncActionKind, flightIds: number[] | null = null) => {
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

  const confirmPreview = async () => {
    if (!pendingAction) return;
    const pullPackagePath = preview?.pull?.package_path ?? null;
    setPreviewOpen(false);
    if (pendingAction === 'run') {
      await execute('run', (operation_id) => runSync({
        operation_id,
        flight_ids: pendingFlightIds,
        pull_conflict_resolutions: pullResolutions,
      }), operationCallbacks);
    } else if (pendingAction === 'pull') {
      await execute('pull', (operation_id) => pullSync({
        operation_id,
        package_path: pullPackagePath,
        conflict_resolutions: pullResolutions,
      }), operationCallbacks);
    } else if (pendingAction === 'push') {
      await execute('push', (operation_id) => pushSync({ operation_id, flight_ids: pendingFlightIds }), operationCallbacks);
    } else {
      await execute('retry', (operation_id) => retrySync({ operation_id, flight_ids: pendingFlightIds }), operationCallbacks);
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
  const showProgress = !!progress && (!!busy || progress.status === 'failed');

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
              title={!runtime?.server_user ? '请先登录服务器' : serverUnavailableReason || '执行 push 后 pull'}
              onClick={() => openPreview('run')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${busy === 'run' ? 'animate-spin' : ''}`} />
              {busy === 'run' ? '同步中...' : '同步一次'}
            </button>
            <button
              type="button"
              disabled={!ready || !runtime?.server_user || !!busy}
              title={!runtime?.server_user ? '请先登录服务器' : serverUnavailableReason || '仅上传本地待同步数据'}
              onClick={() => openPreview('push')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              <Upload className="w-3.5 h-3.5" />
              {busy === 'push' ? '上传中...' : '只上传'}
            </button>
            <button
              type="button"
              disabled={!ready || !runtime?.server_user || !!busy}
              title={!runtime?.server_user ? '请先登录服务器' : serverUnavailableReason || '把服务器数据导入当前本地缓存'}
              onClick={() => openPreview('pull')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-slate-700 text-white hover:bg-slate-600 disabled:opacity-50"
            >
              <Download className="w-3.5 h-3.5" />
              {busy === 'pull' ? '拉取中...' : '从服务器拉取到本地'}
            </button>
          </div>
        </div>
        {showProgress && (
          <SyncProgress progress={progress} busy={busy} />
        )}
        {!ready && (
          <div className="mt-3 flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
            <XCircle className="w-3.5 h-3.5" />
            {serverUnavailableReason}，登录和服务器同步操作不可用。本地导入、查看和分析不受影响。
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
        <SyncPreviewDialog
          loading={previewLoading}
          error={previewError}
          preview={preview}
          action={pendingAction}
          pullResolutions={pullResolutions}
          onResolutionChange={(serverId, resolution) => setPullResolutions((prev) => ({ ...prev, [serverId]: resolution }))}
          onClose={() => setPreviewOpen(false)}
          onConfirm={confirmPreview}
        />
      )}
    </div>
  );
}
