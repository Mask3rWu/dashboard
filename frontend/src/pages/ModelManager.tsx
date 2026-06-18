import { useState, useEffect } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import {
  listModels, updateModel, deleteModel,
  listAircraft, createAircraft, updateAircraft, deleteAircraft,
  deleteFlight, updateFlight,
  getModelColumns, updateModelColumn,
  type AircraftModel, type Aircraft, type Flight,
  type DataTypeGroup,
} from '../api';

interface Props {
  onModelsChanged: () => void;
  onNavigateToFlight: (flightId: number) => void;
  flights: Flight[];
  modelsVersion: number;
}

export default function ModelManager({ onModelsChanged, onNavigateToFlight, flights, modelsVersion }: Props) {
  const [models, setModels] = useState<AircraftModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);

  // Edit model
  const [editingModelId, setEditingModelId] = useState<number | null>(null);
  const [editModelName, setEditModelName] = useState('');

  // Delete model
  const [deletingModelId, setDeletingModelId] = useState<number | null>(null);

  // Create aircraft
  const [showAddAircraft, setShowAddAircraft] = useState(false);
  const [newSerial, setNewSerial] = useState('');

  // Edit aircraft serial
  const [editingAcId, setEditingAcId] = useState<number | null>(null);
  const [editAcSerial, setEditAcSerial] = useState('');

  // Delete aircraft
  const [deletingAcId, setDeletingAcId] = useState<number | null>(null);

  // Expanded aircraft (show flights)
  const [expandedAc, setExpandedAc] = useState<Set<number>>(new Set());

  // Flight editing
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editFlightName, setEditFlightName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);

  // Column groups
  const [columnGroups, setColumnGroups] = useState<DataTypeGroup[]>([]);

  // Batch column editing
  const [isEditingColumns, setIsEditingColumns] = useState(false);
  const [columnEditData, setColumnEditData] = useState<Record<string, { label: string; unit: string }>>({});
  const [showOriginalName, setShowOriginalName] = useState(true);

  // ─── Search & Filter state ────────────────────────────
  const [modelSearch, setModelSearch] = useState('');
  const [aircraftSearch, setAircraftSearch] = useState('');
  const [timeFilterStart, setTimeFilterStart] = useState('');
  const [timeFilterEnd, setTimeFilterEnd] = useState('');

  const loadModels = async () => {
    try {
      const data = await listModels();
      setModels(data.models);
    } catch (e) { console.error(e); }
  };

  const loadAircraft = async (modelId: number) => {
    try {
      const data = await listAircraft(modelId);
      setAircraft(data.aircraft);
    } catch (e) { console.error(e); }
  };

  useEffect(() => { loadModels(); }, []);

  // Refresh models/aircraft when external data changes (e.g. import on another tab)
  useEffect(() => {
    if (modelsVersion > 0) {
      loadModels();
      if (selectedModelId) loadAircraft(selectedModelId);
    }
  }, [modelsVersion]);

  useEffect(() => {
    if (selectedModelId) {
      loadAircraft(selectedModelId);
      getModelColumns(selectedModelId).then(d => setColumnGroups(d.data_types)).catch(() => setColumnGroups([]));
    } else {
      setAircraft([]);
      setColumnGroups([]);
    }
    // Reset search/filter/editing when switching model
    setAircraftSearch('');
    setTimeFilterStart('');
    setTimeFilterEnd('');
    setIsEditingColumns(false);
    setColumnEditData({});
    setShowOriginalName(true);
  }, [selectedModelId]);

  const refresh = () => {
    loadModels();
    if (selectedModelId) loadAircraft(selectedModelId);
    onModelsChanged();
  };

  const handleRenameModel = async (id: number) => {
    if (!editModelName.trim()) { setEditingModelId(null); return; }
    await updateModel(id, editModelName.trim());
    setEditingModelId(null);
    loadModels();
    onModelsChanged();  // refresh flight list so dropdown labels update
  };

  const handleDeleteModel = async (id: number) => {
    try {
      await deleteModel(id);
      setDeletingModelId(null);
      if (selectedModelId === id) setSelectedModelId(null);
      refresh();
    } catch (e) {
      console.error('删除机型失败:', e);
      alert('删除失败，请重试');
    }
  };

  const handleAddAircraft = async () => {
    if (!newSerial.trim() || !selectedModelId) return;
    await createAircraft(selectedModelId, newSerial.trim());
    setShowAddAircraft(false);
    setNewSerial('');
    loadAircraft(selectedModelId);
    loadModels();
    onModelsChanged();
  };

  const handleRenameAircraft = async (id: number) => {
    if (!editAcSerial.trim()) { setEditingAcId(null); return; }
    await updateAircraft(id, editAcSerial.trim());
    setEditingAcId(null);
    if (selectedModelId) loadAircraft(selectedModelId);
    refresh();
  };

  const handleDeleteAircraft = async (id: number) => {
    await deleteAircraft(id);
    setDeletingAcId(null);
    if (selectedModelId) loadAircraft(selectedModelId);
    refresh();
  };

  const handleRenameFlight = async (id: number) => {
    if (!editFlightName.trim()) { setEditingFlightId(null); return; }
    await updateFlight(id, editFlightName.trim());
    setEditingFlightId(null);
    refresh();
  };

  const handleDeleteFlight = async (id: number) => {
    await deleteFlight(id);
    setDeletingFlightId(null);
    refresh();
  };

  const startBatchEditColumns = () => {
    const data: Record<string, { label: string; unit: string }> = {};
    for (const group of columnGroups) {
      for (const col of group.columns) {
        data[`${group.data_type_key}::${col.column_name}`] = {
          label: col.display_label || col.column_name,
          unit: col.unit || '',
        };
      }
    }
    setColumnEditData(data);
    setIsEditingColumns(true);
  };

  const updateColumnEditField = (key: string, field: 'label' | 'unit', value: string) => {
    setColumnEditData((prev) => ({
      ...prev,
      [key]: { ...prev[key], [field]: value },
    }));
  };

  const cancelBatchEditColumns = () => {
    setIsEditingColumns(false);
    setColumnEditData({});
  };

  const saveAllColumns = async () => {
    if (!selectedModelId) return;
    let errorCount = 0;
    for (const [key, value] of Object.entries(columnEditData)) {
      const sepIdx = key.indexOf('::');
      const dataTypeKey = key.slice(0, sepIdx);
      const columnName = key.slice(sepIdx + 2);
      try {
        await updateModelColumn(selectedModelId, dataTypeKey, columnName, {
          display_label: value.label.trim() || undefined,
          unit: value.unit.trim() || undefined,
        });
      } catch (e) {
        errorCount++;
        console.error(`Failed to save column ${columnName}:`, e);
      }
    }
    if (errorCount > 0) {
      alert(`${errorCount} 列保存失败，请重试`);
    }
    setIsEditingColumns(false);
    setColumnEditData({});
    const d = await getModelColumns(selectedModelId);
    setColumnGroups(d.data_types);
  };

  const toggleExpand = (acId: number) => {
    setExpandedAc((prev) => {
      const next = new Set(prev);
      if (next.has(acId)) next.delete(acId);
      else next.add(acId);
      return next;
    });
  };

  const selectedModel = models.find((m) => m.id === selectedModelId);

  // ─── Search filter logic ──────────────────────────────
  const filteredModels = models.filter((m) =>
    !modelSearch.trim() || m.name.toLowerCase().includes(modelSearch.trim().toLowerCase())
  );

  const isTimeFilterActive = timeFilterStart.trim() !== '' && timeFilterEnd.trim() !== '';
  const filterStartMs = isTimeFilterActive ? Date.parse(timeFilterStart) : NaN;
  const filterEndMs = isTimeFilterActive ? Date.parse(timeFilterEnd) : NaN;

  const flightOverlapsTimeFilter = (f: Flight): boolean => {
    if (!isTimeFilterActive) return true;
    if (isNaN(filterStartMs) || isNaN(filterEndMs)) return true;  // invalid dates — show all
    if (!f.start_time || !f.end_time) return false;                // no time data — hide
    const fs = new Date(f.start_time.replace(' ', 'T')).getTime();
    const fe = new Date(f.end_time.replace(' ', 'T')).getTime();
    return fs <= filterEndMs && fe >= filterStartMs;
  };

  const getFlightsForAircraft = (acId: number): Flight[] =>
    flights.filter((f) => f.aircraft_id === acId && flightOverlapsTimeFilter(f));

  const filteredAircraft = aircraft.filter((ac) => {
    if (!aircraftSearch.trim()) return true;
    const t = aircraftSearch.trim().toLowerCase();
    return ac.serial_number.toLowerCase().includes(t) || (ac.name && ac.name.toLowerCase().includes(t));
  });

  const getAircraftStats = (acId: number) => {
    const acFlights = getFlightsForAircraft(acId);
    const hours = acFlights.reduce((s, f) => s + (f.duration_sec ?? 0), 0) / 3600;
    return { count: acFlights.length, hours };
  };

  const globalStats = {
    totalAircraft: models.reduce((s, m) => s + (m.aircraft_count ?? 0), 0),
    totalFlights: flights.length,
    totalHours: flights.reduce((s, f) => s + (f.duration_sec ?? 0), 0) / 3600,
  };

  return (
    <div className="h-full flex">
      {/* Left: Model List */}
      <aside className="w-64 shrink-0 border-r border-gray-200 overflow-y-auto bg-gray-50/50 flex flex-col">
        <div className="p-3 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-500">机型列表</span>
        </div>

        {/* Global summary stats */}
        <div className="px-3 py-2 border-b border-gray-100 bg-white space-y-1">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">总飞机数</span>
            <span className="font-semibold text-gray-700">{globalStats.totalAircraft}</span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">总架次</span>
            <span className="font-semibold text-gray-700">{globalStats.totalFlights}</span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-400">总航时</span>
            <span className="font-semibold text-gray-700">{globalStats.totalHours.toFixed(1)} 小时</span>
          </div>
        </div>

        {/* Model search */}
        <div className="px-2 pt-2 pb-1">
          <input
            type="text"
            value={modelSearch}
            onChange={(e) => setModelSearch(e.target.value)}
            placeholder="搜索机型..."
            className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredModels.map((m) => (
            <div key={m.id}>
              <div
                onClick={() => setSelectedModelId(m.id)}
                className={`rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                  selectedModelId === m.id
                    ? 'bg-blue-50 border border-blue-200'
                    : 'bg-white border border-gray-200 hover:bg-gray-100'
                }`}
              >
                {editingModelId === m.id ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="text" value={editModelName}
                      onChange={(e) => setEditModelName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleRenameModel(m.id); if (e.key === 'Escape') setEditingModelId(null); }}
                      className="flex-1 bg-white border border-blue-400 rounded px-1 py-0.5 text-xs focus:outline-none"
                      autoFocus
                    />
                    <button type="button" onClick={() => handleRenameModel(m.id)} className="text-[10px] text-blue-600 px-1 hover:text-blue-700">✓</button>
                    <button type="button" onClick={() => setEditingModelId(null)} className="text-[10px] text-gray-400 px-1 hover:text-gray-500">✕</button>
                  </div>
                ) : (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-800 truncate">{m.name}</span>
                    {/* Isolate all action buttons from parent onClick (model selection) */}
                    <div className="flex items-center gap-0.5 shrink-0 ml-1" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => { setEditingModelId(m.id); setEditModelName(m.name); }}
                        className="text-gray-300 hover:text-blue-500 p-0.5"
                        title="重命名"
                      >
                        <Pencil className="w-3 h-3" />
                      </button>
                      {deletingModelId === m.id ? (
                        <span className="text-[10px] text-red-500 whitespace-nowrap">
                          确认?{' '}
                          <button
                            type="button"
                            onClick={() => handleDeleteModel(m.id)}
                            className="text-red-600 font-bold hover:text-red-700 px-0.5"
                          >是</button>
                          {' / '}
                          <button
                            type="button"
                            onClick={() => setDeletingModelId(null)}
                            className="text-gray-400 hover:text-gray-500 px-0.5"
                          >否</button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setDeletingModelId(m.id)}
                          className="text-gray-300 hover:text-red-500 p-0.5"
                          title="删除"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  </div>
                )}
                <div className="text-[10px] text-gray-400 mt-0.5">
                  {(m.aircraft_count ?? 0)} 架飞机 · {(m.total_flights ?? 0)} 架次 · {((m.total_flight_hours ?? 0) / 3600).toFixed(1)} 小时
                </div>
              </div>
            </div>
          ))}
          {filteredModels.length === 0 && models.length > 0 && (
            <p className="text-xs text-gray-400 p-2">未找到匹配的机型</p>
          )}
          {models.length === 0 && (
            <p className="text-xs text-gray-400 p-2">暂无机型</p>
          )}
        </div>
      </aside>

      {/* Right: Aircraft & Flights */}
      <main className="flex-1 overflow-y-auto p-6">
        {!selectedModel ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择一个机型查看其飞机列表
          </div>
        ) : (
          <>
            {/* Model name header */}
            <h2 className="text-lg font-semibold text-gray-900 mb-4">{selectedModel.name}</h2>

            {/* Left-right split: aircraft | columns (60:40) */}
            <div className="flex gap-6" style={{ height: 'calc(100% - 2.5rem)' }}>
              {/* Left: Aircraft & Flights (60%) */}
              <div className="min-w-0 overflow-y-auto" style={{ flex: '6' }}>
                {/* Add aircraft button */}
                <div className="flex items-center justify-end mb-3">
                  <button
                    onClick={() => setShowAddAircraft(true)}
                    className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-500"
                  >
                    + 添加飞机
                  </button>
                </div>

                {/* Search & Filter Toolbar */}
                <div className="flex items-center gap-3 mb-4 flex-wrap">
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500 shrink-0">飞机搜索:</span>
                    <input
                      type="text"
                      value={aircraftSearch}
                      onChange={(e) => setAircraftSearch(e.target.value)}
                      placeholder="代号..."
                      className="w-36 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500 shrink-0">时间范围:</span>
                    <input
                      type="datetime-local"
                      value={timeFilterStart}
                      onChange={(e) => setTimeFilterStart(e.target.value)}
                      className="w-44 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
                    />
                    <span className="text-xs text-gray-400">~</span>
                    <input
                      type="datetime-local"
                      value={timeFilterEnd}
                      onChange={(e) => setTimeFilterEnd(e.target.value)}
                      className="w-44 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  {isTimeFilterActive && (
                    <button
                      onClick={() => { setTimeFilterStart(''); setTimeFilterEnd(''); }}
                      className="text-xs text-blue-600 hover:text-blue-500"
                    >
                      清除时间筛选
                    </button>
                  )}
                </div>
                {showAddAircraft && (
                  <div className="mb-4 p-3 bg-white border border-gray-200 rounded-lg space-y-2">
                    <input
                      type="text" value={newSerial}
                      onChange={(e) => setNewSerial(e.target.value)}
                      placeholder="飞机代号"
                      className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500"
                      onKeyDown={(e) => e.key === 'Enter' && handleAddAircraft()}
                    />
                    <div className="flex gap-1">
                      <button onClick={handleAddAircraft} className="text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-500">添加</button>
                      <button onClick={() => setShowAddAircraft(false)} className="text-xs px-3 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                    </div>
                  </div>
                )}

                {/* Aircraft list with expandable flights */}
                {aircraft.length === 0 ? (
                  <p className="text-sm text-gray-400">暂无飞机，请添加飞机代号</p>
                ) : (
                  <div className="space-y-2">
                    {filteredAircraft.map((ac) => {
                      const acFlights = getFlightsForAircraft(ac.id);
                      const isExpanded = expandedAc.has(ac.id);

                      return (
                        <div key={ac.id} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                          {/* Aircraft row */}
                          <div
                            className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors"
                            onClick={() => toggleExpand(ac.id)}
                          >
                            <div className="flex items-center gap-3">
                              <span className="text-xs text-gray-400 transition-transform"
                                style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                                ▶
                              </span>
                              {editingAcId === ac.id ? (
                                <div onClick={(e) => e.stopPropagation()}>
                                  <input
                                    type="text" value={editAcSerial}
                                    onChange={(e) => setEditAcSerial(e.target.value)}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') handleRenameAircraft(ac.id);
                                      if (e.key === 'Escape') setEditingAcId(null);
                                    }}
                                    className="bg-white border border-blue-400 rounded px-2 py-0.5 text-sm focus:outline-none w-24"
                                    autoFocus
                                  />
                                </div>
                              ) : (
                                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                                  {ac.serial_number}
                                </span>
                              )}
                              {(() => {
                                const s = getAircraftStats(ac.id);
                                return (
                                  <>
                                    <span className="text-xs text-gray-400">
                                      总架次: <span className="font-medium text-gray-600">{s.count}</span>
                                    </span>
                                    <span className="text-xs text-gray-400">
                                      总航时: <span className="font-medium text-gray-600">{s.hours.toFixed(1)}</span> 小时
                                    </span>
                                  </>
                                );
                              })()}
                            </div>
                            <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                              {editingAcId === ac.id ? (
                                <>
                                  <button type="button" onClick={() => handleRenameAircraft(ac.id)} className="text-xs px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">保存</button>
                                  <button type="button" onClick={() => setEditingAcId(null)} className="text-xs px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                                </>
                              ) : (
                                <>
                                  <button
                                    onClick={() => { setEditingAcId(ac.id); setEditAcSerial(ac.serial_number); }}
                                    className="text-xs text-gray-400 hover:text-blue-500"
                                  >
                                    重命名
                                  </button>
                                  {deletingAcId === ac.id ? (
                                    <span className="text-xs text-gray-500">
                                      确认?{' '}
                                      <button type="button" onClick={() => handleDeleteAircraft(ac.id)} className="text-red-600 font-bold hover:text-red-700">是</button>
                                      {' / '}
                                      <button type="button" onClick={() => setDeletingAcId(null)} className="text-gray-400 hover:text-gray-500">否</button>
                                    </span>
                                  ) : (
                                    <button
                                      type="button"
                                      onClick={() => setDeletingAcId(ac.id)}
                                      className="text-xs text-red-400 hover:text-red-600"
                                    >
                                      删除
                                    </button>
                                  )}
                                </>
                              )}
                            </div>
                          </div>

                          {/* Expandable flights sub-list */}
                          {isExpanded && (
                            <div className="border-t border-gray-100 bg-gray-50/50">
                              {acFlights.length === 0 ? (
                                <p className="text-xs text-gray-400 px-6 py-3">暂无飞行架次</p>
                              ) : (
                                acFlights.map((f) => (
                                  <div key={f.id} className="flex items-center justify-between px-6 py-2 border-b border-gray-100 last:border-b-0 hover:bg-white transition-colors">
                                    <div className="flex items-center gap-3 min-w-0">
                                      <span className="text-sm font-medium text-gray-700 truncate max-w-[200px]">
                                        {editingFlightId === f.id ? (
                                          <div className="flex items-center gap-1">
                                            <input
                                              type="text" value={editFlightName}
                                              onChange={(e) => setEditFlightName(e.target.value)}
                                              onKeyDown={(e) => {
                                                if (e.key === 'Enter') handleRenameFlight(f.id);
                                                if (e.key === 'Escape') setEditingFlightId(null);
                                              }}
                                              className="bg-white border border-blue-400 rounded px-2 py-0.5 text-xs focus:outline-none w-36"
                                              autoFocus
                                            />
                                            <button type="button" onClick={() => handleRenameFlight(f.id)} className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">✓</button>
                                            <button type="button" onClick={() => setEditingFlightId(null)} className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">✕</button>
                                          </div>
                                        ) : (
                                          <span className="flex items-center gap-1 group">
                                            {f.name}
                                            <button
                                              onClick={() => { setEditingFlightId(f.id); setEditFlightName(f.name); }}
                                              className="text-gray-300 hover:text-blue-500 opacity-0 group-hover:opacity-100 text-[10px]"
                                            ><Pencil className="w-3 h-3" /></button>
                                          </span>
                                        )}
                                      </span>
                                      <span className="text-xs text-gray-400 font-mono">{f.session_key}</span>
                                      {f.duration_sec != null && (
                                        <span className="text-xs text-gray-400">{Math.round(f.duration_sec / 60)}分钟</span>
                                      )}
                                      <span className="text-xs text-gray-400">
                                        {f.start_time && `${f.start_time}${f.end_time ? ` ~ ${f.end_time.split(' ').pop()}` : ''}`}
                                      </span>
                                    </div>
                                    <div className="flex items-center gap-2 shrink-0">
                                      <button
                                        onClick={() => onNavigateToFlight(f.id)}
                                        className="text-xs text-blue-600 hover:text-blue-500 font-medium"
                                      >
                                        分析 →
                                      </button>
                                      {deletingFlightId === f.id ? (
                                        <span className="text-xs text-gray-500" onClick={(e) => e.stopPropagation()}>
                                          确认?{' '}
                                          <button type="button" onClick={() => handleDeleteFlight(f.id)} className="text-red-600 font-bold hover:text-red-700">是</button>
                                          {' / '}
                                          <button type="button" onClick={() => setDeletingFlightId(null)} className="text-gray-400 hover:text-gray-500">否</button>
                                        </span>
                                      ) : (
                                        <button
                                          type="button"
                                          onClick={() => setDeletingFlightId(f.id)}
                                          className="text-xs text-red-400 hover:text-red-600"
                                        >
                                          删除
                                        </button>
                                      )}
                                    </div>
                                  </div>
                                ))
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Right: Column Definitions (40%) */}
              <div className="min-w-0 overflow-y-auto border-l border-gray-200 pl-6" style={{ flex: '4' }}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-gray-700">
                    列定义 ({columnGroups.reduce((s, g) => s + g.columns.length, 0)} 列)
                  </h3>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1 text-[10px] text-gray-400 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={showOriginalName}
                        onChange={(e) => setShowOriginalName(e.target.checked)}
                        className="w-3 h-3"
                      />
                      原字段
                    </label>
                    {columnGroups.length > 0 && (
                    !isEditingColumns ? (
                      <button
                        onClick={startBatchEditColumns}
                        className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-500"
                      >
                        编辑列定义
                      </button>
                    ) : (
                      <div className="flex items-center gap-2">
                        <button
                          onClick={saveAllColumns}
                          className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-500"
                        >
                          保存全部
                        </button>
                        <button
                          onClick={cancelBatchEditColumns}
                          className="px-3 py-1.5 text-xs bg-gray-200 text-gray-600 rounded-lg hover:bg-gray-300"
                        >
                          取消
                        </button>
                      </div>
                    )
                  )}
                  </div>
                </div>

                {columnGroups.length === 0 ? (
                  <p className="text-xs text-gray-400">暂无列定义</p>
                ) : (
                  <div className="space-y-3">
                    {columnGroups.map((group) => (
                      <div key={group.data_type_key} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                        <div className="px-3 py-2 bg-gray-50 text-xs font-medium text-gray-600">
                          {group.label}
                        </div>
                        <div className="divide-y divide-gray-100">
                          {group.columns.map((col) => {
                            const editKey = `${group.data_type_key}::${col.column_name}`;
                            const editData = columnEditData[editKey];
                            return (
                              <div key={col.column_name} className="flex items-center px-3 py-1.5 text-xs gap-1">
                                <span className="text-gray-400 w-6 shrink-0">{col.ordinal}</span>
                                {isEditingColumns && editData ? (
                                  <>
                                    <input
                                      type="text"
                                      value={editData.label}
                                      onChange={(e) => updateColumnEditField(editKey, 'label', e.target.value)}
                                      className="flex-1 bg-white border border-blue-400 rounded px-1.5 py-0.5 text-xs focus:outline-none min-w-0"
                                      placeholder="显示名称"
                                    />
                                    {showOriginalName && (
                                      <span className="text-gray-400 font-mono text-xs shrink-0 truncate" style={{ width: '4.5rem' }} title={col.column_name}>
                                        {col.column_name}
                                      </span>
                                    )}
                                    <input
                                      type="text"
                                      value={editData.unit}
                                      onChange={(e) => updateColumnEditField(editKey, 'unit', e.target.value)}
                                      className="bg-white border border-blue-400 rounded px-1.5 py-0.5 text-xs focus:outline-none"
                                      style={{ width: '3rem' }}
                                      placeholder="单位"
                                    />
                                  </>
                                ) : (
                                  <>
                                    <span className="flex-1 text-gray-700 truncate">
                                      {col.display_label || col.column_name}
                                    </span>
                                    {showOriginalName && (
                                      <span className="text-gray-400 font-mono text-xs shrink-0 truncate" style={{ width: '4.5rem' }} title={col.column_name}>
                                        {col.column_name}
                                      </span>
                                    )}
                                    <span className="text-gray-400 shrink-0 text-right" style={{ width: '3rem' }}>
                                      {col.unit || '-'}
                                    </span>
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
            </div>
          </>
        )}
      </main>
    </div>
  );
}
