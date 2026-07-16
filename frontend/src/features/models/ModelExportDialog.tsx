import type { SyncExportModelNode, SyncExportResult } from '../../api/sync';

interface Props {
  selectedIds: Set<number>;
  filter: string;
  onFilterChange: (value: string) => void;
  visibleFlightIds: number[];
  onSelectVisible: () => void;
  onClearVisible: () => void;
  loading: boolean;
  tree: SyncExportModelNode[];
  onToggleFlight: (flightId: number) => void;
  error: string;
  result: SyncExportResult | null;
  exporting: boolean;
  onClose: () => void;
  onSubmit: () => void;
}

export default function ModelExportDialog({ selectedIds, filter, onFilterChange, visibleFlightIds, onSelectVisible, onClearVisible, loading, tree, onToggleFlight, error, result, exporting, onClose, onSubmit }: Props) {
  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6">
      <div className="w-full max-w-4xl max-h-[86vh] bg-white rounded-lg shadow-xl border border-gray-200 flex flex-col">
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between gap-4">
          <div><div className="text-base font-semibold text-gray-900">导出离线同步包</div><div className="text-xs text-gray-500 mt-1">已选择 {selectedIds.size} 个架次，包将保存到固定 sync_exports 目录</div></div>
          <button onClick={onClose} className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded">关闭</button>
        </div>
        <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
          <input value={filter} onChange={(e) => onFilterChange(e.target.value)} placeholder="筛选机型、飞机、架次、日期、地点、天气" className="flex-1 bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:border-blue-500" />
          <button onClick={onSelectVisible} disabled={visibleFlightIds.length === 0} className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40">全选当前结果</button>
          <button onClick={onClearVisible} disabled={visibleFlightIds.length === 0} className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40">清除当前结果</button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
          {loading ? <div className="text-sm text-gray-400">加载中...</div> : tree.length === 0 ? <div className="text-sm text-gray-400">无可导出的架次</div> : tree.map((model) => (
            <div key={model.id} className="space-y-2">
              <div className="text-sm font-semibold text-gray-800">{model.name}</div>
              {model.aircraft.map((aircraft) => (
                <div key={aircraft.id} className="ml-3 border-l border-gray-200 pl-3 space-y-2">
                  <div className="text-xs font-medium text-blue-700">{aircraft.name}</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
                    {aircraft.flights.map((flight) => (
                      <label key={flight.id} className="flex items-center gap-2 rounded border border-gray-200 px-3 py-2 text-xs hover:bg-gray-50 cursor-pointer">
                        <input type="checkbox" checked={selectedIds.has(flight.id)} onChange={() => onToggleFlight(flight.id)} />
                        <span className="font-medium text-gray-800">{flight.name}</span>
                        {flight.flight_date && <span className="text-gray-400">{flight.flight_date}</span>}
                        {flight.session_key && <span className="font-mono text-gray-400">{flight.session_key}</span>}
                        {flight.record_location && <span className="text-gray-400">{flight.record_location}</span>}
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ))}
          {error && <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-3 py-2">{error}</div>}
          {result && <div className="text-xs text-green-700 bg-green-50 border border-green-100 rounded px-3 py-2 space-y-1"><div>导出完成: {result.filename}</div><div className="font-mono break-all">{result.path}</div><div>架次 {result.flight_count}，原始文件 {result.raw_file_count}</div></div>}
        </div>
        <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200">取消</button>
          <button onClick={onSubmit} disabled={exporting || selectedIds.size === 0} className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-40">{exporting ? '导出中...' : '导出'}</button>
        </div>
      </div>
    </div>
  );
}
