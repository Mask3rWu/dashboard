import type { DiscoveredType } from '../../api/imports';

interface Props {
  discoveredTypes: DiscoveredType[];
  hasMatchedModel: boolean;
  name: string;
  selectedTypes: Set<string>;
  creating: boolean;
  onNameChange: (value: string) => void;
  onSelectAll: () => void;
  onToggleType: (key: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

export default function ModelFromScanForm({ discoveredTypes, hasMatchedModel, name, selectedTypes, creating, onNameChange, onSelectAll, onToggleType, onSubmit, onCancel }: Props) {
  return (
    <div className="mb-4 p-4 bg-amber-50 rounded-lg border border-amber-200 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-amber-800">{hasMatchedModel ? '新建机型' : '发现新格式'}</span>
        <span className="text-xs text-amber-600">{hasMatchedModel ? '不使用当前推荐机型，按扫描结果创建新机型' : '未匹配到已有机型，创建新机型后即可导入'}</span>
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs text-gray-500">机型名称</span>
        <input value={name} onChange={(event) => onNameChange(event.target.value)} className="bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-800 focus:outline-none focus:border-blue-500" placeholder="给新机型命名" />
      </div>
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">数据类型（勾选要导入的，共 {discoveredTypes.length} 个）</span>
          <button onClick={onSelectAll} className="text-xs text-blue-600 hover:underline">全选</button>
        </div>
        {discoveredTypes.map((type) => (
          <label key={type.data_type_key} className="flex items-center gap-2 text-sm py-1 px-2 rounded hover:bg-white/60 cursor-pointer">
            <input type="checkbox" checked={selectedTypes.has(type.data_type_key)} onChange={() => onToggleType(type.data_type_key)} />
            <span className="text-gray-800">{type.display_label}</span>
            <span className="text-xs text-gray-400">{type.data_type_key}</span>
            {type.is_alert && <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-xs border border-amber-200">告警</span>}
            {type.is_raw && <span className="px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded text-xs border border-gray-300" title="疑似原始字节转储，分析价值低，默认不导入。可手动勾选。">原始数据</span>}
            <span className="text-xs text-gray-400 ml-auto">{type.column_count} 列</span>
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={onSubmit} disabled={creating || !name.trim() || selectedTypes.size === 0} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">{creating ? '创建中…' : '创建机型并继续'}</button>
        {hasMatchedModel && <button onClick={onCancel} disabled={creating} className="px-3 py-1.5 text-sm bg-white text-gray-600 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">取消</button>}
        {selectedTypes.size === 0 && <span className="text-xs text-red-500">至少选择一个数据类型</span>}
      </div>
    </div>
  );
}
