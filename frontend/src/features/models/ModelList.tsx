import { Check, Download, LoaderCircle, Pencil, RefreshCw, Trash2, Upload } from 'lucide-react';
import type { AircraftModel } from '../../api/models';
import { deleteActionLabel } from '../../syncStatus';

interface Summary {
  totalAircraft: number;
  totalFlights: number;
  totalHours: number;
}

interface Props {
  models: AircraftModel[];
  filteredModels: AircraftModel[];
  selectedModelId: number | null;
  editingModelId: number | null;
  editModelName: string;
  deletingModelId: number | null;
  modelSearch: string;
  summary: Summary;
  canDeleteModels: boolean;
  canImportSyncPackage: boolean;
  serverOnline: boolean;
  readOnly?: boolean;
  syncable?: boolean;
  syncingModelId?: number | null;
  onExport: () => void;
  onImport: () => void;
  onSearchChange: (value: string) => void;
  onSelect: (modelId: number) => void;
  onStartRename: (model: AircraftModel) => void;
  onRenameValueChange: (value: string) => void;
  onRename: (modelId: number) => void;
  onCancelRename: () => void;
  onRequestDelete: (modelId: number) => void;
  onDelete: (model: AircraftModel) => void;
  onCancelDelete: () => void;
  onSyncModel?: (model: AircraftModel) => void;
}

export default function ModelList({
  models,
  filteredModels,
  selectedModelId,
  editingModelId,
  editModelName,
  deletingModelId,
  modelSearch,
  summary,
  canDeleteModels,
  canImportSyncPackage,
  serverOnline,
  readOnly = false,
  syncable = false,
  syncingModelId = null,
  onExport,
  onImport,
  onSearchChange,
  onSelect,
  onStartRename,
  onRenameValueChange,
  onRename,
  onCancelRename,
  onRequestDelete,
  onDelete,
  onCancelDelete,
  onSyncModel,
}: Props) {
  return (
    <aside className="w-64 shrink-0 border-r border-gray-200 overflow-y-auto bg-gray-50/50 flex flex-col">
      <div className="p-3 border-b border-gray-200 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">机型列表</span>
        {!readOnly && <div className="flex items-center gap-1">
          <button type="button" onClick={onExport} className="text-gray-400 hover:text-blue-500 p-0.5" title="导出同步包">
            <Upload className="w-3.5 h-3.5" />
          </button>
          {canImportSyncPackage && (
            <button type="button" onClick={onImport} className="text-gray-400 hover:text-emerald-500 p-0.5" title="导入同步包">
              <Download className="w-3.5 h-3.5" />
            </button>
          )}
        </div>}
      </div>

      <div className="px-3 py-2 border-b border-gray-100 bg-white space-y-1">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-gray-400">总飞机数</span>
          <span className="font-semibold text-gray-700">{summary.totalAircraft}</span>
        </div>
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-gray-400">总架次</span>
          <span className="font-semibold text-gray-700">{summary.totalFlights}</span>
        </div>
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-gray-400">总航时</span>
          <span className="font-semibold text-gray-700">{summary.totalHours.toFixed(1)} 小时</span>
        </div>
      </div>

      <div className="px-2 pt-2 pb-1">
        <input
          type="text"
          value={modelSearch}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="搜索机型..."
          className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
        />
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {filteredModels.map((model) => (
          <div key={model.id}>
            <div
              onClick={() => onSelect(model.id)}
              className={`rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                selectedModelId === model.id
                  ? 'bg-blue-50 border border-blue-200'
                  : 'bg-white border border-gray-200 hover:bg-gray-100'
              }`}
            >
              {editingModelId === model.id ? (
                <div className="flex items-center gap-1" onClick={(event) => event.stopPropagation()}>
                  <input
                    type="text"
                    value={editModelName}
                    onChange={(event) => onRenameValueChange(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') onRename(model.id);
                      if (event.key === 'Escape') onCancelRename();
                    }}
                    className="flex-1 bg-white border border-blue-400 rounded px-1 py-0.5 text-xs focus:outline-none"
                    autoFocus
                  />
                  <button type="button" onClick={() => onRename(model.id)} className="text-[10px] text-blue-600 px-1 hover:text-blue-700">✓</button>
                  <button type="button" onClick={onCancelRename} className="text-[10px] text-gray-400 px-1 hover:text-gray-500">✕</button>
                </div>
              ) : (
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-800 truncate">{model.name}</span>
                  {!readOnly && <div className="flex items-center gap-0.5 shrink-0 ml-1" onClick={(event) => event.stopPropagation()}>
                    <button type="button" onClick={() => onStartRename(model)} className="text-gray-300 hover:text-blue-500 p-0.5" title="重命名">
                      <Pencil className="w-3 h-3" />
                    </button>
                    {canDeleteModels && deletingModelId === model.id ? (
                      <span className="text-[10px] text-red-500 whitespace-nowrap">
                        {deleteActionLabel(model, serverOnline)}?{' '}
                        <button type="button" onClick={() => onDelete(model)} className="text-red-600 font-bold hover:text-red-700 px-0.5">是</button>
                        {' / '}
                        <button type="button" onClick={onCancelDelete} className="text-gray-400 hover:text-gray-500 px-0.5">否</button>
                      </span>
                    ) : canDeleteModels ? (
                      <button type="button" onClick={() => onRequestDelete(model.id)} className="text-gray-300 hover:text-red-500 p-0.5" title="删除">
                        <Trash2 className="w-3 h-3" />
                      </button>
                    ) : (
                      <span className="text-gray-200 p-0.5" title="当前环境或登录状态无删除机型权限">
                        <Trash2 className="w-3 h-3" />
                      </span>
                    )}
                  </div>}
                </div>
              )}
              <div className="mt-0.5 flex min-h-5 items-center justify-between gap-1">
                <span className="min-w-0 truncate text-[10px] text-gray-400">
                  {(model.aircraft_count ?? 0)} 架飞机 · {(model.total_flights ?? 0)} 架次 · {((model.total_flight_hours ?? 0) / 3600).toFixed(1)} 小时
                </span>
                {syncable && onSyncModel && (
                  <button
                    type="button"
                    onClick={(event) => { event.stopPropagation(); onSyncModel(model); }}
                    disabled={syncingModelId !== null || !!model.model_synced}
                    title={model.model_synced ? '该服务器机型已同步到本地' : '将服务器机型定义同步到本地'}
                    className="flex shrink-0 items-center gap-1 text-[10px] text-blue-600 hover:text-blue-500 disabled:cursor-wait disabled:text-gray-300"
                  >
                    {syncingModelId === model.id ? (
                      <LoaderCircle className="h-3 w-3 animate-spin" />
                    ) : model.model_synced ? (
                      <Check className="h-3 w-3 text-emerald-600" />
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    {syncingModelId === model.id ? '同步中' : model.model_synced ? '已同步' : '同步机型'}
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
        {filteredModels.length === 0 && models.length > 0 && <p className="text-xs text-gray-400 p-2">未找到匹配的机型</p>}
        {models.length === 0 && <p className="text-xs text-gray-400 p-2">暂无机型</p>}
      </div>
    </aside>
  );
}
