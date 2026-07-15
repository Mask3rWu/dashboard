import { Server, Wifi, WifiOff } from 'lucide-react';
import type { RuntimeContext } from '../api/auth';

function formatTime(value?: string | null) {
  if (!value) return '-';
  return value.replace('T', ' ').slice(0, 16);
}

export default function RuntimeStatus({ runtime, onOpenSync }: { runtime: RuntimeContext | null; onOpenSync: () => void }) {
  const online = !!runtime?.server_reachable;
  const pending = runtime?.sync_summary.pending_upload ?? 0;
  const failed = runtime?.sync_summary.upload_failed ?? 0;
  const conflict = runtime?.sync_summary.conflict ?? 0;
  return (
    <button type="button" onClick={onOpenSync} className="hidden xl:flex items-center gap-2 max-w-[520px] px-2.5 py-1 rounded border border-gray-200 bg-white text-left hover:border-blue-300 hover:bg-blue-50" title={runtime?.server_base_url || '未配置服务器'}>
      <Server className="w-3.5 h-3.5 text-gray-400 shrink-0" />
      {online ? <Wifi className="w-3.5 h-3.5 text-emerald-600 shrink-0" /> : <WifiOff className="w-3.5 h-3.5 text-red-500 shrink-0" />}
      <span className={`text-[10px] px-1.5 py-0.5 rounded border ${online ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-red-50 text-red-700 border-red-200'}`}>{runtime?.server_status || 'unknown'}</span>
      <span className="text-xs text-amber-700">待 {pending}</span>
      <span className="text-xs text-red-700">失败 {failed}</span>
      <span className="text-xs text-red-700">冲突 {conflict}</span>
      <span className="text-[10px] text-gray-400">检查 {formatTime(runtime?.last_server_check_at)}</span>
      <span className="text-[10px] text-gray-400">pull {formatTime(runtime?.sync_summary.last_pull_at)}</span>
    </button>
  );
}
