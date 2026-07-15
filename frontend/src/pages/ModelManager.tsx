import { useState, useEffect, useCallback } from 'react';
import { Pencil, Trash2, Download, Upload } from 'lucide-react';
import {
  listModels, updateModel, deleteModel,
  listAircraft, createAircraft, updateAircraft, deleteAircraft,
  deleteFlight, updateFlight, updateFlightRecord,
  getRawFiles, openRawFolder, browseFile,
  getModelColumns, updateModelColumn, updateModelDataTypeLabel,
  getSyncExportTree, exportSyncPackage, previewSyncImport, importSyncPackage,
  type AircraftModel, type Aircraft, type Flight,
  type DataTypeGroup, type FlightRecordFields, type RawFileItem,
  type SyncExportModelNode, type SyncExportResult,
  type SyncImportPreview, type SyncImportReport,
  type DeleteScope,
  FLIGHT_FILTER_FIELDS, type FlightFilterSpec,
} from '../api';
import { deleteActionLabel, deleteScopeFor } from '../syncStatus';
import FlightFilterBar from '../components/FlightFilterBar';

interface Props {
  onModelsChanged: () => void;
  onNavigateToFlight: (flightId: number) => void;
  flights: Flight[];
  modelsVersion: number;
  capabilities: string[];
  serverOnline?: boolean;
  isLoggedIn?: boolean;
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

function emptyRecord(): FlightRecordFields {
  return {
    record_total_duration_min: null,
    record_location: '',
    record_payload: '',
    record_weather: '',
    record_fuel_amount: null,
    record_takeoff_weight: null,
    record_altitude: null,
    record_wind_speed: null,
    record_wind_direction: '',
    record_temperature: null,
    record_note: '',
  };
}

function recordFromFlight(f: Flight): FlightRecordFields {
  return {
    record_total_duration_min: f.record_total_duration_min ?? null,
    record_location: f.record_location ?? '',
    record_payload: f.record_payload ?? '',
    record_weather: f.record_weather ?? '',
    record_fuel_amount: f.record_fuel_amount ?? null,
    record_takeoff_weight: f.record_takeoff_weight ?? null,
    record_altitude: f.record_altitude ?? null,
    record_wind_speed: f.record_wind_speed ?? null,
    record_wind_direction: f.record_wind_direction ?? '',
    record_temperature: f.record_temperature ?? null,
    record_note: f.record_note ?? '',
  };
}

function parseNumberInput(value: string): number | null {
  if (value.trim() === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatDurationMinutes(value: number | null | undefined): string {
  if (value == null) return '';
  const total = Math.max(0, Math.round(Number(value)));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return `${hours} h ${minutes} min`;
}

function formatKgValue(value: string | number | null | undefined): string {
  if (value == null || String(value).trim() === '') return '';
  const text = String(value).trim();
  return /kg$/i.test(text) ? text : `${text}kg`;
}

function recordSummary(f: Flight) {
  const parts = [
    f.record_location ? `地点 ${f.record_location}` : '',
    f.record_weather ? `天气 ${f.record_weather}` : '',
    f.record_total_duration_min != null ? `总时长 ${formatDurationMinutes(f.record_total_duration_min)}` : '',
    f.record_payload ? `载荷 ${formatKgValue(f.record_payload)}` : '',
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

function DurationField({
  value,
  onChange,
}: {
  value: number | null | undefined;
  onChange: (value: number | null) => void;
}) {
  const hasValue = value != null && Number.isFinite(Number(value));
  const total = hasValue ? Math.max(0, Math.round(Number(value))) : 0;
  const hours = hasValue ? Math.floor(total / 60) : '';
  const minutes = hasValue ? total % 60 : '';

  const update = (nextHours: string, nextMinutes: string) => {
    if (nextHours.trim() === '' && nextMinutes.trim() === '') {
      onChange(null);
      return;
    }
    const h = Math.max(0, parseNumberInput(nextHours) ?? 0);
    const m = Math.max(0, parseNumberInput(nextMinutes) ?? 0);
    onChange(Math.round(h) * 60 + Math.round(m));
  };

  return (
    <label className="space-y-1">
      <span className="block text-[10px] text-gray-500">总时长</span>
      <div className="flex items-center gap-1">
        <input
          type="number"
          min="0"
          value={hours}
          onChange={(e) => update(e.target.value, String(minutes))}
          className="min-w-0 flex-1 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
        />
        <span className="text-[10px] text-gray-500">h</span>
        <input
          type="number"
          min="0"
          max="59"
          value={minutes}
          onChange={(e) => update(String(hours), e.target.value)}
          className="min-w-0 flex-1 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
        />
        <span className="text-[10px] text-gray-500">min</span>
      </div>
    </label>
  );
}

function RecordTextarea({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <label className="space-y-1 block">
      <span className="block text-[10px] text-gray-500">{label}</span>
      <textarea
        rows={2}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className="w-full resize-none bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
      />
    </label>
  );
}

export default function ModelManager({ onModelsChanged, onNavigateToFlight, flights, modelsVersion, capabilities, serverOnline = true, isLoggedIn }: Props) {
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

  // ─── Sync package export / import ──────────────────────
  const [exportOpen, setExportOpen] = useState(false);
  const [exportFilter, setExportFilter] = useState('');
  const [exportTree, setExportTree] = useState<SyncExportModelNode[]>([]);
  const [selectedExportIds, setSelectedExportIds] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const [exportError, setExportError] = useState('');
  const [exportResult, setExportResult] = useState<SyncExportResult | null>(null);
  const [syncImportOpen, setSyncImportOpen] = useState(false);
  const [syncImportPath, setSyncImportPath] = useState('');
  const [syncImportPreview, setSyncImportPreview] = useState<SyncImportPreview | null>(null);
  const [syncModelActions, setSyncModelActions] = useState<Record<number, {
    action: 'use_existing' | 'create';
    target_model_id?: number | null;
    name?: string | null;
  }>>({});
  const [syncAircraftMappings, setSyncAircraftMappings] = useState<Record<number, {
    action: 'use_existing' | 'create';
    target_aircraft_id?: number | null;
    name?: string | null;
  }>>({});
  const [syncConflictPolicy, setSyncConflictPolicy] = useState<'skip' | 'update_records'>('skip');
  const [syncImportLoading, setSyncImportLoading] = useState(false);
  const [syncImportBrowsing, setSyncImportBrowsing] = useState(false);
  const [syncImportError, setSyncImportError] = useState('');
  const [syncImportReport, setSyncImportReport] = useState<SyncImportReport | null>(null);
  const canImportSyncPackage = !!isLoggedIn;
  const canDeleteModels = capabilities.includes('delete_models');
  const canDeleteAircraft = capabilities.includes('delete_aircraft');
  const canDeleteFlights = capabilities.includes('delete_flights');
  const canEditColumns = capabilities.includes('update_columns');

  const visibleExportFlightIds = exportTree.flatMap((model) =>
    model.aircraft.flatMap((aircraft) => aircraft.flights.map((flight) => flight.id)),
  );

  const loadExportTree = useCallback(async (q = exportFilter) => {
    setExportLoading(true);
    setExportError('');
    try {
      const data = await getSyncExportTree(q);
      setExportTree(data.tree);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportLoading(false);
    }
  }, [exportFilter]);

  useEffect(() => {
    if (!exportOpen) return;
    const timer = window.setTimeout(() => loadExportTree(exportFilter), 250);
    return () => window.clearTimeout(timer);
  }, [exportFilter, exportOpen, loadExportTree]);

  const openExportDialog = async () => {
    setExportOpen(true);
    setExportResult(null);
    setExportError('');
    await loadExportTree('');
  };

  const toggleExportFlight = (id: number) => {
    setSelectedExportIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectVisibleExportFlights = () => {
    setSelectedExportIds((prev) => {
      const next = new Set(prev);
      visibleExportFlightIds.forEach((id) => next.add(id));
      return next;
    });
  };

  const clearVisibleExportFlights = () => {
    setSelectedExportIds((prev) => {
      const next = new Set(prev);
      visibleExportFlightIds.forEach((id) => next.delete(id));
      return next;
    });
  };

  const submitExport = async () => {
    if (selectedExportIds.size === 0) return;
    setExporting(true);
    setExportError('');
    setExportResult(null);
    try {
      const result = await exportSyncPackage(Array.from(selectedExportIds));
      setExportResult(result);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const openSyncImportDialog = async () => {
    setSyncImportOpen(true);
    setSyncImportPreview(null);
    setSyncImportReport(null);
    setSyncImportError('');
    await loadModels();
  };

  const submitSyncImportPreview = async (pathArg?: string) => {
    const pkgPath = (pathArg ?? syncImportPath).trim();
    if (!pkgPath) return;
    setSyncImportLoading(true);
    setSyncImportError('');
    setSyncImportReport(null);
    try {
      const preview = await previewSyncImport(pkgPath);
      setSyncImportPreview(preview);
      const modelActions: typeof syncModelActions = {};
      preview.model_plans.forEach((plan) => {
        modelActions[plan.source_model_id] = plan.matched_model
          ? { action: 'use_existing', target_model_id: plan.matched_model.id, name: plan.create_name }
          : { action: 'create', target_model_id: null, name: plan.create_name };
      });
      setSyncModelActions(modelActions);
      const aircraftMappings: typeof syncAircraftMappings = {};
      preview.aircraft_plans.forEach((plan) => {
        aircraftMappings[plan.source_aircraft_id] = plan.matched_aircraft
          ? { action: 'use_existing', target_aircraft_id: plan.matched_aircraft.id, name: plan.create_name }
          : { action: 'create', target_aircraft_id: null, name: plan.create_name };
      });
      setSyncAircraftMappings(aircraftMappings);
      setSyncConflictPolicy('skip');
    } catch (e) {
      setSyncImportError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncImportLoading(false);
    }
  };

  const browseSyncPackage = async () => {
    setSyncImportBrowsing(true);
    try {
      const data = await browseFile({
        title: '选择同步包',
        filetypes: '同步包|*.fapkg|所有文件|*.*',
      });
      if (data.path && !data.cancelled) {
        setSyncImportPath(data.path);
        setSyncImportPreview(null);
        setSyncImportReport(null);
        setSyncImportError('');
        await submitSyncImportPreview(data.path);
      }
    } catch (e) {
      setSyncImportError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncImportBrowsing(false);
    }
  };

  const updateSyncModelAction = (sourceModelId: number, patch: Partial<typeof syncModelActions[number]>) => {
    setSyncModelActions((prev) => ({
      ...prev,
      [sourceModelId]: { ...(prev[sourceModelId] ?? { action: 'create' as const }), ...patch },
    }));
  };

  const updateSyncAircraftMapping = (sourceAircraftId: number, patch: Partial<typeof syncAircraftMappings[number]>) => {
    setSyncAircraftMappings((prev) => ({
      ...prev,
      [sourceAircraftId]: { ...(prev[sourceAircraftId] ?? { action: 'create' as const }), ...patch },
    }));
  };

  const submitSyncImport = async () => {
    if (!syncImportPreview) return;
    setSyncImportLoading(true);
    setSyncImportError('');
    setSyncImportReport(null);
    try {
      const report = await importSyncPackage({
        package_path: syncImportPreview.package_path,
        model_actions: Object.entries(syncModelActions).map(([source_model_id, action]) => ({
          source_model_id: Number(source_model_id),
          ...action,
        })),
        aircraft_mappings: Object.entries(syncAircraftMappings).map(([source_aircraft_id, mapping]) => ({
          source_aircraft_id: Number(source_aircraft_id),
          ...mapping,
        })),
        conflict_policy: syncConflictPolicy,
      });
      setSyncImportReport(report);
      await loadModels();
      await onModelsChanged();
    } catch (e) {
      setSyncImportError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncImportLoading(false);
    }
  };

  // ─── Search & Filter state ────────────────────────────
  const [modelSearch, setModelSearch] = useState('');
  const [aircraftSearch, setAircraftSearch] = useState('');
  const [timeFilterStart, setTimeFilterStart] = useState('');
  const [timeFilterEnd, setTimeFilterEnd] = useState('');
  const [flightFilter, setFlightFilter] = useState<FlightFilterSpec | null>(null);

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
    setFlightFilter(null);
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

  const handleDeleteModel = async (model: AircraftModel) => {
    try {
      await deleteModel(model.id, deleteScopeFor(model, serverOnline) as DeleteScope);
      setDeletingModelId(null);
      if (selectedModelId === model.id) setSelectedModelId(null);
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

  const handleDeleteAircraft = async (aircraftItem: Aircraft) => {
    await deleteAircraft(aircraftItem.id, deleteScopeFor(aircraftItem, serverOnline) as DeleteScope);
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

  const handleDeleteFlight = async (flight: Flight) => {
    await deleteFlight(flight.id, deleteScopeFor(flight, serverOnline) as DeleteScope);
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

  const flightMatchesFilter = (f: Flight): boolean => {
    if (!flightFilter || flightFilter.conditions.length === 0) return true;
    const results = flightFilter.conditions.map((c) => {
      const field = FLIGHT_FILTER_FIELDS.find((x) => x.key === c.field);
      if (!field) return true;
      const raw = f[c.field as keyof Flight];
      if (field.type === 'text') {
        const needle = (c.value ?? '').trim().toLowerCase();
        if (!needle) return true;
        return (raw ?? '').toString().toLowerCase().includes(needle);
      }
      // Numeric field
      const v = raw == null || raw === '' ? null : Number(raw);
      if (v == null || !Number.isFinite(v)) return false;
      if (c.op === 'between') {
        return c.min_val != null && c.max_val != null && v >= c.min_val && v <= c.max_val;
      }
      const target = c.value == null || c.value.trim() === '' ? null : Number(c.value);
      if (target == null || !Number.isFinite(target)) return true; // incomplete -> no effect
      switch (c.op) {
        case 'gt': return v > target;
        case 'gte': return v >= target;
        case 'lt': return v < target;
        case 'lte': return v <= target;
        case 'eq': return v === target;
        default: return true;
      }
    });
    return flightFilter.logic === 'and' ? results.every(Boolean) : results.some(Boolean);
  };

  const getFlightsForAircraft = (acId: number): Flight[] =>
    flights.filter((f) => f.aircraft_id === acId && flightOverlapsTimeFilter(f) && flightMatchesFilter(f));

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

  const openRawStorageFolder = async (flightId: number) => {
    try {
      const result = await openRawFolder(flightId);
      if (result.warnings?.length) {
        alert(`原始文件目录已打开，但有 ${result.warnings.length} 个路径更新警告。`);
      }
    } catch (e: any) {
      alert('打开目录失败: ' + (e.message || e));
    }
  };

  return (
    <div className="h-full flex">
      {/* Left: Model List */}
      <aside className="w-64 shrink-0 border-r border-gray-200 overflow-y-auto bg-gray-50/50 flex flex-col">
        <div className="p-3 border-b border-gray-200 flex items-center justify-between">
          <span className="text-xs font-medium text-gray-500">机型列表</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={openExportDialog}
              className="text-gray-400 hover:text-blue-500 p-0.5"
              title="导出同步包"
            >
              <Upload className="w-3.5 h-3.5" />
            </button>
            {canImportSyncPackage && (
              <button
                type="button"
                onClick={openSyncImportDialog}
                className="text-gray-400 hover:text-emerald-500 p-0.5"
                title="导入同步包"
              >
                <Download className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
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
                      {canDeleteModels && deletingModelId === m.id ? (
                        <span className="text-[10px] text-red-500 whitespace-nowrap">
                          {deleteActionLabel(m, serverOnline)}?{' '}
                          <button
                            type="button"
                            onClick={() => handleDeleteModel(m)}
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
                </div>

                {/* Collapsible record-field filter (text: contains; numeric: > ≥ < ≤ = ~) */}
                <FlightFilterBar value={flightFilter} onChange={setFlightFilter} />
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
                                      {deleteActionLabel(ac, serverOnline)}?{' '}
                                      <button type="button" onClick={() => handleDeleteAircraft(ac)} className="text-red-600 font-bold hover:text-red-700">是</button>
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
                                        {f.record_total_duration_min != null && (
                                          <span className="text-xs text-gray-500">总时长 {formatDurationMinutes(f.record_total_duration_min)}</span>
                                        )}
                                        <span className="text-xs text-gray-400">
                                          原始文件 {f.raw_file_count ?? 0}
                                        </span>
                                        <span className={`text-[10px] px-2 py-0.5 rounded border ${syncStateClass(f.sync_state)}`}>
                                          {syncStateLabel(f.sync_state)}
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
                                            {deleteActionLabel(f, serverOnline)}?{' '}
                                            <button type="button" onClick={() => handleDeleteFlight(f)} className="text-red-600 font-bold hover:text-red-700">是</button>
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
                                          <DurationField value={recordForm.record_total_duration_min} onChange={(v) => updateRecordForm({ record_total_duration_min: v })} />
                                          <RecordField label="地点" value={recordForm.record_location} onChange={(v) => updateRecordForm({ record_location: v })} />
                                          <RecordField label="天气" value={recordForm.record_weather} onChange={(v) => updateRecordForm({ record_weather: v })} />
                                          <RecordField label="设备载荷（kg）" type="number" value={recordForm.record_payload} onChange={(v) => updateRecordForm({ record_payload: v })} />
                                          <RecordField label="燃油量（kg）" type="number" value={recordForm.record_fuel_amount} onChange={(v) => updateRecordForm({ record_fuel_amount: parseNumberInput(v) })} />
                                          <RecordField label="起飞重量（kg）" type="number" value={recordForm.record_takeoff_weight} onChange={(v) => updateRecordForm({ record_takeoff_weight: parseNumberInput(v) })} />
                                          <RecordField label="海拔高度（m）" type="number" value={recordForm.record_altitude} onChange={(v) => updateRecordForm({ record_altitude: parseNumberInput(v) })} />
                                          <RecordField label="风速（m/s）" type="number" value={recordForm.record_wind_speed} onChange={(v) => updateRecordForm({ record_wind_speed: parseNumberInput(v) })} />
                                          <RecordField label="风向" value={recordForm.record_wind_direction} onChange={(v) => updateRecordForm({ record_wind_direction: v })} />
                                          <RecordField label="温度（°C）" type="number" value={recordForm.record_temperature} onChange={(v) => updateRecordForm({ record_temperature: parseNumberInput(v) })} />
                                        </div>
                                        <RecordTextarea label="备注" value={recordForm.record_note} onChange={(v) => updateRecordForm({ record_note: v })} />
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
                                            onClick={() => openRawStorageFolder(f.id)}
                                            className="text-xs text-blue-600 hover:text-blue-500"
                                          >
                                            打开目录
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
                    {canEditColumns && columnGroups.length > 0 && (
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
                              {canEditColumns && (
                                <button
                                  onClick={() => { setEditingGroupLabel(group.data_type_key); setEditGroupLabelValue(group.label); }}
                                  className="text-gray-300 hover:text-blue-500 text-[10px] ml-2"
                                  title="编辑组名称"
                                >
                                  <Pencil className="w-3 h-3 inline" />
                                </button>
                              )}
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

      {/* Sync package export modal */}
      {exportOpen && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6">
          <div className="w-full max-w-4xl max-h-[86vh] bg-white rounded-lg shadow-xl border border-gray-200 flex flex-col">
            <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between gap-4">
              <div>
                <div className="text-base font-semibold text-gray-900">导出离线同步包</div>
                <div className="text-xs text-gray-500 mt-1">
                  已选择 {selectedExportIds.size} 个架次，包将保存到固定 sync_exports 目录
                </div>
              </div>
              <button
                onClick={() => setExportOpen(false)}
                className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded"
              >
                关闭
              </button>
            </div>
            <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
              <input
                value={exportFilter}
                onChange={(e) => setExportFilter(e.target.value)}
                placeholder="筛选机型、飞机、架次、日期、地点、天气"
                className="flex-1 bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={selectVisibleExportFlights}
                disabled={visibleExportFlightIds.length === 0}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40"
              >
                全选当前结果
              </button>
              <button
                onClick={clearVisibleExportFlights}
                disabled={visibleExportFlightIds.length === 0}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40"
              >
                清除当前结果
              </button>
            </div>
            <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
              {exportLoading ? (
                <div className="text-sm text-gray-400">加载中...</div>
              ) : exportTree.length === 0 ? (
                <div className="text-sm text-gray-400">无可导出的架次</div>
              ) : (
                exportTree.map((model) => (
                  <div key={model.id} className="space-y-2">
                    <div className="text-sm font-semibold text-gray-800">{model.name}</div>
                    {model.aircraft.map((aircraft) => (
                      <div key={aircraft.id} className="ml-3 border-l border-gray-200 pl-3 space-y-2">
                        <div className="text-xs font-medium text-blue-700">{aircraft.name}</div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
                              {aircraft.flights.map((flight) => (
                                <label
                                  key={flight.id}
                                  className="flex items-center gap-2 rounded border border-gray-200 px-3 py-2 text-xs hover:bg-gray-50 cursor-pointer"
                                >
                                  <input
                                    type="checkbox"
                                    checked={selectedExportIds.has(flight.id)}
                                    onChange={() => toggleExportFlight(flight.id)}
                                  />
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
                ))
              )}
              {exportError && (
                <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-3 py-2">
                  {exportError}
                </div>
              )}
              {exportResult && (
                <div className="text-xs text-green-700 bg-green-50 border border-green-100 rounded px-3 py-2 space-y-1">
                  <div>导出完成: {exportResult.filename}</div>
                  <div className="font-mono break-all">{exportResult.path}</div>
                  <div>架次 {exportResult.flight_count}，原始文件 {exportResult.raw_file_count}</div>
                </div>
              )}
            </div>
            <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
              <button
                onClick={() => setExportOpen(false)}
                className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
              >
                取消
              </button>
              <button
                onClick={submitExport}
                disabled={exporting || selectedExportIds.size === 0}
                className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-40"
              >
                {exporting ? '导出中...' : '导出'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sync package import modal */}
      {syncImportOpen && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6">
          <div className="w-full max-w-4xl max-h-[86vh] bg-white rounded-lg shadow-xl border border-gray-200 flex flex-col">
            <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between gap-4">
              <div>
                <div className="text-base font-semibold text-gray-900">导入外场同步包</div>
                <div className="text-xs text-gray-500 mt-1">先预览包内容，再确认机型、飞机映射和重复架次策略</div>
              </div>
              <button
                onClick={() => setSyncImportOpen(false)}
                className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded"
              >
                关闭
              </button>
            </div>
            <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
              <input
                value={syncImportPath}
                onChange={(e) => setSyncImportPath(e.target.value)}
                placeholder="输入 .fapkg 同步包路径，或点击浏览选择"
                className="flex-1 bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={browseSyncPackage}
                disabled={syncImportBrowsing || syncImportLoading}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40"
              >
                {syncImportBrowsing ? '...' : '浏览'}
              </button>
              <button
                onClick={() => submitSyncImportPreview()}
                disabled={syncImportLoading || !syncImportPath.trim()}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40"
              >
                {syncImportLoading ? '处理中...' : '预览'}
              </button>
            </div>
            <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
              {syncImportError && (
                <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-3 py-2 break-all">
                  {syncImportError}
                </div>
              )}

              {syncImportPreview && (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                    <div className="rounded border border-gray-200 px-3 py-2">
                      <div className="text-gray-400">来源节点</div>
                      <div className="text-gray-800 font-medium truncate">{syncImportPreview.summary.source_node_id || '-'}</div>
                    </div>
                    <div className="rounded border border-gray-200 px-3 py-2">
                      <div className="text-gray-400">导出时间</div>
                      <div className="text-gray-800 font-medium truncate">{syncImportPreview.summary.exported_at || '-'}</div>
                    </div>
                    <div className="rounded border border-gray-200 px-3 py-2">
                      <div className="text-gray-400">范围</div>
                      <div className="text-gray-800 font-medium">
                        {syncImportPreview.summary.flight_count} 架次 / {syncImportPreview.summary.aircraft_count} 飞机
                      </div>
                    </div>
                    <div className="rounded border border-gray-200 px-3 py-2">
                      <div className="text-gray-400">导入路径</div>
                      <div className={syncImportPreview.summary.compatible ? 'text-green-700 font-medium' : 'text-amber-700 font-medium'}>
                        {syncImportPreview.summary.compatible ? 'parsed.sqlite 直接导入' : '需要原始文件重解析'}
                      </div>
                    </div>
                  </div>

                  {!syncImportPreview.summary.compatible && (
                    <div className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded px-3 py-2">
                      当前界面暂不执行不兼容包的重解析导入，请使用同 package/schema 版本导出的同步包。
                    </div>
                  )}

                  <div className="space-y-2">
                    <div className="text-sm font-semibold text-gray-800">机型处理</div>
                    {syncImportPreview.model_plans.map((plan) => {
                      const action = syncModelActions[plan.source_model_id] ?? { action: plan.default_action, name: plan.create_name };
                      return (
                        <div key={plan.source_model_id} className="rounded border border-gray-200 px-3 py-2 flex items-center gap-3 text-xs">
                          <span className="font-medium text-gray-800 w-40 truncate">{plan.source_name}</span>
                          {plan.matched_model ? (
                            <span className="text-green-700">匹配到机型：{plan.matched_model.name}</span>
                          ) : (
                            <>
                              <select
                                value={action.action}
                                onChange={(e) => updateSyncModelAction(plan.source_model_id, { action: e.target.value as 'use_existing' | 'create' })}
                                className="bg-white border border-gray-300 rounded px-2 py-1"
                              >
                                <option value="create">新建机型</option>
                                <option value="use_existing">指定已有机型</option>
                              </select>
                              {action.action === 'create' ? (
                                <input
                                  value={action.name ?? plan.create_name}
                                  onChange={(e) => updateSyncModelAction(plan.source_model_id, { name: e.target.value })}
                                  className="bg-white border border-gray-300 rounded px-2 py-1 flex-1"
                                />
                              ) : (
                                <select
                                  value={action.target_model_id ?? ''}
                                  onChange={(e) => updateSyncModelAction(plan.source_model_id, { target_model_id: e.target.value ? Number(e.target.value) : null })}
                                  className="bg-white border border-gray-300 rounded px-2 py-1 flex-1"
                                >
                                  <option value="">选择机型...</option>
                                  {models.map((model) => (
                                    <option key={model.id} value={model.id}>{model.name}</option>
                                  ))}
                                </select>
                              )}
                            </>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  <div className="space-y-2">
                    <div className="text-sm font-semibold text-gray-800">飞机映射</div>
                    {syncImportPreview.aircraft_plans.map((plan) => {
                      const mapping = syncAircraftMappings[plan.source_aircraft_id] ?? { action: plan.default_action, name: plan.create_name };
                      return (
                        <div key={plan.source_aircraft_id} className="rounded border border-gray-200 px-3 py-2 flex items-center gap-3 text-xs">
                          <span className="font-medium text-gray-800 w-40 truncate">{plan.source_name}</span>
                          {plan.matched_aircraft ? (
                            <span className="text-green-700">匹配到飞机：{plan.matched_aircraft.name}</span>
                          ) : (
                            <>
                              <select
                                value={mapping.action}
                                onChange={(e) => updateSyncAircraftMapping(plan.source_aircraft_id, { action: e.target.value as 'use_existing' | 'create' })}
                                className="bg-white border border-gray-300 rounded px-2 py-1"
                              >
                                <option value="create">新建飞机</option>
                                <option value="use_existing">指定已有飞机</option>
                              </select>
                              {mapping.action === 'create' ? (
                                <input
                                  value={mapping.name ?? plan.create_name}
                                  onChange={(e) => updateSyncAircraftMapping(plan.source_aircraft_id, { name: e.target.value })}
                                  className="bg-white border border-gray-300 rounded px-2 py-1 flex-1"
                                />
                              ) : (
                                <select
                                  value={mapping.target_aircraft_id ?? ''}
                                  onChange={(e) => updateSyncAircraftMapping(plan.source_aircraft_id, { target_aircraft_id: e.target.value ? Number(e.target.value) : null })}
                                  className="bg-white border border-gray-300 rounded px-2 py-1 flex-1"
                                >
                                  <option value="">选择飞机...</option>
                                  {plan.existing_aircraft.map((aircraft) => (
                                    <option key={aircraft.id} value={aircraft.id}>{aircraft.name}</option>
                                  ))}
                                </select>
                              )}
                            </>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  <div className="rounded border border-gray-200 px-3 py-2 text-xs space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium text-gray-800">重复架次</span>
                      <span className="text-gray-500">{syncImportPreview.duplicates.length} 个自动匹配重复项</span>
                    </div>
                    <select
                      value={syncConflictPolicy}
                      onChange={(e) => setSyncConflictPolicy(e.target.value as 'skip' | 'update_records')}
                      className="bg-white border border-gray-300 rounded px-2 py-1"
                    >
                      <option value="skip">保持现状，不更新记录字段</option>
                      <option value="update_records">更新已有架次名称和飞行记录字段</option>
                    </select>
                  </div>
                </>
              )}

              {syncImportReport && (
                <div className="text-xs text-green-700 bg-green-50 border border-green-100 rounded px-3 py-2 space-y-1">
                  <div>导入完成：{syncImportReport.status}</div>
                  <div>
                    新增 {syncImportReport.imported_flights.length}，跳过 {syncImportReport.skipped_flights.length}，
                    更新 {syncImportReport.updated_flights.length}，warning {syncImportReport.warnings.length}，
                    失败 {syncImportReport.failures.length}
                  </div>
                  <div>解析数据行：{syncImportReport.parsed_rows ?? 0}，原始文件：{syncImportReport.raw_files?.attached ?? 0}</div>
                </div>
              )}
            </div>
            <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
              <button
                onClick={() => setSyncImportOpen(false)}
                className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
              >
                取消
              </button>
              <button
                onClick={submitSyncImport}
                disabled={syncImportLoading || !syncImportPreview || !syncImportPreview.summary.compatible}
                className="px-4 py-1.5 text-sm bg-emerald-600 text-white rounded hover:bg-emerald-500 disabled:opacity-40"
              >
                {syncImportLoading ? '导入中...' : '确认导入'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
