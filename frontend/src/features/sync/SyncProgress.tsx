import { RefreshCw } from 'lucide-react';
import type { SyncProgress as SyncProgressState } from '../../api/sync';
import { operationLabel, type SyncActionKind } from './previewFormatters';

interface Props {
  progress: SyncProgressState;
  busy: SyncActionKind | null;
}

export default function SyncProgress({ progress, busy }: Props) {
  const percent = Math.max(0, Math.min(100, progress.percent ?? 0));
  return (
    <div className={`mt-3 rounded border px-3 py-3 text-xs ${progress.status === 'failed' ? 'bg-red-50 border-red-200 text-red-700' : 'bg-blue-50 border-blue-200 text-blue-800'}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-medium">
            <RefreshCw className={`w-3.5 h-3.5 shrink-0 ${busy ? 'animate-spin' : ''}`} />
            <span>{operationLabel(busy)}：{progress.phase}</span>
          </div>
          <div className="mt-1 truncate text-gray-600">{progress.message}</div>
        </div>
        <div className="shrink-0 font-mono text-sm">{percent}%</div>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-white border border-blue-100">
        <div className={`h-full transition-all duration-300 ${progress.status === 'failed' ? 'bg-red-500' : 'bg-blue-600'}`} style={{ width: `${percent}%` }} />
      </div>
      {typeof progress.current === 'number' && typeof progress.total === 'number' && progress.total <= 10000 && (
        <div className="mt-1 text-[11px] text-gray-500">当前进度：{progress.current} / {progress.total}</div>
      )}
    </div>
  );
}
