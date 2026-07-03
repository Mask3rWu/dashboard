import { useState, useEffect } from 'react';
import { Pencil, Trash2, Download, Upload } from 'lucide-react';
import {
  listModels, updateModel, deleteModel,
  listAircraft, createAircraft, updateAircraft, deleteAircraft,
  deleteFlight, updateFlight, updateFlightRecord,
  getRawFiles, getRawManifest,
  getModelColumns, updateModelColumn, updateModelDataTypeLabel,
  exportModel, importModel,
  type AircraftModel, type Aircraft, type Flight,
  type DataTypeGroup, type FlightRecordFields, type RawFileItem,
} from '../api';

interface Props {
  onModelsChanged: () => void;
  onNavigateToFlight: (flightId: number) => void;
  flights: Flight[];
  modelsVersion: number;
  capabilities: string[];
}

function emptyRecord(): FlightRecordFields {
  return {
    record_daily_duration_min: null,
    record_batch_name: '',
    record_location: '',
    record_payload: '',
    record_weather: '',
    record_fuel_amount: null,
    record_takeoff_weight: null,
    record_altitude: null,
    record_wind_speed: null,
    record_note: '',
  };
}

function recordFromFlight(f: Flight): FlightRecordFields {
  return {
    record_daily_duration_min: f.record_daily_duration_min ?? null,
    record_batch_name: f.record_batch_name ?? '',
    record_location: f.record_location ?? '',
    record_payload: f.record_payload ?? '',
    record_weather: f.record_weather ?? '',
    record_fuel_amount: f.record_fuel_amount ?? null,
    record_takeoff_weight: f.record_takeoff_weight ?? null,
    record_altitude: f.record_altitude ?? null,
    record_wind_speed: f.record_wind_speed ?? null,
    record_note: f.record_note ?? '',
  };
}

function parseNumberInput(value: string): number | null {
  if (value.trim() === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function recordSummary(f: Flight) {
  const parts = [
    f.record_batch_name ? `批次 ${f.record_batch_name}` : '',
    f.record_location ? `地点 ${f.record_location}` : '',
    f.record_weather ? `天气 ${f.record_weather}` : '',
    f.record_payload ? `载荷 ${f.record_payload}` : '',
    f.record_takeoff_weight != null ? `起飞 ${f.record_takeoff_weight}kg` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : '未填写记录';
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function RecordField({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string;
  value: string | number | null | undefined;
  onChange: (value: string) => void;
  type?: 'text' | 'number';
}) {
  return (
    <label className="space-y-1">
      <span className="block text-[10px] text-gray-500">{label}</span>
      <input
        type={type}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
      />
    </label>
  );
}

export default function ModelManager({ onModelsChanged, onNavigateToFlight, flights, modelsVersion, capabilities }: Props) {
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
  const [editingRecordFlightId, setEditingRecordFlightId] = useState<number | null>(null);
  const [recordForm, setRecordForm] = useState<FlightRecordFields>(emptyRecord());
  const [savingRecord, setSavingRecord] = useState(false);
  const [expandedRawFlightId, setExpandedRawFlightId] = useState<number | null>(null);
  const [rawFilesByFlight, setRawFilesByFlight] = useState<Record<number, RawFileItem[]>>({});
  const [rawWarningsByFlight, setRawWarningsByFlight] = useState<Record<number, { file?: string; path?: string; error: string }[]>>({});
  const [loadingRawFlightId, setLoadingRawFlightId] = useState<number | null>(null);

  // Column groups
  const [columnGroups, setColumnGroups] = useState<DataTypeGroup[]>([]);

  // Batch column editing
  const [isEditingColumns, setIsEditingColumns] = useState(false);
  const [columnEditData, setColumnEditData] = useState<Record<string, { label: string; unit: string }>>({});
  const [showOriginalName, setShowOriginalName] = useState(true);

  // Group label editing
  const [editingGroupLabel, setEditingGroupLabel] = useState<string | null>(null);
  const [editGroupLabelValue, setEditGroupLabelValue] = useState('');

  // ─── Import / Export ───────────────────────────────────
  const [showImportModal, setShowImportModal] = useState(false);
  const [importData, setImportData] = useState<any>(null);
  const [importName, setImportName] = useState('');
  const [importError, setImportError] = useState('');
  const canDeleteModels = capabilities.includes('delete_models');
  const canDeleteAircraft = capabilities.includes('delete_aircraft');
  const canDeleteFlights = capabilities.includes('delete_flights');

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      if (!data.version || !data.model) {
        setImportError('无效的导出文件格式');
        return;
      }
      setImportData(data);
      setImportName(data.model.name || '');
      setImportError('');
      setShowImportModal(true);
    } catch {
      setImportError('无法解析文件，请选择有效的 JSON 文件');
    }
    e.target.value = ''; // reset so same file can be re-selected
  };

  const handleImportConfirm = async () => {
    if (!importData || !importName.trim()) return;
    try {
      await importModel(importName.trim(), importData);
      setShowImportModal(false);
      setImportData(null);
      loadModels();
      onModelsChanged();
    } catch (err: any) {
      setImportError(err.message || '导入失败');
    }
  };

  // ─── Search & Filter state ────────────────────────────
  const [modelSearch, setModelSearch] = useState('');
  const [aircraftSearch, setAircraftSearch] = useState('');
  const [timeFilterStart, setTimeFilterStart] = useState('');
  const [timeFilterEnd, setTimeFilterEnd] = useState('');
  const [batchFilter, setBatchFilter] = useState('');
  const [locationFilter, setLocationFilter] = useState('');
  const [weatherFilter, setWeatherFilter] = useState('');
  const [payloadFilter, setPayloadFilter] = useState('');

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

  const saveGroupLabel = async (dataTypeKey: string) => {
    if (!selectedModelId || !editGroupLabelValue.trim()) return;
    try {
      await updateModelDataTypeLabel(selectedModelId, dataTypeKey, editGroupLabelValue.trim());
      setEditingGroupLabel(null);
      const d = await getModelColumns(selectedModelId);
      setColumnGroups(d.data_types);
    } catch (e: any) {
      alert('保存失败: ' + (e.message || e));
    }
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

  const flightMatchesRecordFilters = (f: Flight): boolean => {
    const filters: [string, string | undefined][] = [
      [batchFilter, f.record_batch_name],
      [locationFilter, f.record_location],
      [weatherFilter, f.record_weather],
      [payloadFilter, f.record_payload],
    ];
    return filters.every(([needle, value]) => {
      if (!needle.trim()) return true;
      return (value ?? '').toLowerCase().includes(needle.trim().toLowerCase());
    });
  };

  const getFlightsForAircraft = (acId: number): Flight[] =>
    flights.filter((f) => f.aircraft_id === acId && flightOverlapsTimeFilter(f) && flightMatchesRecordFilters(f));

  const filteredAircraft = aircraft.filter((ac) => {
    if (!aircraftSearch.trim()) return true;
    const t = aircraftSearch.trim().toLowerCase();
    return ac.name.toLowerCase().includes(t);
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

  const startEditRecord = (f: Flight) => {
    setEditingRecordFlightId(f.id);
    setRecordForm(recordFromFlight(f));
  };

  const updateRecordForm = (patch: Partial<FlightRecordFields>) => {
    setRecordForm((prev) => ({ ...prev, ...patch }));
  };

  const saveRecord = async (flightId: number) => {
    setSavingRecord(true);
    try {
      await updateFlightRecord(flightId, recordForm);
      setEditingRecordFlightId(null);
      onModelsChanged();
    } finally {
      setSavingRecord(false);
    }
  };

  const clearRecordFilters = () => {
    setBatchFilter('');
    setLocationFilter('');
    setWeatherFilter('');
    setPayloadFilter('');
  };

  const toggleRawFiles = async (flightId: number) => {
    if (expandedRawFlightId === flightId) {
      setExpandedRawFlightId(null);
      return;
    }
    setExpandedRawFlightId(flightId);
    if (rawFilesByFlight[flightId]) return;

    setLoadingRawFlightId(flightId);
    try {
      const data = await getRawFiles(flightId);
      setRawFilesByFlight((prev) => ({ ...prev, [flightId]: data.files }));
      setRawWarningsByFlight((prev) => ({ ...prev, [flightId]: data.warnings }));
    } catch (e: any) {
      setRawWarningsByFlight((prev) => ({
        ...prev,
        [flightId]: [{ error: e.message || String(e) }],
      }));
    } finally {
      setLoadingRawFlightId(null);
    }
  };

  const createRawManifest = async (flightId: number) => {
    try {
      const manifest = await getRawManifest(flightId);
      alert(`原始文件清单已生成:\n${manifest.manifest_path}`);
    } catch (e: any) {
      alert('生成清单失败: ' + (e.message || e));
    }
  };

  const recordFiltersActive = [batchFilter, locationFilter, weatherFilter, payloadFilter].some((v) => v.trim());

  return (
    <div className="h-full flex">
      {/* Left: Model List */}
      <aside className="w-64 shrink-0 border-r border-gray-200 overflow-y-auto bg-gray-50/50 flex flex-col">
        <div className="p-3 border-b border-gray-200 flex items-center justify-between">
          <span className="text-xs font-medium text-gray-500">机型列表</span>
          <label className="cursor-pointer text-gray-400 hover:text-blue-500" title="导入机型配置">
            <Upload className="w-3.5 h-3.5" />
            <input type="file" accept=".json" onChange={handleImportFile} className="hidden" />
          </label>
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
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            const r = await exportModel(m.id);
                            alert(`已导出到:\n${r.path}`);
                          } catch (e: any) { alert('导出失败: ' + (e.message || e)); }
                        }}
                        className="text-gray-300 hover:text-green-500 p-0.5"
                        title="导出配置"
                      >
                        <Download className="w-3 h-3" />
                      </button>
                      {canDeleteModels && deletingModelId === m.id ? (
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
                      ) : canDeleteModels ? (
                        <button
                          type="button"
                          onClick={() => setDeletingModelId(m.id)}
                          className="text-gray-300 hover:text-red-500 p-0.5"
                          title="删除"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      ) : (
                        <span className="text-gray-200 p-0.5" title="当前环境或登录状态无删除机型权限">
                          <Trash2 className="w-3 h-3" />
                        </span>
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
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500 shrink-0">批次:</span>
                    <input
                      type="text"
                      value={batchFilter}
                      onChange={(e) => setBatchFilter(e.target.value)}
                      className="w-24 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500 shrink-0">地点:</span>
                    <input
                      type="text"
                      value={locationFilter}
                      onChange={(e) => setLocationFilter(e.target.value)}
                      className="w-24 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500 shrink-0">天气:</span>
                    <input
                      type="text"
                      value={weatherFilter}
                      onChange={(e) => setWeatherFilter(e.target.value)}
                      className="w-24 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500 shrink-0">载荷:</span>
                    <input
                      type="text"
                      value={payloadFilter}
                      onChange={(e) => setPayloadFilter(e.target.value)}
                      className="w-24 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  {recordFiltersActive && (
                    <button
                      onClick={clearRecordFilters}
                      className="text-xs text-blue-600 hover:text-blue-500"
                    >
                      清除记录筛选
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
                                  {ac.name}
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
                                    onClick={() => { setEditingAcId(ac.id); setEditAcSerial(ac.name); }}
                                    className="text-xs text-gray-400 hover:text-blue-500"
                                  >
                                    重命名
                                  </button>
                                  {canDeleteAircraft && deletingAcId === ac.id ? (
                                    <span className="text-xs text-gray-500">
                                      确认?{' '}
                                      <button type="button" onClick={() => handleDeleteAircraft(ac.id)} className="text-red-600 font-bold hover:text-red-700">是</button>
                                      {' / '}
                                      <button type="button" onClick={() => setDeletingAcId(null)} className="text-gray-400 hover:text-gray-500">否</button>
                                    </span>
                                  ) : canDeleteAircraft ? (
                                    <button
                                      type="button"
                                      onClick={() => setDeletingAcId(ac.id)}
                                      className="text-xs text-red-400 hover:text-red-600"
                                    >
                                      删除
                                    </button>
                                  ) : (
                                    <span className="text-xs text-gray-300" title="当前环境或登录状态无删除飞机权限">删除</span>
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
                                  <div key={f.id} className="px-6 py-2 border-b border-gray-100 last:border-b-0 hover:bg-white transition-colors">
                                    <div className="flex items-center justify-between gap-3">
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
                                          <span className="text-xs text-gray-400">解析 {Math.round(f.duration_sec / 60)}分钟</span>
                                        )}
                                        {f.record_daily_duration_min != null && (
                                          <span className="text-xs text-gray-500">记录 {f.record_daily_duration_min}分钟</span>
                                        )}
                                        <span className="text-xs text-gray-400">
                                          原始文件 {f.raw_file_count ?? 0}
                                        </span>
                                        <span className="text-xs text-gray-400">
                                          {f.start_time && `${f.start_time}${f.end_time ? ` ~ ${f.end_time.split(' ').pop()}` : ''}`}
                                        </span>
                                      </div>
                                      <div className="flex items-center gap-2 shrink-0">
                                        <button
                                          type="button"
                                          onClick={() => startEditRecord(f)}
                                          className="text-xs text-gray-500 hover:text-blue-600"
                                        >
                                          编辑记录
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => toggleRawFiles(f.id)}
                                          className="text-xs text-gray-500 hover:text-blue-600"
                                        >
                                          原始文件
                                        </button>
                                        <button
                                          onClick={() => onNavigateToFlight(f.id)}
                                          className="text-xs text-blue-600 hover:text-blue-500 font-medium"
                                        >
                                          分析 →
                                        </button>
                                        {canDeleteFlights && deletingFlightId === f.id ? (
                                          <span className="text-xs text-gray-500" onClick={(e) => e.stopPropagation()}>
                                            确认?{' '}
                                            <button type="button" onClick={() => handleDeleteFlight(f.id)} className="text-red-600 font-bold hover:text-red-700">是</button>
                                            {' / '}
                                            <button type="button" onClick={() => setDeletingFlightId(null)} className="text-gray-400 hover:text-gray-500">否</button>
                                          </span>
                                        ) : canDeleteFlights ? (
                                          <button
                                            type="button"
                                            onClick={() => setDeletingFlightId(f.id)}
                                            className="text-xs text-red-400 hover:text-red-600"
                                          >
                                            删除
                                          </button>
                                        ) : (
                                          <span className="text-xs text-gray-300" title="当前环境或登录状态无删除架次权限">删除</span>
                                        )}
                                      </div>
                                    </div>
                                    <div className="mt-1 text-xs text-gray-500 truncate">
                                      {recordSummary(f)}
                                      {f.record_note && <span className="text-gray-400"> · 备注 {f.record_note}</span>}
                                      {(f.raw_warnings?.length ?? 0) > 0 && (
                                        <span className="text-amber-600"> · 原始文件转存 warning {f.raw_warnings!.length}</span>
                                      )}
                                    </div>
                                    {editingRecordFlightId === f.id && (
                                      <div className="mt-3 rounded border border-blue-100 bg-blue-50/40 p-3 space-y-3">
                                        <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
                                          <RecordField label="单日飞行时长（分钟）" type="number" value={recordForm.record_daily_duration_min} onChange={(v) => updateRecordForm({ record_daily_duration_min: parseNumberInput(v) })} />
                                          <RecordField label="批次" value={recordForm.record_batch_name} onChange={(v) => updateRecordForm({ record_batch_name: v })} />
                                          <RecordField label="地点" value={recordForm.record_location} onChange={(v) => updateRecordForm({ record_location: v })} />
                                          <RecordField label="设备载荷" value={recordForm.record_payload} onChange={(v) => updateRecordForm({ record_payload: v })} />
                                          <RecordField label="天气" value={recordForm.record_weather} onChange={(v) => updateRecordForm({ record_weather: v })} />
                                          <RecordField label="燃油量（kg）" type="number" value={recordForm.record_fuel_amount} onChange={(v) => updateRecordForm({ record_fuel_amount: parseNumberInput(v) })} />
                                          <RecordField label="起飞重量（kg）" type="number" value={recordForm.record_takeoff_weight} onChange={(v) => updateRecordForm({ record_takeoff_weight: parseNumberInput(v) })} />
                                          <RecordField label="海拔高度（m）" type="number" value={recordForm.record_altitude} onChange={(v) => updateRecordForm({ record_altitude: parseNumberInput(v) })} />
                                          <RecordField label="风速（m/s）" type="number" value={recordForm.record_wind_speed} onChange={(v) => updateRecordForm({ record_wind_speed: parseNumberInput(v) })} />
                                          <RecordField label="备注" value={recordForm.record_note} onChange={(v) => updateRecordForm({ record_note: v })} />
                                        </div>
                                        <div className="flex items-center gap-2 justify-end">
                                          <button
                                            type="button"
                                            onClick={() => saveRecord(f.id)}
                                            disabled={savingRecord}
                                            className="px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50"
                                          >
                                            保存记录
                                          </button>
                                          <button
                                            type="button"
                                            onClick={() => setEditingRecordFlightId(null)}
                                            disabled={savingRecord}
                                            className="px-3 py-1 text-xs bg-gray-200 text-gray-600 rounded hover:bg-gray-300 disabled:opacity-50"
                                          >
                                            取消
                                          </button>
                                        </div>
                                      </div>
                                    )}
                                    {expandedRawFlightId === f.id && (
                                      <div className="mt-3 rounded border border-gray-200 bg-white p-3 space-y-2">
                                        <div className="flex items-center justify-between">
                                          <span className="text-xs font-medium text-gray-600">
                                            原始文件清单
                                          </span>
                                          <button
                                            type="button"
                                            onClick={() => createRawManifest(f.id)}
                                            className="text-xs text-blue-600 hover:text-blue-500"
                                          >
                                            生成 manifest
                                          </button>
                                        </div>
                                        {loadingRawFlightId === f.id ? (
                                          <p className="text-xs text-gray-400">加载中...</p>
                                        ) : (
                                          <>
                                            {(rawWarningsByFlight[f.id]?.length ?? f.raw_warnings?.length ?? 0) > 0 && (
                                              <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 space-y-1">
                                                {(rawWarningsByFlight[f.id] ?? f.raw_warnings ?? []).map((w, idx) => (
                                                  <div key={`${w.file ?? 'warning'}-${idx}`} className="text-xs text-amber-700">
                                                    {w.file ? `${w.file}: ` : ''}{w.error}
                                                  </div>
                                                ))}
                                              </div>
                                            )}
                                            {(rawFilesByFlight[f.id]?.length ?? 0) === 0 ? (
                                              <p className="text-xs text-gray-400">暂无已转存原始文件</p>
                                            ) : (
                                              <div className="divide-y divide-gray-100">
                                                {rawFilesByFlight[f.id].map((raw) => (
                                                  <div key={raw.id} className="py-1.5 text-xs">
                                                    <div className="flex items-center justify-between gap-3">
                                                      <span className="font-medium text-gray-700 truncate" title={raw.original_rel_path}>
                                                        {raw.original_name}
                                                      </span>
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
                        <div className="px-3 py-2 bg-gray-50 text-xs font-medium text-gray-600 flex items-center justify-between">
                          {editingGroupLabel === group.data_type_key ? (
                            <div className="flex items-center gap-1 flex-1">
                              <input
                                type="text"
                                value={editGroupLabelValue}
                                onChange={(e) => setEditGroupLabelValue(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') saveGroupLabel(group.data_type_key);
                                  if (e.key === 'Escape') setEditingGroupLabel(null);
                                }}
                                className="flex-1 bg-white border border-blue-400 rounded px-1.5 py-0.5 text-xs focus:outline-none"
                                autoFocus
                              />
                              <button onClick={() => saveGroupLabel(group.data_type_key)}
                                className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">✓</button>
                              <button onClick={() => setEditingGroupLabel(null)}
                                className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">✕</button>
                            </div>
                          ) : (
                            <>
                              <span>{group.label}</span>
                              <button
                                onClick={() => { setEditingGroupLabel(group.data_type_key); setEditGroupLabelValue(group.label); }}
                                className="text-gray-300 hover:text-blue-500 text-[10px] ml-2"
                                title="编辑组名称"
                              >
                                <Pencil className="w-3 h-3 inline" />
                              </button>
                            </>
                          )}
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

      {/* Import Modal */}
      {showImportModal && importData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowImportModal(false)}>
          <div className="bg-white rounded-lg shadow-xl p-6 w-96 max-w-[90vw]" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-700 mb-4">导入机型配置</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">来源</label>
                <div className="text-sm text-gray-800 bg-gray-50 rounded px-2 py-1">
                  {importData.model?.name}
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">新机型名称</label>
                <input
                  type="text"
                  value={importName}
                  onChange={(e) => setImportName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleImportConfirm(); }}
                  className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-blue-400"
                  autoFocus
                />
              </div>
              {importError && (
                <div className="text-xs text-red-500 bg-red-50 rounded px-2 py-1">{importError}</div>
              )}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button
                type="button"
                onClick={() => { setShowImportModal(false); setImportData(null); }}
                className="px-3 py-1.5 text-xs text-gray-500 hover:text-gray-700"
              >取消</button>
              <button
                type="button"
                onClick={handleImportConfirm}
                disabled={!importName.trim()}
                className="px-3 py-1.5 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
              >导入</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
