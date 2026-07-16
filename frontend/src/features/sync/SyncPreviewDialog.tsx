import { AlertTriangle, HelpCircle, RefreshCw } from 'lucide-react';
import type { SyncPreviewResult } from '../../api/sync';
import { syncStateLabel } from '../../syncStatus';
import {
  baseChangeSummary,
  baseModelName,
  baseObjectName,
  entityTypeLabel,
  flightChangeSummary,
  flightDisplayName,
  flightSubText,
  operationLabel,
  previewMatchedByLabel,
  sortPreviewItems,
  uploadServerValue,
  type SyncActionKind,
} from './previewFormatters';

interface Props {
  loading: boolean;
  error: string;
  preview: SyncPreviewResult | null;
  action: SyncActionKind | null;
  pullResolutions: Record<string, 'local' | 'server'>;
  onResolutionChange: (serverId: string, resolution: 'local' | 'server') => void;
  onClose: () => void;
  onConfirm: () => void;
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

export default function SyncPreviewDialog({ loading, error, preview, action, pullResolutions, onResolutionChange, onClose, onConfirm }: Props) {
  const uploadBase = sortPreviewItems([...(preview?.upload?.models ?? []), ...(preview?.upload?.aircraft ?? [])].filter((item) => item.action !== 'existing'));
  const pullBase = sortPreviewItems([...(preview?.pull?.models ?? []), ...(preview?.pull?.aircraft ?? [])].filter((item) => item.action !== 'existing'));
  const uploadItems = sortPreviewItems((preview?.upload?.items ?? []).filter((item) => item.action !== 'existing'));
  const pullItems = sortPreviewItems((preview?.pull?.items ?? []).filter((item) => item.action !== 'existing'));
  const uploadHasConflict = [...(preview?.upload?.models ?? []), ...(preview?.upload?.aircraft ?? []), ...(preview?.upload?.items ?? [])].some((item) => item.action === 'conflict');
  const pullBaseHasConflict = pullBase.some((item) => item.action === 'conflict');
  const canConfirm = !!preview && !loading && !error && !uploadHasConflict && !pullBaseHasConflict;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-5xl max-h-[86vh] overflow-hidden rounded-lg bg-white shadow-xl border border-gray-200 flex flex-col">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-200">
          <div>
            <div className="flex items-center gap-2">
              <div className="text-base font-semibold text-gray-900">{operationLabel(action)}预览</div>
              <PreviewHelp />
            </div>
            <div className="text-xs text-gray-500 mt-0.5">确认后才会开始写入本地或服务器</div>
          </div>
          <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50">关闭</button>
        </div>

        <div className="overflow-auto p-4 space-y-4">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-3">
              <RefreshCw className="w-4 h-4 animate-spin" />
              正在检查本地与服务器的同步清单...
            </div>
          )}
          {error && (
            <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-3">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {preview?.upload && (
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold text-gray-900">待上传到服务器</div>
                <div className="text-xs text-gray-500">
                  {uploadBase.length + uploadItems.length} 项
                  {typeof preview.upload.summary?.conflict === 'number' && preview.upload.summary.conflict > 0 ? ` / 冲突 ${preview.upload.summary.conflict}` : ''}
                </div>
              </div>
              {uploadBase.length > 0 && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between border-l-4 border-blue-500 pl-2">
                    <div className="text-xs font-semibold text-gray-800">基础信息：机型 / 飞机号</div>
                    <div className="text-[11px] text-gray-400">{uploadBase.length} 项</div>
                  </div>
                  <div className="border border-gray-200 rounded overflow-hidden">
                    <div className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 bg-blue-50 text-[11px] font-medium text-gray-600">
                      <span>对象</span><span>所属机型</span><span>本地将上传</span><span>服务器当前</span><span>将执行</span>
                    </div>
                    {uploadBase.map((item) => (
                      <div key={`up-base-${item.entity_type}-${item.id}`} className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                        <span className="text-gray-600">{entityTypeLabel(item.entity_type)}</span>
                        <span className="text-gray-700 truncate">{baseModelName(item)}</span>
                        <span className="font-medium text-gray-900 truncate">{baseObjectName(item)}</span>
                        <span className="text-gray-500 truncate">{uploadServerValue(item)}</span>
                        <span className={item.action === 'conflict' ? 'text-red-700 font-medium truncate' : 'text-gray-700 truncate'}>{baseChangeSummary(item, 'upload')}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="space-y-1">
                <div className="flex items-center justify-between border-l-4 border-slate-400 pl-2">
                  <div className="text-xs font-semibold text-gray-800">架次数据</div>
                  <div className="text-[11px] text-gray-400">{uploadItems.length} 项</div>
                </div>
                <div className="border border-gray-200 rounded overflow-hidden">
                  <div className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 bg-gray-50 text-[11px] font-medium text-gray-500">
                    <span>机型</span><span>飞机号</span><span>本地将上传</span><span>服务器当前</span><span>将执行</span>
                  </div>
                  {uploadItems.length === 0 ? (
                    <div className="px-3 py-6 text-center text-sm text-gray-400">没有待上传架次</div>
                  ) : uploadItems.map((item) => (
                    <div key={`up-${item.id}`} className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                      <span className="text-gray-600 truncate">{item.model_name || '-'}</span>
                      <span className="text-gray-600 truncate">{item.aircraft_name || '-'}</span>
                      <div className="min-w-0"><div className="font-medium text-gray-900 truncate">{flightDisplayName(item)}</div><div className="text-gray-400 truncate">{flightSubText(item)}</div></div>
                      <span className="text-gray-500 truncate">{item.action === 'create' ? '服务器无' : previewMatchedByLabel(item.matched_by) || '已关联服务器'}</span>
                      <span className={item.action === 'conflict' ? 'text-red-700 font-medium truncate' : 'text-gray-700 truncate'}>{flightChangeSummary(item, 'upload')}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          )}

          {preview?.pull && (
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold text-gray-900">待下载到本地</div>
                <div className="text-xs text-gray-500">{pullBase.length + pullItems.length} 项 / 冲突 {preview.pull.conflicts.length}</div>
              </div>
              {pullBase.length > 0 && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between border-l-4 border-blue-500 pl-2">
                    <div className="text-xs font-semibold text-gray-800">基础信息：机型 / 飞机号</div>
                    <div className="text-[11px] text-gray-400">{pullBase.length} 项</div>
                  </div>
                  <div className="border border-gray-200 rounded overflow-hidden">
                    <div className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 bg-blue-50 text-[11px] font-medium text-gray-600">
                      <span>对象</span><span>所属机型</span><span>本地当前</span><span>服务器内容</span><span>将执行</span>
                    </div>
                    {pullBase.map((item) => {
                      const conflict = item.action === 'conflict' && item.server_id !== null && item.server_id !== undefined;
                      return (
                        <div key={`pull-base-${item.entity_type}-${item.server_id}`} className="grid grid-cols-[90px_140px_150px_150px_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                          <span className="text-gray-600">{entityTypeLabel(item.entity_type)}</span>
                          <span className="text-gray-700 truncate">{baseModelName(item)}</span>
                          <span className="text-gray-500 truncate">{item.local?.name || '本地无'}</span>
                          <span className="font-medium text-gray-900 truncate">{baseObjectName(item)}</span>
                          <span className={conflict ? 'text-red-700 font-medium truncate' : 'text-gray-700 truncate'}>{baseChangeSummary(item, 'pull')}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              <div className="space-y-1">
                <div className="flex items-center justify-between border-l-4 border-slate-400 pl-2">
                  <div className="text-xs font-semibold text-gray-800">架次数据</div>
                  <div className="text-[11px] text-gray-400">{pullItems.length} 项</div>
                </div>
                <div className="border border-gray-200 rounded overflow-hidden">
                  <div className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 bg-gray-50 text-[11px] font-medium text-gray-500">
                    <span>机型</span><span>飞机号</span><span>本地当前</span><span>服务器内容</span><span>将执行</span>
                  </div>
                  {pullItems.length === 0 ? (
                    <div className="px-3 py-6 text-center text-sm text-gray-400">没有待下载架次</div>
                  ) : pullItems.map((item) => {
                    const conflict = item.action === 'conflict' && item.server_id !== null && item.server_id !== undefined;
                    const sid = String(item.server_id ?? '');
                    return (
                      <div key={`pull-${item.server_id}`} className="grid grid-cols-[120px_120px_1.2fr_1.2fr_1fr] gap-3 px-3 py-2 border-t border-gray-100 text-xs items-center">
                        <span className="text-gray-600 truncate">{item.model_name || '-'}</span>
                        <span className="text-gray-600 truncate">{item.aircraft_name || '-'}</span>
                        <div className="min-w-0 text-gray-500">{item.local ? <><div className="truncate">{item.local.name}</div><div className="text-[11px] text-gray-400">{syncStateLabel(item.local.sync_state)}</div></> : '本地无'}</div>
                        <div className="min-w-0"><div className="font-medium text-gray-900 truncate">{flightDisplayName(item)}</div><div className="text-gray-400 truncate">{flightSubText(item)}</div></div>
                        {conflict ? (
                          <div className="flex flex-wrap items-center gap-2">
                            <label className="inline-flex items-center gap-1"><input type="radio" checked={pullResolutions[sid] !== 'server'} onChange={() => onResolutionChange(sid, 'local')} />保留本地</label>
                            <label className="inline-flex items-center gap-1"><input type="radio" checked={pullResolutions[sid] === 'server'} onChange={() => onResolutionChange(sid, 'server')} />使用服务器</label>
                          </div>
                        ) : <span className="text-gray-700 truncate">{flightChangeSummary(item, 'pull')}</span>}
                      </div>
                    );
                  })}
                </div>
              </div>
            </section>
          )}

          {uploadHasConflict && <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">上传预检存在冲突，当前服务器协议不支持在此处直接覆盖服务器，请先处理冲突后再同步。</div>}
          {pullBaseHasConflict && <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">基础信息存在冲突，请先处理本地机型或飞机号改动后再拉取。</div>}
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-200 bg-gray-50">
          <button type="button" onClick={onClose} className="px-4 py-1.5 text-sm rounded border border-gray-300 text-gray-700 hover:bg-white">取消</button>
          <button type="button" disabled={!canConfirm} onClick={onConfirm} className="px-4 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50">确认执行</button>
        </div>
      </div>
    </div>
  );
}
