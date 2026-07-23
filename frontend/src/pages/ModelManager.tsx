import { useState, useEffect, useCallback, useEffectEvent, useRef } from 'react';
import { listModels, updateModel, deleteModel, listAircraft, createAircraft, updateAircraft, deleteAircraft, getModelColumns, updateModelColumn, updateModelDataTypeLabel, type AircraftModel, type Aircraft, type DataTypeGroup, type DeleteScope } from '../api/models';
import { deleteFlight, updateFlight, updateFlightRecord, getRawFiles, openRawFolder, FLIGHT_FILTER_FIELDS, type Flight, type FlightRecordFields, type RawFileItem, type FlightFilterSpec } from '../api/flights';
import { matchFlightsByData, type FilterSpec } from '../api/analysis';
import { browseFile } from '../api/imports';
import { getSyncExportTree, exportSyncPackage, previewSyncImport, importSyncPackage, type SyncExportModelNode, type SyncExportResult, type SyncImportPreview, type SyncImportReport } from '../api/sync';
import { downloadRemoteFlights, getRemoteModelColumns, listRemoteAircraft, listRemoteModels, searchRemoteFlights, syncRemoteModel, type AircraftSearchSummary, type RemoteDownloadResult } from '../api/remoteData';
import { deleteScopeFor } from '../syncStatus';
import FlightFilterBar, { FilterRulesHelp } from '../components/FlightFilterBar';
import { emptyRecord, recordFromFlight } from '../features/flights/recordFields';
import AircraftList from '../features/models/AircraftList';
import ModelExportDialog from '../features/models/ModelExportDialog';
import ModelImportDialog, { type SyncAircraftMapping, type SyncModelAction } from '../features/models/ModelImportDialog';
import ColumnEditor from '../features/models/ColumnEditor';
import ModelList from '../features/models/ModelList';
import SyncProgress from '../features/sync/SyncProgress';
import { useSyncOperation } from '../features/sync/useSyncOperation';

interface Props {
  onModelsChanged: () => void;
  onNavigateToFlight: (flightId: number) => void;
  flights: Flight[];
  modelsVersion: number;
  capabilities: string[];
  serverOnline?: boolean;
  isLoggedIn?: boolean;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function ModelManager({ onModelsChanged, onNavigateToFlight, flights, modelsVersion, capabilities, serverOnline = true, isLoggedIn }: Props) {
  const [dataSource, setDataSource] = useState<'local' | 'server'>('local');
  const [models, setModels] = useState<AircraftModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [serverFlights, setServerFlights] = useState<Flight[]>([]);
  const [serverPage, setServerPage] = useState(1);
  const [serverTotal, setServerTotal] = useState(0);
  const [serverDurationSec, setServerDurationSec] = useState(0);
  const [serverAircraftSummaries, setServerAircraftSummaries] = useState<AircraftSearchSummary[]>([]);
  const [selectedServerFlightIds, setSelectedServerFlightIds] = useState<Set<number>>(new Set());
  const [serverQueryLoading, setServerQueryLoading] = useState(false);
  const [serverQueryError, setServerQueryError] = useState<string | null>(null);
  const [syncingModelId, setSyncingModelId] = useState<number | null>(null);
  const {
    busy: syncBusy,
    progress: syncProgress,
    execute: executeSyncOperation,
  } = useSyncOperation();

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
  const [syncModelActions, setSyncModelActions] = useState<Record<number, SyncModelAction>>({});
  const [syncAircraftMappings, setSyncAircraftMappings] = useState<Record<number, SyncAircraftMapping>>({});
  const [syncMetadataStrategy, setSyncMetadataStrategy] = useState<'package_wins' | 'target_wins'>('target_wins');
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
      setSyncMetadataStrategy('target_wins');
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
        metadata_strategy: syncMetadataStrategy,
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
  const [dataFilter, setDataFilter] = useState<FilterSpec | null>(null);
  const [dataMatchedFlightIds, setDataMatchedFlightIds] = useState<Set<number> | null>(null);
  const [dataFilterLoading, setDataFilterLoading] = useState(false);
  const [dataFilterError, setDataFilterError] = useState<string | null>(null);
  const dataFilterRequestRef = useRef(0);

  const loadModels = async () => {
    try {
      const data = dataSource === 'server' ? await listRemoteModels() : await listModels();
      setModels(data.models);
      setSelectedModelId((current) => (
        current != null && data.models.some((model) => model.id === current)
          ? current
          : data.models[0]?.id ?? null
      ));
    } catch (e) { console.error(e); }
  };

  const loadAircraft = async (modelId: number) => {
    try {
      const data = dataSource === 'server'
        ? await listRemoteAircraft(modelId)
        : await listAircraft(modelId);
      setAircraft(data.aircraft);
    } catch (e) { console.error(e); }
  };

  const runServerSearch = async (modelId: number, page = 1, resetFilters = false) => {
    setServerQueryLoading(true);
    setServerQueryError(null);
    try {
      const result = await searchRemoteFlights({
        model_id: modelId,
        aircraft_search: resetFilters ? '' : aircraftSearch,
        time_from: resetFilters ? undefined : (timeFilterStart || undefined),
        time_to: resetFilters ? undefined : (timeFilterEnd || undefined),
        record_filter: resetFilters ? undefined : flightFilter,
        data_filter: resetFilters ? undefined : dataFilter,
        page,
        page_size: 50,
      });
      setServerFlights(result.flights);
      setServerPage(result.page);
      setServerTotal(result.total);
      setServerDurationSec(result.summary.duration_sec);
      setServerAircraftSummaries(result.aircraft_summaries);
      setSelectedServerFlightIds(new Set());
      setExpandedAc(new Set(result.flights.map((flight) => flight.aircraft_id)));
    } catch (error: unknown) {
      setServerFlights([]);
      setServerTotal(0);
      setServerDurationSec(0);
      setServerAircraftSummaries([]);
      setServerQueryError(errorMessage(error));
    } finally {
      setServerQueryLoading(false);
    }
  };

  const loadSourceModels = useEffectEvent(() => loadModels());
  const loadSelectedModelData = useEffectEvent((modelId: number) => {
    loadAircraft(modelId);
    const columnsRequest = dataSource === 'server'
      ? getRemoteModelColumns(modelId)
      : getModelColumns(modelId);
    columnsRequest.then(d => setColumnGroups(d.data_types)).catch(() => setColumnGroups([]));
    if (dataSource === 'server') runServerSearch(modelId, 1, true);
  });

  useEffect(() => {
    setSelectedModelId(null);
    setAircraft([]);
    setColumnGroups([]);
    setServerFlights([]);
    setSelectedServerFlightIds(new Set());
    loadSourceModels();
  }, [dataSource]);

  const refreshSelectedModel = useEffectEvent(() => {
    if (selectedModelId) loadAircraft(selectedModelId);
  });

  // Refresh models/aircraft when external data changes (e.g. import on another tab)
  useEffect(() => {
    if (modelsVersion > 0 && dataSource === 'local') {
      loadSourceModels();
      refreshSelectedModel();
    }
  }, [modelsVersion, dataSource]);

  useEffect(() => {
    if (selectedModelId) {
      loadSelectedModelData(selectedModelId);
    } else {
      setAircraft([]);
      setColumnGroups([]);
    }
    // Reset search/filter/editing when switching model
    setAircraftSearch('');
    setTimeFilterStart('');
    setTimeFilterEnd('');
    setFlightFilter(null);
    setDataFilter(null);
    setDataMatchedFlightIds(null);
    setDataFilterLoading(false);
    setDataFilterError(null);
    setIsEditingColumns(false);
    setColumnEditData({});
    setShowOriginalName(true);
  }, [selectedModelId, dataSource]);

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
    } catch (error: unknown) {
      alert('保存失败: ' + errorMessage(error));
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

  const dataFilterCandidateIds = dataSource === 'local' ? flights
    .filter((flight) => flight.model_id === selectedModelId && flightOverlapsTimeFilter(flight) && flightMatchesFilter(flight))
    .map((flight) => flight.id) : [];
  const dataFilterCandidateKey = dataFilterCandidateIds.join(',');

  useEffect(() => {
    const requestId = ++dataFilterRequestRef.current;
    const candidateIds = dataFilterCandidateKey
      ? dataFilterCandidateKey.split(',').map((value) => Number(value))
      : [];
    if (dataSource === 'server' || !selectedModelId || !dataFilter) {
      const resetTimer = window.setTimeout(() => {
        setDataMatchedFlightIds(null);
        setDataFilterLoading(false);
        setDataFilterError(null);
      }, 0);
      return () => window.clearTimeout(resetTimer);
    }

    if (candidateIds.length === 0) {
      const emptyTimer = window.setTimeout(() => {
        setDataMatchedFlightIds(new Set());
        setDataFilterLoading(false);
        setDataFilterError(null);
      }, 0);
      return () => window.clearTimeout(emptyTimer);
    }

    const modelId = selectedModelId;
    const timer = window.setTimeout(() => {
      setDataFilterLoading(true);
      setDataFilterError(null);
      matchFlightsByData(modelId, candidateIds, dataFilter)
        .then((result) => {
          if (dataFilterRequestRef.current !== requestId) return;
          setDataMatchedFlightIds(new Set(result.flight_ids));
        })
        .catch((error: unknown) => {
          if (dataFilterRequestRef.current !== requestId) return;
          setDataFilterError(errorMessage(error));
        })
        .finally(() => {
          if (dataFilterRequestRef.current === requestId) setDataFilterLoading(false);
        });
    }, 250);

    return () => window.clearTimeout(timer);
  }, [selectedModelId, dataFilter, dataFilterCandidateKey, modelsVersion, dataSource]);

  const flightMatchesDataFilter = (flight: Flight): boolean =>
    !dataFilter || dataMatchedFlightIds == null || dataMatchedFlightIds.has(flight.id);

  const getFlightsForAircraft = (acId: number): Flight[] =>
    dataSource === 'server'
      ? serverFlights.filter((flight) => flight.aircraft_id === acId)
      : flights.filter((f) => f.aircraft_id === acId && flightOverlapsTimeFilter(f) && flightMatchesFilter(f) && flightMatchesDataFilter(f));

  const filteredAircraft = aircraft.filter((ac) => {
    if (dataSource === 'server' && !serverFlights.some((flight) => flight.aircraft_id === ac.id)) return false;
    if (!aircraftSearch.trim()) return true;
    const t = aircraftSearch.trim().toLowerCase();
    return ac.name.toLowerCase().includes(t);
  });

  const getAircraftStats = (acId: number) => {
    if (dataSource === 'server') {
      const summary = serverAircraftSummaries.find((item) => item.aircraft_id === acId);
      return {
        count: summary?.matched_count ?? 0,
        hours: (summary?.matched_duration_sec ?? 0) / 3600,
      };
    }
    const acFlights = getFlightsForAircraft(acId);
    const hours = acFlights.reduce((s, f) => s + (f.duration_sec ?? 0), 0) / 3600;
    return { count: acFlights.length, hours };
  };

  const globalStats = {
    totalAircraft: models.reduce((s, m) => s + (m.aircraft_count ?? 0), 0),
    totalFlights: dataSource === 'server'
      ? models.reduce((sum, model) => sum + (model.total_flights ?? 0), 0)
      : flights.length,
    totalHours: dataSource === 'server'
      ? models.reduce((sum, model) => sum + (model.total_flight_hours ?? 0), 0) / 3600
      : flights.reduce((s, f) => s + (f.duration_sec ?? 0), 0) / 3600,
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
    } catch (error: unknown) {
      setRawWarningsByFlight((prev) => ({
        ...prev,
        [flightId]: [{ error: errorMessage(error) }],
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
    } catch (error: unknown) {
      alert('打开目录失败: ' + errorMessage(error));
    }
  };

  const toggleServerFlight = (flightId: number) => {
    const flight = serverFlights.find((item) => item.id === flightId);
    if (!flight || flight.downloaded) return;
    setSelectedServerFlightIds((current) => {
      const next = new Set(current);
      if (next.has(flightId)) next.delete(flightId);
      else next.add(flightId);
      return next;
    });
  };

  const selectServerPage = () => {
    setSelectedServerFlightIds(new Set(
      serverFlights.filter((flight) => !flight.downloaded).map((flight) => flight.id),
    ));
  };

  const downloadSelectedServerFlights = async () => {
    if (selectedServerFlightIds.size === 0 || !selectedModelId || syncBusy) return;
    const remoteModel = models.find((model) => model.id === selectedModelId);
    if (!remoteModel?.model_synced) {
      alert('请先在左侧机型列表中点击“同步机型”，成功后再下载架次。');
      return;
    }
    const flightIds = Array.from(selectedServerFlightIds).filter((flightId) =>
      serverFlights.some((flight) => flight.id === flightId && !flight.downloaded),
    );
    if (flightIds.length === 0) {
      setSelectedServerFlightIds(new Set());
      return;
    }

    let result: RemoteDownloadResult | null = null;
    await executeSyncOperation(
      'pull',
      async (operationId) => {
        const downloadResult = await downloadRemoteFlights(selectedModelId, flightIds, operationId);
        result = downloadResult;
        return downloadResult;
      },
      {
        onSuccess: async () => {
          await onModelsChanged();
          await runServerSearch(selectedModelId, serverPage);
          const created = result?.report?.created?.flights ?? 0;
          const updated = result?.report?.updated?.flights ?? 0;
          const skipped = result?.report?.already_downloaded?.flights ?? 0;
          const warnings = result?.report?.warnings?.length ?? 0;
          const statusText = result?.status === 'partial' && warnings > 0
            ? `，有 ${warnings} 个文件警告`
            : '';
          const skippedText = skipped > 0 ? `，跳过本地已有 ${skipped} 个架次` : '';
          alert(`下载完成：新增 ${created} 个架次，更新 ${updated} 个架次${skippedText}${statusText}。可切换到本地数据进行分析。`);
        },
        onFailure: async () => undefined,
      },
    );
  };

  const handleSyncRemoteModel = async (model: AircraftModel) => {
    if (syncingModelId !== null) return;
    setSyncingModelId(model.id);
    try {
      const result = await syncRemoteModel(model.id);
      await loadModels();
      await onModelsChanged();
      const actionText = result.action === 'created'
        ? '已在本地创建机型'
        : result.action === 'linked'
          ? '已关联本地同结构机型'
          : '已更新本地机型定义';
      alert(`${actionText}：${model.name}`);
    } catch (error: unknown) {
      alert('同步机型失败：' + errorMessage(error));
    } finally {
      setSyncingModelId(null);
    }
  };

  const serverPageCount = Math.max(1, Math.ceil(serverTotal / 50));
  const serverDownloadablePageCount = serverFlights.filter((flight) => !flight.downloaded).length;
  const showServerDownloadProgress = dataSource === 'server'
    && !!syncProgress
    && (syncBusy === 'pull' || syncProgress.status === 'failed');

  return (
    <div className="h-full flex">
      <ModelList
        models={models}
        filteredModels={filteredModels}
        selectedModelId={selectedModelId}
        editingModelId={editingModelId}
        editModelName={editModelName}
        deletingModelId={deletingModelId}
        modelSearch={modelSearch}
        summary={globalStats}
        canDeleteModels={canDeleteModels}
        canImportSyncPackage={canImportSyncPackage}
        serverOnline={serverOnline}
        readOnly={dataSource === 'server'}
        syncable={dataSource === 'server'}
        syncingModelId={syncingModelId}
        onExport={openExportDialog}
        onImport={openSyncImportDialog}
        onSearchChange={setModelSearch}
        onSelect={setSelectedModelId}
        onStartRename={(model) => { setEditingModelId(model.id); setEditModelName(model.name); }}
        onRenameValueChange={setEditModelName}
        onRename={handleRenameModel}
        onCancelRename={() => setEditingModelId(null)}
        onRequestDelete={setDeletingModelId}
        onDelete={handleDeleteModel}
        onCancelDelete={() => setDeletingModelId(null)}
        onSyncModel={handleSyncRemoteModel}
      />

      {/* Right: Aircraft & Flights */}
      <main className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="inline-flex rounded border border-gray-300 bg-gray-100 p-0.5" aria-label="数据源">
            <button
              type="button"
              onClick={() => setDataSource('local')}
              className={`px-3 py-1 text-xs rounded ${dataSource === 'local' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
            >
              本地数据
            </button>
            <button
              type="button"
              onClick={() => setDataSource('server')}
              disabled={!serverOnline}
              title={serverOnline ? '查看服务器数据' : '服务器离线或未登录'}
              className={`px-3 py-1 text-xs rounded disabled:cursor-not-allowed disabled:opacity-40 ${dataSource === 'server' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
            >
              服务器数据
            </button>
          </div>
          {dataSource === 'server' && (
            <span className="text-xs text-gray-500">服务器数据为只读，下载后可在本地分析</span>
          )}
        </div>
        {!selectedModel ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择一个机型查看其飞机列表
          </div>
        ) : (
          <>
            {/* Model name header */}
            <div className="mb-4 flex min-h-8 flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-gray-900">{selectedModel.name}</h2>
              {dataSource === 'server' && (
                <div className="flex flex-wrap items-center justify-end gap-3 text-xs">
                  <div className="text-gray-500">
                    共 <span className="font-medium text-gray-800">{serverTotal}</span> 个架次，
                    总航时 <span className="font-medium text-gray-800">{(serverDurationSec / 3600).toFixed(1)}</span> 小时
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={selectServerPage}
                      disabled={serverDownloadablePageCount === 0 || !!syncBusy || !selectedModel.model_synced}
                      title={selectedModel.model_synced ? '选择当前页未下载的架次' : '请先在左侧同步机型'}
                      className="text-blue-600 hover:text-blue-500 disabled:text-gray-300"
                    >
                      选择当前页
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedServerFlightIds(new Set())}
                      disabled={selectedServerFlightIds.size === 0 || !!syncBusy || !selectedModel.model_synced}
                      title={selectedModel.model_synced ? '下载选中的架次到本地' : '请先在左侧同步机型'}
                      className="text-gray-500 hover:text-gray-700 disabled:text-gray-300"
                    >
                      清空选择
                    </button>
                    <button
                      type="button"
                      onClick={downloadSelectedServerFlights}
                      disabled={selectedServerFlightIds.size === 0 || !!syncBusy}
                      className="px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50"
                    >
                      {syncBusy === 'pull' ? '正在下载...' : `下载选中架次 (${selectedServerFlightIds.size})`}
                    </button>
                  </div>
                </div>
              )}
            </div>
            {showServerDownloadProgress && syncProgress && (
              <div className="mb-4">
                <SyncProgress progress={syncProgress} busy={syncBusy} />
              </div>
            )}

            {/* Left-right split: aircraft | columns (60:40) */}
            <div className="flex gap-6" style={{ height: 'calc(100% - 2.5rem)' }}>
              {/* Left: Aircraft & Flights (60%) */}
              <div className="min-w-0 overflow-y-auto" style={{ flex: '6' }}>
                {/* Add aircraft button */}
                {dataSource === 'local' && <div className="flex items-center justify-end mb-3">
                  <button
                    onClick={() => setShowAddAircraft(true)}
                    className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-500"
                  >
                    + 添加飞机
                  </button>
                </div>}

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
                  <FilterRulesHelp />
                  {isTimeFilterActive && (
                    <button
                      onClick={() => { setTimeFilterStart(''); setTimeFilterEnd(''); }}
                      className="text-xs text-blue-600 hover:text-blue-500"
                    >
                      清除时间筛选
                    </button>
                  )}
                  {dataSource === 'server' && (
                    <button
                      type="button"
                      onClick={() => selectedModelId && runServerSearch(selectedModelId, 1)}
                      disabled={serverQueryLoading}
                      className="px-3 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-500 disabled:opacity-50"
                    >
                      {serverQueryLoading ? '查询中...' : '查询'}
                    </button>
                  )}
                </div>

                {/* Collapsible record-field filter (text: contains; numeric: > ≥ < ≤ = ~) */}
                <FlightFilterBar
                  value={flightFilter}
                  onChange={setFlightFilter}
                  dataColumnGroups={columnGroups}
                  dataFilter={dataFilter}
                  onDataFilterChange={setDataFilter}
                  dataFilterLoading={dataSource === 'server' ? serverQueryLoading : dataFilterLoading}
                  dataFilterError={dataSource === 'server' ? serverQueryError : dataFilterError}
                />
                {showAddAircraft && dataSource === 'local' && (
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

                <AircraftList
                  aircraft={dataSource === 'server' ? filteredAircraft : aircraft}
                  filteredAircraft={filteredAircraft}
                  expandedAircraftIds={expandedAc}
                  editingAircraftId={editingAcId}
                  editAircraftName={editAcSerial}
                  deletingAircraftId={deletingAcId}
                  editingFlightId={editingFlightId}
                  editFlightName={editFlightName}
                  deletingFlightId={deletingFlightId}
                  editingRecordFlightId={editingRecordFlightId}
                  recordForm={recordForm}
                  savingRecord={savingRecord}
                  expandedRawFlightId={expandedRawFlightId}
                  rawFilesByFlight={rawFilesByFlight}
                  rawWarningsByFlight={rawWarningsByFlight}
                  loadingRawFlightId={loadingRawFlightId}
                  canDeleteAircraft={canDeleteAircraft}
                  canDeleteFlights={canDeleteFlights}
                  serverOnline={serverOnline}
                  readOnly={dataSource === 'server'}
                  selectable={dataSource === 'server' && !!selectedModel.model_synced}
                  selectedFlightIds={selectedServerFlightIds}
                  onSelectFlight={toggleServerFlight}
                  getFlightsForAircraft={getFlightsForAircraft}
                  getAircraftStats={getAircraftStats}
                  onToggleAircraft={toggleExpand}
                  onStartRenameAircraft={(item) => { setEditingAcId(item.id); setEditAcSerial(item.name); }}
                  onAircraftNameChange={setEditAcSerial}
                  onRenameAircraft={handleRenameAircraft}
                  onCancelRenameAircraft={() => setEditingAcId(null)}
                  onRequestDeleteAircraft={setDeletingAcId}
                  onDeleteAircraft={handleDeleteAircraft}
                  onCancelDeleteAircraft={() => setDeletingAcId(null)}
                  onStartRenameFlight={(flight) => { setEditingFlightId(flight.id); setEditFlightName(flight.name); }}
                  onFlightNameChange={setEditFlightName}
                  onRenameFlight={handleRenameFlight}
                  onCancelRenameFlight={() => setEditingFlightId(null)}
                  onEditRecord={startEditRecord}
                  onRecordChange={updateRecordForm}
                  onSaveRecord={saveRecord}
                  onCancelEditRecord={() => setEditingRecordFlightId(null)}
                  onToggleRawFiles={toggleRawFiles}
                  onOpenRawFolder={openRawStorageFolder}
                  onNavigateToFlight={onNavigateToFlight}
                  onRequestDeleteFlight={setDeletingFlightId}
                  onDeleteFlight={handleDeleteFlight}
                  onCancelDeleteFlight={() => setDeletingFlightId(null)}
                />
                {dataSource === 'server' && serverTotal > 0 && (
                  <div className="mt-4 flex items-center justify-center gap-3 text-xs text-gray-500">
                    <button
                      type="button"
                      disabled={serverPage <= 1 || serverQueryLoading}
                      onClick={() => selectedModelId && runServerSearch(selectedModelId, serverPage - 1)}
                      className="px-2 py-1 border border-gray-300 rounded disabled:opacity-40"
                    >
                      上一页
                    </button>
                    <span>第 {serverPage} / {serverPageCount} 页</span>
                    <button
                      type="button"
                      disabled={serverPage >= serverPageCount || serverQueryLoading}
                      onClick={() => selectedModelId && runServerSearch(selectedModelId, serverPage + 1)}
                      className="px-2 py-1 border border-gray-300 rounded disabled:opacity-40"
                    >
                      下一页
                    </button>
                  </div>
                )}
              </div>

              <ColumnEditor
                groups={columnGroups}
                canEdit={dataSource === 'local' && canEditColumns}
                editing={isEditingColumns}
                editData={columnEditData}
                showOriginalName={showOriginalName}
                editingGroupLabel={editingGroupLabel}
                groupLabelValue={editGroupLabelValue}
                onShowOriginalNameChange={setShowOriginalName}
                onStartBatchEdit={startBatchEditColumns}
                onSaveAll={saveAllColumns}
                onCancelBatchEdit={cancelBatchEditColumns}
                onStartGroupEdit={(dataTypeKey, label) => { setEditingGroupLabel(dataTypeKey); setEditGroupLabelValue(label); }}
                onGroupLabelValueChange={setEditGroupLabelValue}
                onSaveGroupLabel={saveGroupLabel}
                onCancelGroupEdit={() => setEditingGroupLabel(null)}
                onColumnEditField={updateColumnEditField}
              />
            </div>
          </>
        )}
      </main>

      {/* Sync package export modal */}
      {exportOpen && (
        <ModelExportDialog
          selectedIds={selectedExportIds}
          filter={exportFilter}
          onFilterChange={setExportFilter}
          visibleFlightIds={visibleExportFlightIds}
          onSelectVisible={selectVisibleExportFlights}
          onClearVisible={clearVisibleExportFlights}
          loading={exportLoading}
          tree={exportTree}
          onToggleFlight={toggleExportFlight}
          error={exportError}
          result={exportResult}
          exporting={exporting}
          onClose={() => setExportOpen(false)}
          onSubmit={submitExport}
        />
      )}
      {/* Sync package import modal */}
      {syncImportOpen && (
        <ModelImportDialog
          path={syncImportPath}
          onPathChange={setSyncImportPath}
          browsing={syncImportBrowsing}
          loading={syncImportLoading}
          error={syncImportError}
          preview={syncImportPreview}
          report={syncImportReport}
          models={models}
          modelActions={syncModelActions}
          aircraftMappings={syncAircraftMappings}
          metadataStrategy={syncMetadataStrategy}
          onBrowse={browseSyncPackage}
          onPreview={() => submitSyncImportPreview()}
          onModelActionChange={updateSyncModelAction}
          onAircraftMappingChange={updateSyncAircraftMapping}
          onMetadataStrategyChange={setSyncMetadataStrategy}
          onClose={() => setSyncImportOpen(false)}
          onSubmit={submitSyncImport}
        />
      )}
    </div>
  );
}
