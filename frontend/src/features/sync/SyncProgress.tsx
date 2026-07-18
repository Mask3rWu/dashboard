import { RefreshCw } from 'lucide-react';
import type { SyncProgress as SyncProgressState } from '../../api/sync';
import { operationLabel, type SyncActionKind } from './previewFormatters';

interface Props {
  progress: SyncProgressState;
  busy: SyncActionKind | null;
}

export default function SyncProgress({ progress, busy }: Props) {
  const percent = Math.max(0, Math.min(100, progress.percent ?? 0));
  const formatCount = (value: number) => new Intl.NumberFormat('zh-CN').format(value);
  const formatRate = (value: number, unit?: string | null) => {
    if (unit === 'bytes') {
      const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
      let amount = value;
      let index = 0;
      while (amount >= 1024 && index < units.length - 1) {
        amount /= 1024;
        index += 1;
      }
      return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    }
    return `${formatCount(Math.round(value))} ${unit === 'rows' ? '行/秒' : unit === 'files' ? '文件/秒' : '项/秒'}`;
  };
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
        <div className="shrink-0 font-mono text-sm">{percent.toFixed(1)}%</div>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-white border border-blue-100">
        <div className={`h-full transition-all duration-300 ${progress.status === 'failed' ? 'bg-red-500' : 'bg-blue-600'}`} style={{ width: `${percent}%` }} />
      </div>
      {typeof progress.current === 'number' && (
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-500">
          <span>
            当前阶段：{formatCount(progress.current)}
            {typeof progress.total === 'number' ? ` / ${formatCount(progress.total)}` : ''}
            {progress.unit === 'rows' ? ' 行' : progress.unit === 'files' ? ' 个文件' : progress.unit === 'bytes' ? ' 字节' : ' 项'}
          </span>
          {progress.table_name && <span>表：{progress.table_name}</span>}
          {progress.file_name && <span>文件：{progress.file_name}</span>}
          {typeof progress.rate === 'number' && <span>{formatRate(progress.rate, progress.unit)}</span>}
          {typeof progress.eta_seconds === 'number' && <span>约剩 {Math.ceil(progress.eta_seconds)} 秒</span>}
        </div>
      )}
    </div>
  );
}
