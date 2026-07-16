import type { AircraftModel } from '../../api/models';
import type { SyncImportPreview, SyncImportReport } from '../../api/sync';

export interface SyncModelAction {
  action: 'use_existing' | 'create';
  target_model_id?: number | null;
  name?: string | null;
}

export interface SyncAircraftMapping {
  action: 'use_existing' | 'create';
  target_aircraft_id?: number | null;
  name?: string | null;
}

interface Props {
  path: string;
  onPathChange: (value: string) => void;
  browsing: boolean;
  loading: boolean;
  error: string;
  preview: SyncImportPreview | null;
  report: SyncImportReport | null;
  models: AircraftModel[];
  modelActions: Record<number, SyncModelAction>;
  aircraftMappings: Record<number, SyncAircraftMapping>;
  conflictPolicy: 'skip' | 'update_records';
  onBrowse: () => void;
  onPreview: () => void;
  onModelActionChange: (sourceModelId: number, patch: Partial<SyncModelAction>) => void;
  onAircraftMappingChange: (sourceAircraftId: number, patch: Partial<SyncAircraftMapping>) => void;
  onConflictPolicyChange: (value: 'skip' | 'update_records') => void;
  onClose: () => void;
  onSubmit: () => void;
}

export default function ModelImportDialog({ path, onPathChange, browsing, loading, error, preview, report, models, modelActions, aircraftMappings, conflictPolicy, onBrowse, onPreview, onModelActionChange, onAircraftMappingChange, onConflictPolicyChange, onClose, onSubmit }: Props) {
  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6">
      <div className="w-full max-w-4xl max-h-[86vh] bg-white rounded-lg shadow-xl border border-gray-200 flex flex-col">
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between gap-4">
          <div><div className="text-base font-semibold text-gray-900">导入外场同步包</div><div className="text-xs text-gray-500 mt-1">先预览包内容，再确认机型、飞机映射和重复架次策略</div></div>
          <button onClick={onClose} className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded">关闭</button>
        </div>
        <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
          <input value={path} onChange={(event) => onPathChange(event.target.value)} placeholder="输入 .fapkg 同步包路径，或点击浏览选择" className="flex-1 bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:border-blue-500" />
          <button onClick={onBrowse} disabled={browsing || loading} className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40">{browsing ? '...' : '浏览'}</button>
          <button onClick={onPreview} disabled={loading || !path.trim()} className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40">{loading ? '处理中...' : '预览'}</button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
          {error && <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-3 py-2 break-all">{error}</div>}
          {preview && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <div className="rounded border border-gray-200 px-3 py-2"><div className="text-gray-400">来源节点</div><div className="text-gray-800 font-medium truncate">{preview.summary.source_node_id || '-'}</div></div>
                <div className="rounded border border-gray-200 px-3 py-2"><div className="text-gray-400">导出时间</div><div className="text-gray-800 font-medium truncate">{preview.summary.exported_at || '-'}</div></div>
                <div className="rounded border border-gray-200 px-3 py-2"><div className="text-gray-400">范围</div><div className="text-gray-800 font-medium">{preview.summary.flight_count} 架次 / {preview.summary.aircraft_count} 飞机</div></div>
                <div className="rounded border border-gray-200 px-3 py-2"><div className="text-gray-400">导入路径</div><div className={preview.summary.compatible ? 'text-green-700 font-medium' : 'text-amber-700 font-medium'}>{preview.summary.compatible ? 'parsed.sqlite 直接导入' : '需要原始文件重解析'}</div></div>
              </div>
              {!preview.summary.compatible && <div className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded px-3 py-2">当前界面暂不执行不兼容包的重解析导入，请使用同 package/schema 版本导出的同步包。</div>}

              <div className="space-y-2">
                <div className="text-sm font-semibold text-gray-800">机型处理</div>
                {preview.model_plans.map((plan) => {
                  const action = modelActions[plan.source_model_id] ?? { action: plan.default_action, name: plan.create_name };
                  return (
                    <div key={plan.source_model_id} className="rounded border border-gray-200 px-3 py-2 flex items-center gap-3 text-xs">
                      <span className="font-medium text-gray-800 w-40 truncate">{plan.source_name}</span>
                      {plan.matched_model ? <span className="text-green-700">匹配到机型：{plan.matched_model.name}</span> : (
                        <>
                          <select value={action.action} onChange={(event) => onModelActionChange(plan.source_model_id, { action: event.target.value as SyncModelAction['action'] })} className="bg-white border border-gray-300 rounded px-2 py-1"><option value="create">新建机型</option><option value="use_existing">指定已有机型</option></select>
                          {action.action === 'create' ? (
                            <input value={action.name ?? plan.create_name} onChange={(event) => onModelActionChange(plan.source_model_id, { name: event.target.value })} className="bg-white border border-gray-300 rounded px-2 py-1 flex-1" />
                          ) : (
                            <select value={action.target_model_id ?? ''} onChange={(event) => onModelActionChange(plan.source_model_id, { target_model_id: event.target.value ? Number(event.target.value) : null })} className="bg-white border border-gray-300 rounded px-2 py-1 flex-1"><option value="">选择机型...</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="space-y-2">
                <div className="text-sm font-semibold text-gray-800">飞机映射</div>
                {preview.aircraft_plans.map((plan) => {
                  const mapping = aircraftMappings[plan.source_aircraft_id] ?? { action: plan.default_action, name: plan.create_name };
                  return (
                    <div key={plan.source_aircraft_id} className="rounded border border-gray-200 px-3 py-2 flex items-center gap-3 text-xs">
                      <span className="font-medium text-gray-800 w-40 truncate">{plan.source_name}</span>
                      {plan.matched_aircraft ? <span className="text-green-700">匹配到飞机：{plan.matched_aircraft.name}</span> : (
                        <>
                          <select value={mapping.action} onChange={(event) => onAircraftMappingChange(plan.source_aircraft_id, { action: event.target.value as SyncAircraftMapping['action'] })} className="bg-white border border-gray-300 rounded px-2 py-1"><option value="create">新建飞机</option><option value="use_existing">指定已有飞机</option></select>
                          {mapping.action === 'create' ? (
                            <input value={mapping.name ?? plan.create_name} onChange={(event) => onAircraftMappingChange(plan.source_aircraft_id, { name: event.target.value })} className="bg-white border border-gray-300 rounded px-2 py-1 flex-1" />
                          ) : (
                            <select value={mapping.target_aircraft_id ?? ''} onChange={(event) => onAircraftMappingChange(plan.source_aircraft_id, { target_aircraft_id: event.target.value ? Number(event.target.value) : null })} className="bg-white border border-gray-300 rounded px-2 py-1 flex-1"><option value="">选择飞机...</option>{plan.existing_aircraft.map((aircraft) => <option key={aircraft.id} value={aircraft.id}>{aircraft.name}</option>)}</select>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="rounded border border-gray-200 px-3 py-2 text-xs space-y-2">
                <div className="flex items-center justify-between gap-3"><span className="font-medium text-gray-800">重复架次</span><span className="text-gray-500">{preview.duplicates.length} 个自动匹配重复项</span></div>
                <select value={conflictPolicy} onChange={(event) => onConflictPolicyChange(event.target.value as 'skip' | 'update_records')} className="bg-white border border-gray-300 rounded px-2 py-1"><option value="skip">保持现状，不更新记录字段</option><option value="update_records">更新已有架次名称和飞行记录字段</option></select>
              </div>
            </>
          )}
          {report && <div className="text-xs text-green-700 bg-green-50 border border-green-100 rounded px-3 py-2 space-y-1"><div>导入完成：{report.status}</div><div>新增 {report.imported_flights.length}，跳过 {report.skipped_flights.length}，更新 {report.updated_flights.length}，warning {report.warnings.length}，失败 {report.failures.length}</div><div>解析数据行：{report.parsed_rows ?? 0}，原始文件：{report.raw_files?.attached ?? 0}</div></div>}
        </div>
        <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200">取消</button>
          <button onClick={onSubmit} disabled={loading || !preview || !preview.summary.compatible} className="px-4 py-1.5 text-sm bg-emerald-600 text-white rounded hover:bg-emerald-500 disabled:opacity-40">{loading ? '导入中...' : '确认导入'}</button>
        </div>
      </div>
    </div>
  );
}
