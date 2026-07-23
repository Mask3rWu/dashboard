import { Pencil } from 'lucide-react';
import type { Aircraft } from '../../api/models';
import type { Flight, FlightRecordFields, RawFileItem } from '../../api/flights';
import { deleteActionLabel } from '../../syncStatus';
import FlightRecordForm from '../flights/FlightRecordForm';
import { formatDurationMinutes } from '../flights/recordFields';

type RawWarning = { file?: string; path?: string; error: string };

interface Props {
  aircraft: Aircraft[];
  filteredAircraft: Aircraft[];
  expandedAircraftIds: Set<number>;
  editingAircraftId: number | null;
  editAircraftName: string;
  deletingAircraftId: number | null;
  editingFlightId: number | null;
  editFlightName: string;
  deletingFlightId: number | null;
  editingRecordFlightId: number | null;
  recordForm: FlightRecordFields;
  savingRecord: boolean;
  expandedRawFlightId: number | null;
  rawFilesByFlight: Record<number, RawFileItem[]>;
  rawWarningsByFlight: Record<number, RawWarning[]>;
  loadingRawFlightId: number | null;
  canDeleteAircraft: boolean;
  canDeleteFlights: boolean;
  serverOnline: boolean;
  readOnly?: boolean;
  selectable?: boolean;
  selectedFlightIds?: Set<number>;
  onSelectFlight?: (flightId: number) => void;
  getFlightsForAircraft: (aircraftId: number) => Flight[];
  getAircraftStats: (aircraftId: number) => { count: number; hours: number };
  onToggleAircraft: (aircraftId: number) => void;
  onStartRenameAircraft: (aircraft: Aircraft) => void;
  onAircraftNameChange: (value: string) => void;
  onRenameAircraft: (aircraftId: number) => void;
  onCancelRenameAircraft: () => void;
  onRequestDeleteAircraft: (aircraftId: number) => void;
  onDeleteAircraft: (aircraft: Aircraft) => void;
  onCancelDeleteAircraft: () => void;
  onStartRenameFlight: (flight: Flight) => void;
  onFlightNameChange: (value: string) => void;
  onRenameFlight: (flightId: number) => void;
  onCancelRenameFlight: () => void;
  onEditRecord: (flight: Flight) => void;
  onRecordChange: (patch: Partial<FlightRecordFields>) => void;
  onSaveRecord: (flightId: number) => void;
  onCancelEditRecord: () => void;
  onToggleRawFiles: (flightId: number) => void;
  onOpenRawFolder: (flightId: number) => void;
  onNavigateToFlight: (flightId: number) => void;
  onRequestDeleteFlight: (flightId: number) => void;
  onDeleteFlight: (flight: Flight) => void;
  onCancelDeleteFlight: () => void;
}

function syncStateLabel(state?: string | null) {
  const labels: Record<string, string> = {
    local_only: '本地',
    pending_upload: '本地',
    syncing: '同步中',
    synced: '已同步',
    dirty: '待更新',
    upload_failed: '上传失败',
    conflict: '冲突',
    server_cache: '服务器缓存',
    server_remote: '服务器',
    server_deleted: '服务器已删',
  };
  return labels[state || ''] || state || '未标记';
}

function syncStateClass(state?: string | null) {
  if (state === 'local_only' || state === 'pending_upload' || state === 'dirty') return 'bg-amber-50 text-amber-700 border-amber-200';
  if (state === 'upload_failed' || state === 'conflict') return 'bg-red-50 text-red-700 border-red-200';
  if (state === 'synced' || state === 'server_cache') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  return 'bg-gray-50 text-gray-600 border-gray-200';
}

function formatKgValue(value: string | number | null | undefined): string {
  if (value == null || String(value).trim() === '') return '';
  const text = String(value).trim();
  return /kg$/i.test(text) ? text : `${text}kg`;
}

function recordSummary(flight: Flight) {
  const parts = [
    flight.record_location ? `地点 ${flight.record_location}` : '',
    flight.record_weather ? `天气 ${flight.record_weather}` : '',
    flight.record_total_duration_min != null ? `总时长 ${formatDurationMinutes(flight.record_total_duration_min)}` : '',
    flight.record_payload ? `载荷 ${formatKgValue(flight.record_payload)}` : '',
    flight.record_takeoff_weight != null ? `起飞 ${flight.record_takeoff_weight}kg` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : '未填写记录';
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function AircraftList(props: Props) {
  const {
    aircraft, filteredAircraft, expandedAircraftIds, editingAircraftId, editAircraftName,
    deletingAircraftId, editingFlightId, editFlightName, deletingFlightId,
    editingRecordFlightId, recordForm, savingRecord, expandedRawFlightId,
    rawFilesByFlight, rawWarningsByFlight, loadingRawFlightId, canDeleteAircraft,
    canDeleteFlights, serverOnline, getFlightsForAircraft, getAircraftStats,
    onToggleAircraft, onStartRenameAircraft, onAircraftNameChange, onRenameAircraft,
    onCancelRenameAircraft, onRequestDeleteAircraft, onDeleteAircraft,
    onCancelDeleteAircraft, onStartRenameFlight, onFlightNameChange, onRenameFlight,
    onCancelRenameFlight, onEditRecord, onRecordChange, onSaveRecord,
    onCancelEditRecord, onToggleRawFiles, onOpenRawFolder, onNavigateToFlight,
    onRequestDeleteFlight, onDeleteFlight, onCancelDeleteFlight,
  } = props;
  const readOnly = props.readOnly ?? false;
  const selectable = props.selectable ?? false;
  const selectedFlightIds = props.selectedFlightIds ?? new Set<number>();

  if (aircraft.length === 0) {
    return <p className="text-sm text-gray-400">{readOnly ? '未找到符合条件的架次' : '暂无飞机，请添加飞机代号'}</p>;
  }

  return (
    <div className="space-y-2">
      {filteredAircraft.map((item) => {
        const flights = getFlightsForAircraft(item.id);
        const isExpanded = expandedAircraftIds.has(item.id);
        const stats = getAircraftStats(item.id);
        return (
          <div key={item.id} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors" onClick={() => onToggleAircraft(item.id)}>
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400 transition-transform" style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>▶</span>
                {!readOnly && editingAircraftId === item.id ? (
                  <div onClick={(event) => event.stopPropagation()}>
                    <input
                      type="text"
                      value={editAircraftName}
                      onChange={(event) => onAircraftNameChange(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') onRenameAircraft(item.id);
                        if (event.key === 'Escape') onCancelRenameAircraft();
                      }}
                      className="bg-white border border-blue-400 rounded px-2 py-0.5 text-sm focus:outline-none w-24"
                      autoFocus
                    />
                  </div>
                ) : (
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">{item.name}</span>
                )}
                <span className="text-xs text-gray-400">总架次: <span className="font-medium text-gray-600">{stats.count}</span></span>
                <span className="text-xs text-gray-400">总航时: <span className="font-medium text-gray-600">{stats.hours.toFixed(1)}</span> 小时</span>
              </div>
              {!readOnly && <div className="flex items-center gap-2" onClick={(event) => event.stopPropagation()}>
                {editingAircraftId === item.id ? (
                  <>
                    <button type="button" onClick={() => onRenameAircraft(item.id)} className="text-xs px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">保存</button>
                    <button type="button" onClick={onCancelRenameAircraft} className="text-xs px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                  </>
                ) : (
                  <>
                    <button onClick={() => onStartRenameAircraft(item)} className="text-xs text-gray-400 hover:text-blue-500">重命名</button>
                    {canDeleteAircraft && deletingAircraftId === item.id ? (
                      <span className="text-xs text-gray-500">
                        {deleteActionLabel(item, serverOnline)}?{' '}
                        <button type="button" onClick={() => onDeleteAircraft(item)} className="text-red-600 font-bold hover:text-red-700">是</button>{' / '}
                        <button type="button" onClick={onCancelDeleteAircraft} className="text-gray-400 hover:text-gray-500">否</button>
                      </span>
                    ) : canDeleteAircraft ? (
                      <button type="button" onClick={() => onRequestDeleteAircraft(item.id)} className="text-xs text-red-400 hover:text-red-600">删除</button>
                    ) : (
                      <span className="text-xs text-gray-300" title="当前环境或登录状态无删除飞机权限">删除</span>
                    )}
                  </>
                )}
              </div>}
            </div>

            {isExpanded && (
              <div className="border-t border-gray-100 bg-gray-50/50">
                {flights.length === 0 ? (
                  <p className="text-xs text-gray-400 px-6 py-3">暂无飞行架次</p>
                ) : flights.map((flight) => (
                  <div key={flight.id} className="px-6 py-2 border-b border-gray-100 last:border-b-0 hover:bg-white transition-colors">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        {selectable && (
                          <input
                            type="checkbox"
                            checked={!flight.downloaded && selectedFlightIds.has(flight.id)}
                            disabled={!!flight.downloaded}
                            onChange={() => {
                              if (!flight.downloaded) props.onSelectFlight?.(flight.id);
                            }}
                            aria-label={`选择架次 ${flight.name}`}
                            title={flight.downloaded ? '本地已有，无需重复下载' : '选择下载到本地'}
                            className="w-4 h-4 accent-blue-600 shrink-0"
                          />
                        )}
                        <span className="text-sm font-medium text-gray-700 truncate max-w-[200px]">
                          {!readOnly && editingFlightId === flight.id ? (
                            <div className="flex items-center gap-1">
                              <input
                                type="text"
                                value={editFlightName}
                                onChange={(event) => onFlightNameChange(event.target.value)}
                                onKeyDown={(event) => {
                                  if (event.key === 'Enter') onRenameFlight(flight.id);
                                  if (event.key === 'Escape') onCancelRenameFlight();
                                }}
                                className="bg-white border border-blue-400 rounded px-2 py-0.5 text-xs focus:outline-none w-36"
                                autoFocus
                              />
                              <button type="button" onClick={() => onRenameFlight(flight.id)} className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">✓</button>
                              <button type="button" onClick={onCancelRenameFlight} className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">✕</button>
                            </div>
                          ) : (
                            <span className="flex items-center gap-1 group">
                              {flight.name}
                              {!readOnly && <button onClick={() => onStartRenameFlight(flight)} className="text-gray-300 hover:text-blue-500 opacity-0 group-hover:opacity-100 text-[10px]"><Pencil className="w-3 h-3" /></button>}
                            </span>
                          )}
                        </span>
                        <span className="text-xs text-gray-400 font-mono">{flight.session_key}</span>
                        {flight.duration_sec != null && <span className="text-xs text-gray-400">解析 {Math.round(flight.duration_sec / 60)}分钟</span>}
                        {flight.record_total_duration_min != null && <span className="text-xs text-gray-500">总时长 {formatDurationMinutes(flight.record_total_duration_min)}</span>}
                        <span className="text-xs text-gray-400">原始文件 {flight.raw_file_count ?? 0}</span>
                        <span className={`text-[10px] px-2 py-0.5 rounded border ${syncStateClass(flight.sync_state)}`}>{syncStateLabel(flight.sync_state)}</span>
                        {readOnly && flight.downloaded && <span className="text-[10px] px-2 py-0.5 rounded border bg-emerald-50 text-emerald-700 border-emerald-200">本地已有</span>}
                        <span className="text-xs text-gray-400">{flight.start_time && `${flight.start_time}${flight.end_time ? ` ~ ${flight.end_time.split(' ').pop()}` : ''}`}</span>
                      </div>
                      {!readOnly && <div className="flex items-center gap-2 shrink-0">
                        <button type="button" onClick={() => onEditRecord(flight)} className="text-xs text-gray-500 hover:text-blue-600">编辑记录</button>
                        <button type="button" onClick={() => onToggleRawFiles(flight.id)} className="text-xs text-gray-500 hover:text-blue-600">原始文件</button>
                        <button onClick={() => onNavigateToFlight(flight.id)} className="text-xs text-blue-600 hover:text-blue-500 font-medium">分析 →</button>
                        {canDeleteFlights && deletingFlightId === flight.id ? (
                          <span className="text-xs text-gray-500" onClick={(event) => event.stopPropagation()}>
                            {deleteActionLabel(flight, serverOnline)}?{' '}
                            <button type="button" onClick={() => onDeleteFlight(flight)} className="text-red-600 font-bold hover:text-red-700">是</button>{' / '}
                            <button type="button" onClick={onCancelDeleteFlight} className="text-gray-400 hover:text-gray-500">否</button>
                          </span>
                        ) : canDeleteFlights ? (
                          <button type="button" onClick={() => onRequestDeleteFlight(flight.id)} className="text-xs text-red-400 hover:text-red-600">删除</button>
                        ) : (
                          <span className="text-xs text-gray-300" title="当前环境或登录状态无删除架次权限">删除</span>
                        )}
                      </div>}
                    </div>
                    <div className="mt-1 text-xs text-gray-500 truncate">
                      {recordSummary(flight)}
                      {flight.record_note && <span className="text-gray-400"> · 备注 {flight.record_note}</span>}
                      {(flight.raw_warnings?.length ?? 0) > 0 && <span className="text-amber-600"> · 原始文件转存 warning {flight.raw_warnings!.length}</span>}
                    </div>
                    {editingRecordFlightId === flight.id && (
                      <div className="mt-3 rounded border border-blue-100 bg-blue-50/40 p-3 space-y-3">
                        <FlightRecordForm value={recordForm} onChange={onRecordChange} />
                        <div className="flex items-center gap-2 justify-end">
                          <button type="button" onClick={() => onSaveRecord(flight.id)} disabled={savingRecord} className="px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50">保存记录</button>
                          <button type="button" onClick={onCancelEditRecord} disabled={savingRecord} className="px-3 py-1 text-xs bg-gray-200 text-gray-600 rounded hover:bg-gray-300 disabled:opacity-50">取消</button>
                        </div>
                      </div>
                    )}
                    {expandedRawFlightId === flight.id && (
                      <div className="mt-3 rounded border border-gray-200 bg-white p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium text-gray-600">原始文件清单</span>
                          <button type="button" onClick={() => onOpenRawFolder(flight.id)} className="text-xs text-blue-600 hover:text-blue-500">打开目录</button>
                        </div>
                        {loadingRawFlightId === flight.id ? (
                          <p className="text-xs text-gray-400">加载中...</p>
                        ) : (
                          <>
                            {(rawWarningsByFlight[flight.id]?.length ?? flight.raw_warnings?.length ?? 0) > 0 && (
                              <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 space-y-1">
                                {(rawWarningsByFlight[flight.id] ?? flight.raw_warnings ?? []).map((warning, index) => (
                                  <div key={`${warning.file ?? 'warning'}-${index}`} className="text-xs text-amber-700">{warning.file ? `${warning.file}: ` : ''}{warning.error}</div>
                                ))}
                              </div>
                            )}
                            {(rawFilesByFlight[flight.id]?.length ?? 0) === 0 ? (
                              <p className="text-xs text-gray-400">暂无已转存原始文件</p>
                            ) : (
                              <div className="divide-y divide-gray-100">
                                {rawFilesByFlight[flight.id].map((raw) => (
                                  <div key={raw.id} className="py-1.5 text-xs">
                                    <div className="flex items-center justify-between gap-3">
                                      <span className="font-medium text-gray-700 truncate" title={raw.original_rel_path}>{raw.original_name}</span>
                                      <span className="text-gray-400 shrink-0">{formatBytes(raw.size_bytes)}</span>
                                    </div>
                                    <div className="mt-0.5 flex items-center gap-2 text-[10px] text-gray-400 min-w-0">
                                      {raw.data_type_key && <span>{raw.data_type_key}</span>}
                                      <span className="font-mono truncate" title={raw.sha256}>{raw.sha256.slice(0, 16)}...</span>
                                      <span className="truncate" title={raw.storage_rel_path}>{raw.storage_rel_path}</span>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
