import { Pencil } from 'lucide-react';
import type { DataTypeGroup } from '../../api/models';

interface ColumnEditValue { label: string; unit: string; }

interface Props {
  groups: DataTypeGroup[];
  canEdit: boolean;
  editing: boolean;
  editData: Record<string, ColumnEditValue>;
  showOriginalName: boolean;
  editingGroupLabel: string | null;
  groupLabelValue: string;
  onShowOriginalNameChange: (value: boolean) => void;
  onStartBatchEdit: () => void;
  onSaveAll: () => void;
  onCancelBatchEdit: () => void;
  onStartGroupEdit: (dataTypeKey: string, label: string) => void;
  onGroupLabelValueChange: (value: string) => void;
  onSaveGroupLabel: (dataTypeKey: string) => void;
  onCancelGroupEdit: () => void;
  onColumnEditField: (key: string, field: 'label' | 'unit', value: string) => void;
}

export default function ColumnEditor({ groups, canEdit, editing, editData, showOriginalName, editingGroupLabel, groupLabelValue, onShowOriginalNameChange, onStartBatchEdit, onSaveAll, onCancelBatchEdit, onStartGroupEdit, onGroupLabelValueChange, onSaveGroupLabel, onCancelGroupEdit, onColumnEditField }: Props) {
  return (
    <div className="min-w-0 overflow-y-auto border-l border-gray-200 pl-6" style={{ flex: '4' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-700">列定义 ({groups.reduce((sum, group) => sum + group.columns.length, 0)} 列)</h3>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[10px] text-gray-400 cursor-pointer select-none">
            <input type="checkbox" checked={showOriginalName} onChange={(event) => onShowOriginalNameChange(event.target.checked)} className="w-3 h-3" />原字段
          </label>
          {canEdit && groups.length > 0 && (!editing ? (
            <button onClick={onStartBatchEdit} className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-500">编辑列定义</button>
          ) : (
            <div className="flex items-center gap-2">
              <button onClick={onSaveAll} className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-500">保存全部</button>
              <button onClick={onCancelBatchEdit} className="px-3 py-1.5 text-xs bg-gray-200 text-gray-600 rounded-lg hover:bg-gray-300">取消</button>
            </div>
          ))}
        </div>
      </div>

      {groups.length === 0 ? <p className="text-xs text-gray-400">暂无列定义</p> : (
        <div className="space-y-3">
          {groups.map((group) => (
            <div key={group.data_type_key} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-gray-50 text-xs font-medium text-gray-600 flex items-center justify-between">
                {editingGroupLabel === group.data_type_key ? (
                  <div className="flex items-center gap-1 flex-1">
                    <input type="text" value={groupLabelValue} onChange={(event) => onGroupLabelValueChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') onSaveGroupLabel(group.data_type_key); if (event.key === 'Escape') onCancelGroupEdit(); }} className="flex-1 bg-white border border-blue-400 rounded px-1.5 py-0.5 text-xs focus:outline-none" autoFocus />
                    <button onClick={() => onSaveGroupLabel(group.data_type_key)} className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">✓</button>
                    <button onClick={onCancelGroupEdit} className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">✕</button>
                  </div>
                ) : (
                  <>
                    <span>{group.label}</span>
                    {canEdit && <button onClick={() => onStartGroupEdit(group.data_type_key, group.label)} className="text-gray-300 hover:text-blue-500 text-[10px] ml-2" title="编辑组名称"><Pencil className="w-3 h-3 inline" /></button>}
                  </>
                )}
              </div>
              <div className="divide-y divide-gray-100">
                {group.columns.map((column) => {
                  const editKey = `${group.data_type_key}::${column.column_name}`;
                  const value = editData[editKey];
                  return (
                    <div key={column.column_name} className="flex items-center px-3 py-1.5 text-xs gap-1">
                      <span className="text-gray-400 w-6 shrink-0">{column.ordinal}</span>
                      {editing && value ? (
                        <>
                          <input type="text" value={value.label} onChange={(event) => onColumnEditField(editKey, 'label', event.target.value)} className="flex-1 bg-white border border-blue-400 rounded px-1.5 py-0.5 text-xs focus:outline-none min-w-0" placeholder="显示名称" />
                          {showOriginalName && <span className="text-gray-400 font-mono text-xs shrink-0 truncate" style={{ width: '4.5rem' }} title={column.column_name}>{column.column_name}</span>}
                          <input type="text" value={value.unit} onChange={(event) => onColumnEditField(editKey, 'unit', event.target.value)} className="bg-white border border-blue-400 rounded px-1.5 py-0.5 text-xs focus:outline-none" style={{ width: '3rem' }} placeholder="单位" />
                        </>
                      ) : (
                        <>
                          <span className="flex-1 text-gray-700 truncate">{column.display_label || column.column_name}</span>
                          {showOriginalName && <span className="text-gray-400 font-mono text-xs shrink-0 truncate" style={{ width: '4.5rem' }} title={column.column_name}>{column.column_name}</span>}
                          <span className="text-gray-400 shrink-0 text-right" style={{ width: '3rem' }}>{column.unit || '-'}</span>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
